"""Fargate hourly rates for the Spot price ladder (task 1.1b, owner 2026-11-18).

ON-DEMAND rates come from the AWS Price List API (authoritative). SPOT rates come from Cost
Explorer (owner choice "b"): the rate we were actually charged, cost / usage for the
``SpotUsage-Fargate-*`` usage types, per day over the last ``SPOT_WINDOW_DAYS``. The HIGHEST daily
rate is used, not the average, so a rise inside the window counts in full. AWS publishes no
Fargate Spot price API; the residual risk is a rise since the last day we ran Spot tasks (Fargate
Spot prices move gradually), and ``spot_ladder.max_fraction_of_on_demand`` can add a margin.

Rates are sanity-checked on refresh AND on load (``Rates.sane``): each Spot rate must be positive,
below on-demand and at least ``MIN_SPOT_RATIO`` of it (Fargate Spot is at most ~70% off). A
rounding-to-zero Spot rate, a wrong on-demand rate or a tampered item would otherwise let the ladder
climb past the owner's limit; an insane item is treated as no rates (never upsize).

``refresh`` runs once a day (lambda_handlers/price_refresh.py; a Cost Explorer request costs
$0.01) and stores ONE item, ``price#fargate#<region>``, in the api-cache table. ``load`` reads it
for each launch; a missing or stale item returns None and the ladder then never upsizes (the
owner's limit - never above the original size's on-demand price - cannot be checked without it).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

SPOT_WINDOW_DAYS = 7
DEFAULT_MAX_AGE_DAYS = 3
MIN_SPOT_RATIO = 0.2
MIN_DAY_USAGE = 1.0  # unit-hours; a smaller day's cost is too rounded to give a rate

_SPOT_VCPU = "SpotUsage-Fargate-vCPU-Hours:perCPU"
_SPOT_GB = "SpotUsage-Fargate-GB-Hours"
_OD_VCPU = "-Fargate-vCPU-Hours:perCPU"
_OD_GB = "-Fargate-GB-Hours"


@dataclass(frozen=True)
class Rates:
    """USD per vCPU-hour and per GB-hour, Spot and on-demand (Linux/x86)."""

    spot_vcpu: float
    spot_gb: float
    od_vcpu: float
    od_gb: float
    refreshed_at: int

    def sane(self) -> bool:
        """Every rate finite and positive; each Spot rate below on-demand and at least
        MIN_SPOT_RATIO of it."""
        import math

        values = (self.spot_vcpu, self.spot_gb, self.od_vcpu, self.od_gb)
        if not all(math.isfinite(v) and v > 0 for v in values):
            return False
        return all(
            MIN_SPOT_RATIO * od <= spot < od
            for spot, od in ((self.spot_vcpu, self.od_vcpu), (self.spot_gb, self.od_gb))
        )

    def hourly(self, cpu_units: int, memory_mb: int, spot: bool) -> float:
        """USD per hour for a task of ``cpu_units`` (1024 = 1 vCPU) and ``memory_mb``."""
        vcpu, gb = cpu_units / 1024.0, memory_mb / 1024.0
        if spot:
            return vcpu * self.spot_vcpu + gb * self.spot_gb
        return vcpu * self.od_vcpu + gb * self.od_gb


def item_key(region: str) -> str:
    return f"price#fargate#{region}"


def _ce_days(ce: Any, region: str, today: date) -> list:
    """Every ResultsByTime day of ECS cost and usage by usage type (all pages)."""
    start = today - timedelta(days=SPOT_WINDOW_DAYS)
    args: Dict[str, Any] = {
        "TimePeriod": {"Start": start.isoformat(), "End": today.isoformat()},
        "Granularity": "DAILY",
        "Metrics": ["UnblendedCost", "UsageQuantity"],
        "Filter": {
            "And": [
                {
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Elastic Container Service"],
                    }
                },
                {"Dimensions": {"Key": "REGION", "Values": [region]}},
            ]
        },
        "GroupBy": [{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
    }
    days: list = []
    while True:
        resp = ce.get_cost_and_usage(**args)
        days.extend(resp.get("ResultsByTime", []))
        token = resp.get("NextPageToken")
        if not token:
            return days
        args["NextPageToken"] = token


def _highest_daily_rate(days: list, suffix: str) -> float:
    """The highest cost/usage over the days with at least MIN_DAY_USAGE of the ``suffix`` usage
    type, or 0."""
    best = 0.0
    for day in days:
        cost = usage = 0.0
        for group in day.get("Groups", []):
            if group["Keys"][0].endswith(suffix):
                cost += float(group["Metrics"]["UnblendedCost"]["Amount"])
                usage += float(group["Metrics"]["UsageQuantity"]["Amount"])
        if usage >= MIN_DAY_USAGE:
            best = max(best, cost / usage)
    return best


def spot_rates_from_ce(ce: Any, region: str, today: date) -> Tuple[float, float]:
    """(vCPU, GB) Spot rates actually charged in ``region``: the highest daily rate over the last
    window. Raises LookupError if there was no Fargate Spot usage in the window."""
    days = _ce_days(ce, region, today)
    rates = []
    for suffix in (_SPOT_VCPU, _SPOT_GB):
        rate = _highest_daily_rate(days, suffix)
        if rate <= 0:
            raise LookupError(
                f"no Fargate Spot usage ({suffix}) in {region} in the last "
                f"{SPOT_WINDOW_DAYS} days"
            )
        rates.append(rate)
    return rates[0], rates[1]


def _on_demand_price(raw: str) -> Tuple[str, float]:
    """(matching suffix or '', USD) for one Price List product (Linux/x86 on-demand only)."""
    import json

    product = json.loads(raw)
    usage_type = product["product"]["attributes"].get("usagetype", "")
    if any(x in usage_type for x in ("Spot", "ARM", "Windows")):
        return "", 0.0
    suffix = next((x for x in (_OD_VCPU, _OD_GB) if usage_type.endswith(x)), "")
    for term in product["terms"].get("OnDemand", {}).values():
        for dim in term["priceDimensions"].values():
            return suffix, float(dim["pricePerUnit"]["USD"])
    return "", 0.0


def on_demand_rates(pricing: Any, region: str) -> Tuple[float, float]:
    """(vCPU, GB) Linux/x86 on-demand Fargate rates from the Price List API. Raises LookupError
    if either is missing."""
    found: Dict[str, float] = {}
    pages = pricing.get_paginator("get_products").paginate(
        ServiceCode="AmazonECS",
        Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}],
    )
    for page in pages:
        for raw in page.get("PriceList", []):
            suffix, price = _on_demand_price(raw)
            if suffix and found.setdefault(suffix, price) != price:
                raise LookupError(f"conflicting Fargate on-demand prices for {suffix}")
    if _OD_VCPU not in found or _OD_GB not in found:
        raise LookupError(f"Fargate on-demand rates not found for {region}")
    return found[_OD_VCPU], found[_OD_GB]


def refresh(ce: Any, pricing: Any, table: Any, region: str) -> Rates:
    """Fetch both rate sets and store them. Raises on any failure (the caller alerts); the last
    good item is left in place."""
    spot_vcpu, spot_gb = spot_rates_from_ce(ce, region, date.today())
    od_vcpu, od_gb = on_demand_rates(pricing, region)
    rates = Rates(spot_vcpu, spot_gb, od_vcpu, od_gb, int(time.time()))
    if not rates.sane():
        raise ValueError(
            f"implausible Fargate rates (Spot {spot_vcpu:.6f}/{spot_gb:.6f}, on-demand "
            f"{od_vcpu:.6f}/{od_gb:.6f}); kept the last good item"
        )
    table.put_item(
        Item={
            "cache_key": item_key(region),
            "spot_vcpu": repr(spot_vcpu),
            "spot_gb": repr(spot_gb),
            "od_vcpu": repr(od_vcpu),
            "od_gb": repr(od_gb),
            "refreshed_at": rates.refreshed_at,
            "spot_window_days": SPOT_WINDOW_DAYS,
        }
    )
    return rates


def load(
    table: Any,
    region: str,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: Optional[float] = None,
) -> Optional[Rates]:
    """The stored rates, or None if absent, unreadable or older than ``max_age_days``."""
    item = table.get_item(Key={"cache_key": item_key(region)}).get("Item")
    if not item:
        return None
    try:
        rates = Rates(
            float(item["spot_vcpu"]),
            float(item["spot_gb"]),
            float(item["od_vcpu"]),
            float(item["od_gb"]),
            int(item["refreshed_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not rates.sane():
        return None
    now = time.time() if now is None else now
    if now - rates.refreshed_at > max_age_days * 86400:
        return None
    return rates
