"""Credit-aware submission gating (M4.2, CONCURRENCY_AND_NAT_SPEC §9.0/§9.1).

An overspend on Grok is a **correctness** problem, not just a cost one: requests
submitted beyond available credit fail silently (dropped / generic errors), so the
work is not processed. This module gates batch submission on available credit,
cluster-wide, so N concurrent submitters cannot jointly overspend:

    reserve(estimated_cost) -> True  => enough credit; caller submits
                            -> False => HOLD the work in the pending queue (do
                                        NOT drop it) and alert; resume on top-up

Balance model: no confirmed Grok balance API, so we track an operator-set budget
(`cost.budget_usd`) decremented by an atomic DynamoDB running-spend counter
(`credit#spend`). Each submitter atomically adds its estimated cost BEFORE
submitting; if that would exceed the budget, it rolls back and holds.

Pricing (§9.1, verified docs.x.ai 2026-09-21) is a config-overridable table keyed
by model; batch discount is 20% for the grok-4.20/4.3 family (NOT ~50%).
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SPEND_KEY = "credit#spend"

# Verified xAI standard-context prices, USD per 1M tokens (§9.1). Config
# api.grok.pricing overrides these. Batch discount is a fraction (0.20 = 20% off).
DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    "grok-4.20-0309-reasoning": {"input": 1.25, "output": 2.50, "batch_discount": 0.20},
    "grok-4.20-0309-non-reasoning": {
        "input": 1.25,
        "output": 2.50,
        "batch_discount": 0.20,
    },
    "grok-4.3": {"input": 1.25, "output": 2.50, "batch_discount": 0.20},
    "grok-4.6": {"input": 2.00, "output": 6.00, "batch_discount": 0.0},
    "grok-4.7": {"input": 2.00, "output": 6.00, "batch_discount": 0.0},
}

_DEFAULT_MODEL = "grok-4.20-0309-reasoning"


def _pricing_for(
    model: str, config: Optional[Dict[str, Any]] = None
) -> Dict[str, float]:
    """Resolve the price row for a model, config overriding the verified defaults."""
    table = dict(DEFAULT_PRICING)
    if config:
        override = config.get("api", {}).get("grok", {}).get("pricing", {})
        for m, row in override.items():
            table[m] = {**table.get(m, {}), **row}
    return table.get(
        model,
        table.get(
            _DEFAULT_MODEL, {"input": 1.25, "output": 2.50, "batch_discount": 0.20}
        ),
    )


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    model: str = _DEFAULT_MODEL,
    is_batch: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    """Estimate USD cost for a set of requests (§9.1: tokens × price × batch discount).

    Prices are per 1M tokens. The batch discount applies only in batch mode and
    only to models that carry one (grok-4.20/4.3 family = 20%; others = 0).
    """
    row = _pricing_for(model, config)
    cost = (input_tokens / 1_000_000.0) * row.get("input", 0.0)
    cost += (output_tokens / 1_000_000.0) * row.get("output", 0.0)
    if is_batch:
        cost *= 1.0 - float(row.get("batch_discount", 0.0))
    return round(cost, 6)


def _budget_usd(config: Optional[Dict[str, Any]]) -> Optional[float]:
    """Operator budget ceiling. None => gating disabled (no budget configured)."""
    env = os.getenv("GROK_BUDGET_USD")
    if env:
        try:
            return float(env)
        except ValueError:
            return None
    if config:
        b = config.get("cost", {}).get("budget_usd")
        if b is not None:
            return float(b)
    return None


def _gating_enabled(config: Optional[Dict[str, Any]]) -> bool:
    if not config:
        return False
    return bool(config.get("cost", {}).get("credit_gating", False))


def _table():
    import boto3

    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    name = os.getenv("CACHE_TABLE", f"{os.getenv('ENV_NAME', 'dev')}-wwii-api-cache")
    return boto3.resource("dynamodb", region_name=region).Table(name)


def current_spend_usd() -> float:
    """Read the cluster-wide accrued/reserved spend counter (0 if absent)."""
    try:
        resp = _table().get_item(Key={"cache_key": SPEND_KEY})
        item = resp.get("Item")
        if item and "spend_usd" in item:
            return float(item["spend_usd"])
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Spend read failed: %s", e)
    return 0.0


def reserve(estimated_cost_usd: float, config: Optional[Dict[str, Any]] = None) -> bool:
    """Atomically reserve estimated cost against the budget before submitting.

    Returns True if the reservation fits within budget (caller may submit), False
    if it would exceed budget (caller must HOLD the work, not drop it). When
    gating is disabled or no budget is set, always returns True (no-op gate) — so
    existing serial behavior is unchanged until an operator opts in.
    """
    if not _gating_enabled(config):
        return True
    budget = _budget_usd(config)
    if budget is None:
        logger.warning("Credit gating enabled but no budget set — not gating")
        return True
    try:
        table = _table()
        # Atomic add with a condition that the new total stays within budget.
        table.update_item(
            Key={"cache_key": SPEND_KEY},
            UpdateExpression="SET spend_usd = if_not_exists(spend_usd, :z) + :c",
            ConditionExpression="if_not_exists(spend_usd, :z) + :c <= :b",
            ExpressionAttributeValues={
                ":c": Decimal(str(estimated_cost_usd)),
                ":b": Decimal(str(budget)),
                ":z": Decimal("0"),
            },
        )
        return True
    except Exception as e:
        # ConditionalCheckFailed => would exceed budget => hold. Any other error
        # is treated conservatively as "cannot confirm budget" => hold + alert.
        name = type(e).__name__
        if "ConditionalCheckFailed" in name:
            logger.error(
                "Credit gate: reservation of $%.4f would exceed budget $%.2f — HOLDING work",
                estimated_cost_usd,
                budget,
            )
        else:  # pragma: no cover - defensive
            logger.error("Credit gate reservation error (%s) — HOLDING work", name)
        return False


def release(estimated_cost_usd: float, config: Optional[Dict[str, Any]] = None) -> None:
    """Roll back a reservation (e.g. submission aborted after reserving)."""
    if not _gating_enabled(config) or _budget_usd(config) is None:
        return
    try:
        _table().update_item(
            Key={"cache_key": SPEND_KEY},
            UpdateExpression="SET spend_usd = if_not_exists(spend_usd, :z) - :c",
            ExpressionAttributeValues={
                ":c": Decimal(str(estimated_cost_usd)),
                ":z": Decimal("0"),
            },
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Credit gate release failed: %s", e)
