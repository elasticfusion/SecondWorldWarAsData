#!/usr/bin/env python3
"""Flush all search# cache entries from DynamoDB."""

import boto3

table = boto3.resource("dynamodb", region_name="us-east-1").Table("dev-wwii-api-cache")

keys_to_delete = []
params = {
    "FilterExpression": "begins_with(cache_key, :prefix)",
    "ExpressionAttributeValues": {":prefix": "search#"},
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
