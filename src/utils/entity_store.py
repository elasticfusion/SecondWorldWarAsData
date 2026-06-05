"""DynamoDB-backed entity storage for immediate durability and fast queries."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DynamoEntityStore:
    """Read/write entities to DynamoDB with immediate durability.

    Single-table design using the existing cache table.
    Key format: entity#{entity_type}#{entity_id}
    """

    def __init__(self, table_name: str = "", region: str = "us-east-1"):
        import boto3

        self._table_name = table_name or os.environ.get(
            "CACHE_TABLE", "dev-wwii-api-cache"
        )
        self._region = region
        self._table = boto3.resource("dynamodb", region_name=region).Table(
            self._table_name
        )

    def _key(self, entity_type: str, entity_id: str) -> str:
        return f"entity#{entity_type}#{entity_id}"

    def get(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get a single entity by type and ID. Returns None if not found."""
        try:
            resp = self._table.get_item(
                Key={"cache_key": self._key(entity_type, entity_id)}
            )
            item = resp.get("Item")
            if item and "data" in item:
                return json.loads(item["data"])
        except Exception as e:
            logger.warning("DynamoEntityStore.get failed: %s", e)
        return None

    def put(self, entity_type: str, entity_id: str, data: Dict[str, Any]) -> bool:
        """Write an entity. Returns True on success."""
        try:
            name = (
                data.get("name", "")
                or data.get("current_name", "")
                or data.get("group_name", "")
                or data.get("common_name", "")
                or data.get("date_start", "")
            )
            self._table.put_item(
                Item={
                    "cache_key": self._key(entity_type, entity_id),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "name": name.lower() if name else "",
                    "enrichment_status": data.get("enrichment_status", ""),
                    "book": (
                        data.get("event_mentions", [{}])[0].get("book", "")
                        if data.get("event_mentions")
                        else ""
                    ),
                    "data": json.dumps(data, ensure_ascii=False),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return True
        except Exception as e:
            logger.warning("DynamoEntityStore.put failed: %s", e)
            return False

    def delete(self, entity_type: str, entity_id: str) -> None:
        """Delete an entity."""
        try:
            self._table.delete_item(
                Key={"cache_key": self._key(entity_type, entity_id)}
            )
        except Exception as e:
            logger.warning("DynamoEntityStore.delete failed: %s", e)

    def query_unenriched(
        self, entity_type: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Query entities without enrichment_status set. Returns list of entity data dicts."""
        results: List[Dict[str, Any]] = []
        try:
            kwargs = {
                "FilterExpression": (
                    "begins_with(cache_key, :prefix) "
                    "AND (attribute_not_exists(enrichment_status) "
                    "OR enrichment_status = :empty)"
                ),
                "ExpressionAttributeValues": {
                    ":prefix": f"entity#{entity_type}#",
                    ":empty": "",
                },
                "ProjectionExpression": "#d",
                "ExpressionAttributeNames": {"#d": "data"},
            }
            while len(results) < limit:
                resp = self._table.scan(**kwargs)
                for item in resp.get("Items", []):
                    if len(results) >= limit:
                        break
                    try:
                        results.append(json.loads(item["data"]))
                    except (json.JSONDecodeError, KeyError):
                        pass
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except Exception as e:
            logger.warning("DynamoEntityStore.query_unenriched failed: %s", e)
        return results

    def query_by_name(self, entity_type: str, name: str) -> Optional[Dict[str, Any]]:
        """Find an entity by normalized name. Returns first match or None."""
        try:
            resp = self._table.scan(
                FilterExpression=("begins_with(cache_key, :prefix) AND #n = :name"),
                ExpressionAttributeValues={
                    ":prefix": f"entity#{entity_type}#",
                    ":name": name.lower(),
                },
                ExpressionAttributeNames={"#n": "name"},
                Limit=1,
            )
            items = resp.get("Items", [])
            if items and "data" in items[0]:
                return json.loads(items[0]["data"])
        except Exception as e:
            logger.warning("DynamoEntityStore.query_by_name failed: %s", e)
        return None

    def count(self, entity_type: str) -> int:
        """Count entities of a given type."""
        try:
            count = 0
            kwargs = {
                "FilterExpression": "begins_with(cache_key, :prefix)",
                "ExpressionAttributeValues": {":prefix": f"entity#{entity_type}#"},
                "Select": "COUNT",
            }
            while True:
                resp = self._table.scan(**kwargs)
                count += resp.get("Count", 0)
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            return count
        except Exception as e:
            logger.warning("DynamoEntityStore.count failed: %s", e)
            return 0


_entity_store_cache: Optional[DynamoEntityStore] = None
_entity_store_checked = False


def get_entity_store() -> Optional[DynamoEntityStore]:
    """Get DynamoDB entity store if enabled in config. Cached after first call."""
    global _entity_store_cache, _entity_store_checked
    if _entity_store_checked:
        return _entity_store_cache
    _entity_store_checked = True

    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})
    # Lambda context: check env vars (config.yaml isn't patched at runtime in Lambda)
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        table = os.environ.get("CACHE_TABLE", "")
        if table:
            _entity_store_cache = DynamoEntityStore(
                table_name=table,
                region=os.environ.get("AWS_REGION", "us-east-1"),
            )
            return _entity_store_cache
        return None

    if not aws.get("enabled"):
        return None

    backend = config.get("storage", {}).get("entity_backend", "filesystem")
    if backend != "dynamodb":
        return None

    _entity_store_cache = DynamoEntityStore(
        table_name=aws.get("cache_table", ""),
        region=aws.get("region", "us-east-1"),
    )
    return _entity_store_cache
