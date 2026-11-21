"""EC2 Spot price ladder for the GPU OCR (AWS Batch), task 1.1d, owner 2026-11-18/20.

Owner rule: finish the OCR at the lowest price available at that moment, and NEVER above the
hourly ON-DEMAND price of the ORIGINAL instance type (rung 0). Any instance type that can handle
the workload will do (owner 2026-11-20: not tied to one class; this is true of GPUs: the job needs
one GPU with enough VRAM, the vCPU and the memory its job definition asks for).

The ladder is a list of candidate instance types (CloudFormation parameters ``GpuRung0Type`` ..
``GpuRung3Type``, passed to the controller as ``OCR_GPU_LADDER``); each rung has its own Spot compute
environment and job queue (``<env>-wwii-chandra-gpu`` for rung 0, ``...-gpu-r1`` .. ``-r3``), because
AWS Batch cannot be told which instance type a job gets, only which queue it waits in. A job that is
reclaimed (``Host EC2 ... terminated``) or starved (RUNNABLE too long) on rung k is resubmitted on
the next rung whose type

  * can handle the workload (``capable``: GPU memory, vCPU, memory from ``describe_instance_types``;
    a type that cannot - e.g. a 16 GB g4dn, which runs Chandra out of memory - is never used), and
  * has a live Spot price (``describe_spot_price_history``, the highest of the AZs in use) at or
    below the original type's on-demand price (Price List API) x ``max_fraction``.

A rung over the limit is skipped, not the end of the ladder: instance families are not monotone in
price. When no rung is left, or a price cannot be read, the job goes to the ON-DEMAND queue, whose
compute environment holds the ORIGINAL type only: never above the original's on-demand price. The
rung is the QUEUE the failed job ran on, so no ladder state is stored. ``ocrctl#<job name>`` rows
(lambda_handlers/ocr_spot_controller) only make each move happen once.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_MAX_FRACTION = 1.0
OD_TTL_DAYS = 7
MAX_RUNGS = 4
_TYPE_RE = re.compile(r"^[a-z0-9]{2,12}\.[a-z0-9]{3,12}$")
# the OCR job definition (cloudformation/ocr.yaml ChandraJobDef): 4 vCPU, 15000 MiB, 1 GPU
JOB_VCPU = 4
JOB_MEMORY_MIB = 15000
JOB_GPUS = 1
# Chandra needs ~9-10 GB of GPU per page and a 16 GB card runs out of memory (cloudformation/ocr.yaml,
# g4dn); the 24 GB-class cards (A10G, L4) report ~23 GB
MIN_GPU_MIB = 22000


def parse_ladder(value: str) -> List[str]:
    """Instance types from the OCR_GPU_LADDER value ('g5.xlarge,g6.xlarge,...'), by RUNG POSITION
    (the queue ``-rN`` holds entry N, so a malformed entry becomes an empty slot, never a shift),
    trailing empty slots dropped, at most MAX_RUNGS."""
    out: List[str] = []
    for part in str(value or "").split(","):
        t = part.strip().lower()
        out.append(t if _TYPE_RE.match(t) else "")
    while out and not out[-1]:
        out.pop()
    return out[:MAX_RUNGS]


def queue_names(env: str, rungs: int = MAX_RUNGS) -> List[str]:
    """The Spot job queue of each rung: ``<env>-wwii-chandra-gpu`` (rung 0), ``...-gpu-r1`` ..."""
    base = f"{env}-wwii-chandra-gpu"
    return [base] + [f"{base}-r{k}" for k in range(1, max(1, rungs))]


def ondemand_queue(env: str) -> str:
    return f"{env}-wwii-chandra-gpu-ondemand"


def all_queues(env: str) -> List[str]:
    """Every OCR queue (the Spot rungs and the on-demand fallback)."""
    return queue_names(env) + [ondemand_queue(env)]


def rung_of(queue: str, env: str, rungs: int = MAX_RUNGS) -> Optional[int]:
    """The rung a queue name (or ARN) belongs to, or None (on-demand, or not an OCR queue)."""
    name = str(queue).rsplit("/", 1)[-1]
    names = queue_names(env, rungs)
    return names.index(name) if name in names else None


@dataclass(frozen=True)
class Shape:
    """What an instance type offers (from describe_instance_types)."""

    vcpu: int
    memory_mib: int
    gpus: int
    gpu_mib: int


def capable(shape: Optional[Shape], min_gpu_mib: int = MIN_GPU_MIB) -> bool:
    """True if the type can run one OCR job: the job's vCPU and memory, and at least one GPU
    whose own memory is ``min_gpu_mib``. An unknown shape is not capable."""
    if shape is None:
        return False
    per_gpu = shape.gpu_mib // shape.gpus if shape.gpus else 0
    return (
        shape.vcpu >= JOB_VCPU
        and shape.memory_mib >= JOB_MEMORY_MIB
        and shape.gpus >= JOB_GPUS
        and per_gpu >= min_gpu_mib
    )


def shape_of(ec2: Any, instance_type: str) -> Optional[Shape]:
    """The type's shape from ``describe_instance_types``, or None if unknown / not a GPU type."""
    info = ec2.describe_instance_types(InstanceTypes=[instance_type])["InstanceTypes"]
    if not info:
        return None
    t = info[0]
    gpus = t.get("GpuInfo", {}).get("Gpus") or []
    return Shape(
        vcpu=int(t.get("VCpuInfo", {}).get("DefaultVCpus", 0)),
        memory_mib=int(t.get("MemoryInfo", {}).get("SizeInMiB", 0)),
        gpus=sum(int(g.get("Count", 1)) for g in gpus),
        gpu_mib=int(t.get("GpuInfo", {}).get("TotalGpuMemoryInMiB", 0)),
    )


def _finite_positive(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def on_demand_hourly(pricing: Any, instance_type: str, region: str) -> Optional[float]:
    """The type's Linux on-demand USD/hour from the Price List API, or None. Several different
    prices for one filter set are refused (None): the limit must not be guessed."""
    import json

    resp = pricing.get_products(
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": f, "Value": v}
            for f, v in (
                ("instanceType", instance_type),
                ("regionCode", region),
                ("operatingSystem", "Linux"),
                ("tenancy", "Shared"),
                ("preInstalledSw", "NA"),
                ("capacitystatus", "Used"),
            )
        ],
        MaxResults=20,
    )
    prices = set()
    for raw in resp.get("PriceList", []):
        product = json.loads(raw) if isinstance(raw, str) else raw
        for term in product.get("terms", {}).get("OnDemand", {}).values():
            for dim in term.get("priceDimensions", {}).values():
                p = _finite_positive(dim.get("pricePerUnit", {}).get("USD"))
                if p:
                    prices.add(p)
    return prices.pop() if len(prices) == 1 else None


def spot_hourly(
    ec2: Any,
    instance_type: str,
    azs: Optional[Sequence[str]] = None,
    now: Optional[float] = None,
) -> Optional[float]:
    """The type's CURRENT Linux Spot USD/hour: the highest of the latest price in each AZ in use
    (Batch may launch in any of them), or None if no AZ gives a price."""
    import datetime as dt

    start = dt.datetime.fromtimestamp(
        (time.time() if now is None else now) - 3 * 3600, dt.timezone.utc
    )
    pager = ec2.get_paginator("describe_spot_price_history").paginate(
        InstanceTypes=[instance_type],
        ProductDescriptions=["Linux/UNIX"],
        StartTime=start,
    )
    latest = _latest_per_az(
        (rec for page in pager for rec in page.get("SpotPriceHistory", [])), azs
    )
    return max(p for _, p in latest.values()) if latest else None


def _latest_per_az(
    records: Any, azs: Optional[Sequence[str]]
) -> Dict[str, Tuple[Any, float]]:
    """The newest valid (timestamp, price) per AZ, only for ``azs`` when given."""
    latest: Dict[str, Tuple[Any, float]] = {}
    for rec in records:
        az = rec.get("AvailabilityZone", "")
        price = _finite_positive(rec.get("SpotPrice"))
        if price is None or (azs and az not in azs):
            continue
        if az not in latest or rec["Timestamp"] > latest[az][0]:
            latest[az] = (rec["Timestamp"], price)
    return latest


def od_item_key(region: str, instance_type: str) -> str:
    return f"price#gpu#{region}#{instance_type}"


def load_on_demand(
    table: Any,
    pricing: Any,
    instance_type: str,
    region: str,
    now: Optional[float] = None,
) -> Optional[float]:
    """The on-demand hourly price, kept in the api-cache table for OD_TTL_DAYS (it hardly
    changes; the Price List API is slow). None if it cannot be read: the caller then never
    climbs (the limit cannot be checked)."""
    now = time.time() if now is None else now
    key = {"cache_key": od_item_key(region, instance_type)}
    item = table.get_item(Key=key).get("Item")
    if item:
        price = _finite_positive(item.get("hourly"))
        if price and now - float(item.get("at", 0)) < OD_TTL_DAYS * 86400:
            return price
    price = on_demand_hourly(pricing, instance_type, region)
    if price:
        table.put_item(
            Item={
                **key,
                "hourly": repr(price),
                "at": int(now),
                "ttl": int(now) + 2 * OD_TTL_DAYS * 86400,
            }
        )
    return price


@dataclass(frozen=True)
class Move:
    """Where a job goes next: Spot rung ``rung`` (``queue``) or the on-demand queue."""

    queue: str
    rung: Optional[int]  # None = on-demand
    instance_type: str
    hourly: Optional[float]
    limit: Optional[float]
    skipped: Tuple[str, ...] = ()  # rungs passed over, with the reason

    @property
    def ondemand(self) -> bool:
        return self.rung is None

    def describe(self) -> str:
        kind = "on-demand" if self.ondemand else "Spot"
        price = f" at ${self.hourly:.4f}/h" if self.hourly is not None else ""
        cap = f" (limit ${self.limit:.4f}/h)" if self.limit is not None else ""
        return f"{self.instance_type} {kind}{price}{cap}"


def next_move(
    env: str,
    types: Sequence[str],
    current_rung: int,
    shapes: Dict[str, Optional[Shape]],
    spot: Dict[str, Optional[float]],
    od_original: Optional[float],
    max_fraction: float = DEFAULT_MAX_FRACTION,
    min_gpu_mib: int = MIN_GPU_MIB,
) -> Move:
    """The next place for a job that failed on ``current_rung``: the first later rung that is
    capable and priced at or under the limit, else the on-demand queue (original type).
    """
    limit = od_original * max_fraction if od_original else None
    skipped: List[str] = []
    for k in range(current_rung + 1, len(types)):
        t = types[k]
        price = spot.get(t)
        if not t:
            skipped.append(f"rung {k}: no valid instance type")
        elif not capable(shapes.get(t), min_gpu_mib):
            skipped.append(f"{t}: cannot run the job")
        elif limit is None:
            skipped.append(f"{t}: the original on-demand price is unknown")
        elif price is None:
            skipped.append(f"{t}: no Spot price")
        elif price > limit:
            skipped.append(f"{t}: ${price:.4f}/h is over ${limit:.4f}/h")
        else:
            return Move(
                queue_names(env, len(types))[k], k, t, price, limit, tuple(skipped)
            )
    return Move(ondemand_queue(env), None, types[0], od_original, limit, tuple(skipped))


def max_fraction(value: Any) -> float:
    """``OCR_MAX_FRACTION`` (default 1.0; capped at 1.0: the owner's limit may be tightened,
    never loosened)."""
    v = _finite_positive(value)
    return v if v and v <= 1.0 else DEFAULT_MAX_FRACTION
