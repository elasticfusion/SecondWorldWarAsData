#!/usr/bin/env python3
"""Flush bibliography-related cache entries from DynamoDB."""

import boto3

import os

ENV = os.environ.get("ENV_NAME", "dev")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
table = boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-wwii-api-cache")

prefixes = [
    "search#",
    "bibliography_nara#",
    "bibliography_verify#",
    "openserp_archive#",
]

keys_to_delete: list[str] = []
for prefix in prefixes:
    params = {
        "FilterExpression": "begins_with(cache_key, :prefix)",
        "ExpressionAttributeValues": {":prefix": prefix},
        "ProjectionExpression": "cache_key",
    }
    while True:
        resp = table.scan(**params)
        keys_to_delete.extend(item["cache_key"] for item in resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

print(f"Found {len(keys_to_delete)} cache entries to delete")
with table.batch_writer() as batch:
    for key in keys_to_delete:
        batch.delete_item(Key={"cache_key": key})
print("Done")
