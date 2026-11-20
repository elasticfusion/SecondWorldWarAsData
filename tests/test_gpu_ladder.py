"""Tests for src/utils/gpu_ladder (task 1.1d): the Spot price ladder for the GPU OCR."""

import datetime as dt
import json
from unittest.mock import MagicMock

import pytest

from src.utils import gpu_ladder as gl

G5 = gl.Shape(vcpu=4, memory_mib=16384, gpus=1, gpu_mib=23028)
G6 = gl.Shape(vcpu=4, memory_mib=16384, gpus=1, gpu_mib=23034)
G4DN = gl.Shape(vcpu=4, memory_mib=16384, gpus=1, gpu_mib=15360)
TYPES = ["g5.xlarge", "g6.xlarge", "g5.2xlarge", "g6.2xlarge"]
SHAPES = {"g6.xlarge": G6, "g5.2xlarge": G5, "g6.2xlarge": G6}


def test_parse_ladder_keeps_rung_positions():
    assert gl.parse_ladder(" G5.xlarge,g6.xlarge,g5.2xlarge") == [
        "g5.xlarge",
        "g6.xlarge",
        "g5.2xlarge",
    ]
    # a bad entry is an empty slot: the types after it keep their queue (-r2 stays g5.2xlarge)
    assert gl.parse_ladder("g5.xlarge,;rm,g5.2xlarge,,") == [
        "g5.xlarge",
        "",
        "g5.2xlarge",
    ]
    assert gl.parse_ladder("") == []
    assert len(gl.parse_ladder(",".join(f"g{n}.xlarge" for n in range(10)))) == 4


def test_next_move_skips_an_empty_rung_slot():
    m = gl.next_move(
        "dev", ["g5.xlarge", "", "g5.2xlarge"], 0, SHAPES, {"g5.2xlarge": 0.3}, 1.0
    )
    assert m.rung == 2 and m.queue == "dev-wwii-chandra-gpu-r2"
    assert "rung 1: no valid instance type" in m.skipped


def test_queue_names_rung_zero_keeps_its_name():
    assert gl.queue_names("dev") == [
        "dev-wwii-chandra-gpu",
        "dev-wwii-chandra-gpu-r1",
        "dev-wwii-chandra-gpu-r2",
        "dev-wwii-chandra-gpu-r3",
    ]
    assert gl.all_queues("dev")[-1] == "dev-wwii-chandra-gpu-ondemand"


def test_rung_of_accepts_name_or_arn():
    assert gl.rung_of("dev-wwii-chandra-gpu", "dev") == 0
    arn = "arn:aws:batch:us-east-1:1:job-queue/dev-wwii-chandra-gpu-r2"
    assert gl.rung_of(arn, "dev") == 2
    assert gl.rung_of("dev-wwii-chandra-gpu-ondemand", "dev") is None
    assert gl.rung_of("other-queue", "dev") is None


def test_capable_needs_gpu_memory_vcpu_and_memory():
    assert gl.capable(G5)
    assert not gl.capable(G4DN)  # 16 GB card: Chandra runs out of memory
    assert not gl.capable(None)
    assert not gl.capable(gl.Shape(2, 16384, 1, 23028))
    assert not gl.capable(gl.Shape(4, 8192, 1, 23028))
    assert not gl.capable(gl.Shape(4, 16384, 0, 0))
    # four small GPUs are not one big one: the per-GPU memory counts
    assert not gl.capable(gl.Shape(48, 192000, 4, 4 * 15360))
    assert gl.capable(gl.Shape(48, 192000, 4, 4 * 23028))


def test_shape_of_reads_describe_instance_types():
    ec2 = MagicMock()
    ec2.describe_instance_types.return_value = {
        "InstanceTypes": [
            {
                "VCpuInfo": {"DefaultVCpus": 4},
                "MemoryInfo": {"SizeInMiB": 16384},
                "GpuInfo": {"Gpus": [{"Count": 1}], "TotalGpuMemoryInMiB": 23028},
            }
        ]
    }
    assert gl.shape_of(ec2, "g5.xlarge") == G5
    ec2.describe_instance_types.return_value = {"InstanceTypes": []}
    assert gl.shape_of(ec2, "x") is None
    ec2.describe_instance_types.return_value = {
        "InstanceTypes": [{"VCpuInfo": {"DefaultVCpus": 4}}]
    }
    assert not gl.capable(gl.shape_of(ec2, "m5.xlarge"))  # no GPU


def _move(spot, od=1.0, current=0, shapes=SHAPES, frac=1.0):
    return gl.next_move("dev", TYPES, current, shapes, spot, od, frac)


def test_next_move_takes_first_rung_at_or_under_the_cap():
    m = _move({"g6.xlarge": 0.4, "g5.2xlarge": 0.3})
    assert (m.queue, m.rung, m.instance_type) == (
        "dev-wwii-chandra-gpu-r1",
        1,
        "g6.xlarge",
    )
    assert not m.ondemand and m.limit == 1.0


def test_next_move_price_equal_to_the_cap_is_allowed():
    assert _move({"g6.xlarge": 1.0}).rung == 1


def test_next_move_skips_over_limit_rung_and_keeps_climbing():
    m = _move({"g6.xlarge": 1.5, "g5.2xlarge": 0.9})
    assert m.rung == 2
    assert "over" in m.skipped[0] and "g6.xlarge" in m.skipped[0]


def test_next_move_skips_incapable_and_unpriced_rungs():
    shapes = {**SHAPES, "g6.xlarge": G4DN}
    m = _move({"g6.xlarge": 0.1, "g5.2xlarge": None, "g6.2xlarge": 0.5}, shapes=shapes)
    assert m.rung == 3
    assert "cannot run the job" in m.skipped[0]
    assert "no Spot price" in m.skipped[1]


def test_next_move_goes_on_demand_at_the_original_type_when_nothing_fits():
    m = _move({t: 9.0 for t in TYPES})
    assert m.ondemand and m.queue == "dev-wwii-chandra-gpu-ondemand"
    assert m.instance_type == "g5.xlarge" and m.hourly == 1.0
    assert len(m.skipped) == 3


def test_next_move_unknown_original_price_never_climbs():
    m = _move({t: 0.01 for t in TYPES}, od=None)
    assert m.ondemand and m.limit is None


def test_next_move_only_looks_at_later_rungs():
    assert _move({t: 0.1 for t in TYPES}, current=2).rung == 3
    assert _move({t: 0.1 for t in TYPES}, current=3).ondemand


def test_next_move_fraction_tightens_the_limit():
    assert _move({"g6.xlarge": 0.6, "g5.2xlarge": 0.4}, frac=0.5).rung == 2


def test_max_fraction_can_tighten_but_never_loosen():
    assert gl.max_fraction("0.5") == 0.5
    for bad in ("1.5", "0", "-1", "nan", "inf", "x", None):
        assert gl.max_fraction(bad) == 1.0


def _od_product(*prices):
    return [
        json.dumps(
            {
                "terms": {
                    "OnDemand": {
                        "t": {"priceDimensions": {"d": {"pricePerUnit": {"USD": p}}}}
                    }
                }
            }
        )
        for p in prices
    ]


def test_on_demand_hourly_parses_the_price_list():
    pricing = MagicMock()
    pricing.get_products.return_value = {"PriceList": _od_product("1.0060000000")}
    assert gl.on_demand_hourly(pricing, "g5.xlarge", "us-east-1") == pytest.approx(
        1.006
    )
    fields = {
        f["Field"]: f["Value"] for f in pricing.get_products.call_args.kwargs["Filters"]
    }
    assert (
        fields["instanceType"] == "g5.xlarge" and fields["operatingSystem"] == "Linux"
    )


def test_on_demand_hourly_refuses_ambiguous_or_unreadable_prices():
    pricing = MagicMock()
    pricing.get_products.return_value = {"PriceList": _od_product("1.0", "2.0")}
    assert gl.on_demand_hourly(pricing, "g5.xlarge", "us-east-1") is None
    pricing.get_products.return_value = {"PriceList": _od_product("0.0000000000")}
    assert gl.on_demand_hourly(pricing, "g5.xlarge", "us-east-1") is None
    pricing.get_products.return_value = {"PriceList": []}
    assert gl.on_demand_hourly(pricing, "g5.xlarge", "us-east-1") is None


def _hist(*recs):
    ec2 = MagicMock()
    ec2.get_paginator.return_value.paginate.return_value = [
        {"SpotPriceHistory": list(recs)}
    ]
    return ec2


def _rec(az, price, minute):
    return {
        "AvailabilityZone": az,
        "SpotPrice": price,
        "Timestamp": dt.datetime(2026, 1, 1, 0, minute, tzinfo=dt.timezone.utc),
    }


def test_spot_hourly_is_the_highest_latest_price_over_the_azs():
    ec2 = _hist(
        _rec("a", "0.30", 1),
        _rec("a", "0.50", 9),  # latest in a
        _rec("b", "0.40", 5),
        _rec("c", "9.00", 5),  # an AZ the subnets cannot use
    )
    assert gl.spot_hourly(ec2, "g6.xlarge", ["a", "b"]) == 0.5
    assert gl.spot_hourly(ec2, "g6.xlarge") == 9.0


def test_spot_hourly_none_when_no_usable_price():
    assert gl.spot_hourly(_hist(), "g6.xlarge", ["a"]) is None
    assert gl.spot_hourly(_hist(_rec("a", "nan", 1)), "g6.xlarge", ["a"]) is None
    assert gl.spot_hourly(_hist(_rec("z", "0.2", 1)), "g6.xlarge", ["a"]) is None


def test_load_on_demand_caches_in_the_table():
    table = MagicMock()
    table.get_item.return_value = {}
    pricing = MagicMock()
    pricing.get_products.return_value = {"PriceList": _od_product("1.0")}
    assert gl.load_on_demand(table, pricing, "g5.xlarge", "us-east-1", now=1000) == 1.0
    item = table.put_item.call_args.kwargs["Item"]
    assert (
        item["cache_key"] == "price#gpu#us-east-1#g5.xlarge" and item["hourly"] == "1.0"
    )
    # a fresh cached row is used without calling the Price List API
    table.get_item.return_value = {"Item": {"hourly": "1.0", "at": 1000}}
    pricing.get_products.reset_mock()
    assert gl.load_on_demand(table, pricing, "g5.xlarge", "us-east-1", now=2000) == 1.0
    pricing.get_products.assert_not_called()
    # an expired row is refreshed
    far = 1000 + 8 * 86400
    assert gl.load_on_demand(table, pricing, "g5.xlarge", "us-east-1", now=far) == 1.0
    pricing.get_products.assert_called_once()


def test_load_on_demand_unreadable_price_is_not_cached():
    table = MagicMock()
    table.get_item.return_value = {}
    pricing = MagicMock()
    pricing.get_products.return_value = {"PriceList": []}
    assert gl.load_on_demand(table, pricing, "g5.xlarge", "us-east-1") is None
    table.put_item.assert_not_called()


def test_move_describe_names_type_price_and_limit():
    m = _move({"g6.xlarge": 0.4})
    assert m.describe() == "g6.xlarge Spot at $0.4000/h (limit $1.0000/h)"
    assert "on-demand" in _move({}).describe()
