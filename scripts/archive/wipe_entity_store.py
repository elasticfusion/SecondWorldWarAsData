#!/usr/bin/env python3
"""Wipe all entity# entries from DynamoDB entity store."""

import boto3

import os

ENV = os.environ.get("ENV_NAME", "dev")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
table = boto3.resource("dynamodb", region_name=REGION).Table(f"{ENV}-wwii-api-cache")

keys_to_delete: list[str] = []
params = {
    "FilterExpression": "begins_with(cache_key, :prefix)",
    "ExpressionAttributeValues": {":prefix": "entity#"},
    "ProjectionExpression": "cache_key",
}
while True:
    resp = table.scan(**params)
    keys_to_delete.extend(item["cache_key"] for item in resp.get("Items", []))
    if "LastEvaluatedKey" not in resp:
        break
    params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

print(f"Deleting {len(keys_to_delete)} entity store entries")
with table.batch_writer() as batch:
    for key in keys_to_delete:
        batch.delete_item(Key={"cache_key": key})
print("Done")
