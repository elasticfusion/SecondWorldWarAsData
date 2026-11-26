"""Read the live xAI prepaid credit balance for the trigger Lambda (task 1.1e, owner 2026-11-18).

The only Lambda allowed the xAI management key (its own role; every other Lambda role is denied
it, cloudformation/iam.yaml). Outside the VPC, so it needs no NAT. Invoked synchronously by
src/utils/credit_hold when its cached ``credit#balance`` is stale; stores the balance there and
returns ``{"usd": <float>}``, or ``{"error": <cause>, "reason": <text>}`` when it cannot be read
(never a guessed number). No Grok tokens are spent.
"""

import json
import logging
import os
import time
from typing import Any, Dict

import boto3

from src.utils import grok_balance
from src.utils.aws_clients import get_dynamo_table
from src.utils.credit_hold import BALANCE_KEY, CACHE_SECONDS

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
CACHE_TABLE = os.getenv("CACHE_TABLE", "dev-wwii-api-cache")
ENV_NAME = os.getenv("ENV_NAME", "dev")

_keys: Dict[str, str] = {}  # secret id -> value, kept for a warm container


def _secret(sm: Any, secret_id: str) -> str:
    if secret_id not in _keys:
        _keys[secret_id] = sm.get_secret_value(SecretId=secret_id)[
            "SecretString"
        ].strip()
    return _keys[secret_id]


def handler(_event=None, _context=None, sm=None, table=None, session=None):
    """Read, store and return the balance. Clients are injectable for tests."""
    sm = sm or boto3.client("secretsmanager", region_name=REGION)
    try:
        mgmt = _secret(
            sm,
            os.getenv(
                "XAI_MANAGEMENT_SECRET_ID",
                f"{ENV_NAME}-wwii-pipeline/xai-management-key",
            ),
        )
        api = _secret(
            sm, os.getenv("SECRETS_ID", f"{ENV_NAME}-wwii-pipeline/grok-api-key")
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - reported to the caller, which lets the task decide
        logger.error(
            "OBSERV credit.check_failed reason=secret_%s status=error - the xAI keys could not "
            "be read; check the xai-management-key and grok-api-key secrets and this "
            "Lambda's role",
            type(e).__name__,
        )
        return {"error": "config", "reason": f"secret: {type(e).__name__}"}
    try:
        usd = grok_balance.balance_usd(mgmt, api, session)
    except grok_balance.BalanceUnreadable as e:
        if e.cause == "config":
            _keys.clear()  # a rotated key: read the secrets again on the next call
        logger.warning(
            "OBSERV credit.check_unreadable cause=%s reason=%s status=warn",
            e.cause,
            e.reason,
        )
        return {"error": e.cause, "reason": e.reason}
    try:
        table = table or get_dynamo_table(CACHE_TABLE, region=REGION)
        now = time.time()
        table.put_item(
            Item={
                "cache_key": BALANCE_KEY,
                "response": json.dumps({"usd": usd, "at": now}),
                "ttl": int(now + CACHE_SECONDS * 4),
            }
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - the answer is still good; only the cache missed
        logger.warning(
            "OBSERV credit.cache_write_failed err=%s status=warn - each launch now asks again; "
            "check the credit-check role's dynamodb:PutItem on credit#balance",
            type(e).__name__,
        )
    logger.info("OBSERV credit.check usd=%.2f status=ok", usd)
    return {"usd": usd}
