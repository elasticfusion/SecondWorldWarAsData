"""Daily Fargate price refresh for the Spot price ladder (task 1.1b, owner 2026-11-18).

Reads the Fargate Spot rates actually charged (Cost Explorer, $0.01 a call, once a day) and the
on-demand rates (Price List API), and stores them as ``price#fargate#<region>`` in the api-cache
table (src/utils/fargate_prices). The trigger Lambda reads that item at every launch; without a
fresh one it never upsizes a Spot task (src/utils/spot_ladder).

A failure keeps the last good item and alerts (phase2-complete -> email + Slack) with what to do.
No Fargate Spot usage in the window is reported the same way: the ladder cannot check the owner's
limit without a Spot rate, so a reclaimed task goes straight to on-demand at its original size.
"""

import logging
import os
from typing import Any

import boto3

from src.utils import fargate_prices
from src.utils.observe import observe, observe_error

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
CACHE_TABLE = os.getenv("CACHE_TABLE", "dev-wwii-api-cache")


def _table() -> Any:
    ddb: Any = boto3.resource("dynamodb", region_name=REGION)
    return ddb.Table(CACHE_TABLE)


def handler(_event=None, _context=None, ce=None, pricing=None, table=None):
    """Refresh the stored rates. Clients are injectable for tests."""
    # Cost Explorer and the Price List API are served from us-east-1 only
    ce = ce or boto3.client("ce", region_name="us-east-1")
    pricing = pricing or boto3.client("pricing", region_name="us-east-1")
    table = table or _table()
    try:
        rates = fargate_prices.refresh(ce, pricing, table, REGION)
    except Exception as e:  # noqa: BLE001 - alert, keep the last good item
        observe_error(
            "spot",
            "price_refresh_failed",
            note=(
                "Fargate prices were not refreshed, so after 3 days the Spot ladder stops "
                "upsizing (a reclaimed task relaunches on-demand at its original size). Check "
                "this Lambda's logs; LookupError means no Fargate Spot usage in the last "
                f"{fargate_prices.SPOT_WINDOW_DAYS} days, which clears once Spot tasks run"
            ),
            region=REGION,
            error=type(e).__name__,
        )
        return {"status": "failed", "error": type(e).__name__}
    observe(
        "spot",
        "price_refresh",
        status="ok",
        region=REGION,
        spot_vcpu_h=round(rates.spot_vcpu, 6),
        spot_gb_h=round(rates.spot_gb, 6),
        od_vcpu_h=round(rates.od_vcpu, 6),
        od_gb_h=round(rates.od_gb, 6),
    )
    return {"status": "ok"}
