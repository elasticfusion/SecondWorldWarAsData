"""Shared AWS client/handle constructors — the single source of truth for region + table-name
resolution, so a DynamoDB table handle is built the SAME way everywhere.

Before this module, ~20 call sites each hand-rolled
``boto3.resource("dynamodb", region_name=<region>).Table(<name>)`` with the region resolved two
divergent ways (env-only ``os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))``
vs a config-read), a drift that risks a handle pointing at the wrong region/table. Region now
resolves through the single source of truth ``config.get_aws_region()``; the cache-table name
resolves through ``cache_table_name()``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.utils.config import get_aws_region


def cache_table_name() -> str:
    """The shared API-cache DynamoDB table name: ``CACHE_TABLE`` env, else
    ``{ENV_NAME|dev}-wwii-api-cache``. Single source of truth for the default name."""
    return os.getenv("CACHE_TABLE", f"{os.getenv('ENV_NAME', 'dev')}-wwii-api-cache")


def get_dynamo_table(
    name: Optional[str] = None, *, region: Optional[str] = None
) -> Any:
    """Return a DynamoDB ``Table`` resource handle.

    ``region`` defaults to :func:`config.get_aws_region` (the single source of truth: ``AWS_REGION``
    → ``AWS_DEFAULT_REGION`` → ``us-east-1``). ``name`` defaults to :func:`cache_table_name`. boto3
    is imported lazily so importing this module stays cheap for non-AWS code paths.
    """
    import boto3

    return boto3.resource("dynamodb", region_name=region or get_aws_region()).Table(
        name or cache_table_name()
    )
