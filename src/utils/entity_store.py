"""DynamoDB-backed entity storage for immediate durability and fast queries."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.utils.config import get_aws_region

logger = logging.getLogger(__name__)


def _fuzzy_index_match(norm_title: str, index: Dict[str, str]) -> Optional[str]:
    """Match a normalized title against the bibliography title index (exact then
    0.85 fuzzy), returning the mapped BibliographyID or None. Mirrors the
    file-based bibliography._find_match so Dynamo-backed dedup behaves identically."""
    from difflib import SequenceMatcher

    if norm_title in index:
        return index[norm_title]
    for existing_title, bib_id in index.items():
        if SequenceMatcher(None, norm_title, existing_title).ratio() >= 0.85:
            return bib_id
    return None


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

    def put(
        self, entity_type: str, entity_id: str, data: Dict[str, Any], filename: str = ""
    ) -> bool:
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
                    "filename": filename,
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

    @staticmethod
    def _mention_key(mention: Dict[str, Any]) -> Any:
        """Dedup key for an event mention: (Sub_eventID, book) — matches dedup/merge."""
        return (mention.get("Sub_eventID"), mention.get("book"))

    def _merge_mentions(
        self, existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Append incoming mentions not already present (idempotent, §3.3)."""
        seen = {self._mention_key(m) for m in existing if m.get("Sub_eventID")}
        merged = list(existing)
        for m in incoming:
            key = self._mention_key(m)
            # A mention with no Sub_eventID can't be deduped — keep it (rare).
            if not m.get("Sub_eventID") or key not in seen:
                merged.append(m)
                if m.get("Sub_eventID"):
                    seen.add(key)
        return merged

    def merge_entity(
        self,
        entity_type: str,
        entity_id: str,
        data: Dict[str, Any],
        *,
        filename: str = "",
        max_retries: int = 5,
    ) -> bool:
        """Idempotently merge an entity's event_mentions into the stored record.

        The correctness crux under concurrency (§3.3): two books extracting the
        same entity (e.g. "Eisenhower") must NOT clobber each other's mentions.
        Uses optimistic concurrency — read current + `_version`, merge mentions
        (deduped on Sub_eventID+book), then a version-conditional put; on a
        conflict (another writer won the race) re-read and retry. Idempotent: a
        relaunched/retried task re-merging the same mentions is a no-op.

        Falls back to a plain put() when the entity doesn't exist yet.
        """
        key = self._key(entity_type, entity_id)
        for attempt in range(max_retries):
            try:
                resp = self._table.get_item(Key={"cache_key": key})
                item = resp.get("Item")
                if not item or "data" not in item:
                    # First writer — create with version 1 (conditional so a racing
                    # creator doesn't get silently overwritten).
                    return self._conditional_create(
                        entity_type, entity_id, data, filename
                    )
                current = json.loads(item["data"])
                version = int(item.get("_version", 0))
                merged = dict(current)
                merged["event_mentions"] = self._merge_mentions(
                    current.get("event_mentions", []),
                    data.get("event_mentions", []),
                )
                if merged.get("event_mentions") == current.get("event_mentions"):
                    return True  # nothing new to append — idempotent no-op
                self._table.put_item(
                    Item=self._item(
                        entity_type, entity_id, merged, filename, version + 1
                    ),
                    ConditionExpression="#v = :cur",
                    ExpressionAttributeNames={"#v": "_version"},
                    ExpressionAttributeValues={":cur": version},
                )
                return True
            except self._table.meta.client.exceptions.ConditionalCheckFailedException:
                logger.info(
                    "merge_entity conflict on %s (attempt %d) — retrying",
                    key,
                    attempt + 1,
                )
                continue
            except Exception as e:
                logger.warning("DynamoEntityStore.merge_entity failed: %s", e)
                return False
        logger.error("merge_entity exhausted retries for %s — mentions NOT merged", key)
        return False

    def _conditional_create(
        self, entity_type: str, entity_id: str, data: Dict[str, Any], filename: str
    ) -> bool:
        """Create an entity only if absent (version 1). Retry-safe first write."""
        try:
            self._table.put_item(
                Item=self._item(entity_type, entity_id, data, filename, 1),
                ConditionExpression="attribute_not_exists(cache_key)",
            )
            return True
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            # Lost the create race — someone else created it; merge into theirs.
            return self.merge_entity(entity_type, entity_id, data, filename=filename)
        except Exception as e:
            logger.warning("DynamoEntityStore._conditional_create failed: %s", e)
            return False

    def _item(
        self,
        entity_type: str,
        entity_id: str,
        data: Dict[str, Any],
        filename: str,
        version: int,
    ) -> Dict[str, Any]:
        """Build the DynamoDB item for an entity (shared by put/merge)."""
        name = (
            data.get("name", "")
            or data.get("current_name", "")
            or data.get("group_name", "")
            or data.get("common_name", "")
            or data.get("date_start", "")
        )
        return {
            "cache_key": self._key(entity_type, entity_id),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "name": name.lower() if name else "",
            "filename": filename,
            "enrichment_status": data.get("enrichment_status", ""),
            "book": (
                data.get("event_mentions", [{}])[0].get("book", "")
                if data.get("event_mentions")
                else ""
            ),
            "data": json.dumps(data, ensure_ascii=False),
            "_version": version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

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

    def list_all(self, entity_type: str) -> List[Dict[str, Any]]:
        """Return all entities of a given type. Each dict has 'data' and 'filename' keys."""
        results: List[Dict[str, Any]] = []
        try:
            kwargs = {
                "FilterExpression": "begins_with(cache_key, :prefix)",
                "ExpressionAttributeValues": {":prefix": f"entity#{entity_type}#"},
                "ProjectionExpression": "#d, filename",
                "ExpressionAttributeNames": {"#d": "data"},
            }
            while True:
                resp = self._table.scan(**kwargs)
                for item in resp.get("Items", []):
                    try:
                        results.append(
                            {
                                "data": json.loads(item["data"]),
                                "filename": item.get("filename", ""),
                            }
                        )
                    except (json.JSONDecodeError, KeyError):
                        pass
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except Exception as e:
            logger.warning("DynamoEntityStore.list_all failed: %s", e)
        return results

    # --- Bibliography (G3): Dynamo-backed title-dedup + entry merge ---
    # Bibliography dedups cross-book by TITLE (not event_mentions), so it needs a
    # normalized-title -> BibliographyID index. Stored as ONE versioned item
    # (bibindex#all) so the fuzzy-match logic (iterate titles) is preserved while
    # both index and entries are Dynamo-backed and race-safe across Fargate hosts
    # (the old flock-guarded S3 JSON writes were per-host, NOT concurrency-safe).

    _BIB_INDEX_KEY = "bibindex#all"

    def get_bibliography_index(self) -> Dict[str, str]:
        """Return the normalized-title -> BibliographyID map (empty if none)."""
        try:
            resp = self._table.get_item(Key={"cache_key": self._BIB_INDEX_KEY})
            item = resp.get("Item")
            if item and "data" in item:
                return json.loads(item["data"])
        except Exception as e:
            logger.warning("get_bibliography_index failed: %s", e)
        return {}

    def store_bibliography(
        self,
        norm_title: str,
        bib_id: str,
        entry_builder,
        mention: Dict[str, Any],
        mention_exists,
        max_retries: int = 8,
    ) -> Optional[str]:
        """Atomically dedup-by-title + append a mention, race-safe across hosts.

        norm_title: normalized title for the index lookup/insert.
        bib_id: caller-proposed new BibliographyID (used only if no match exists).
        entry_builder: () -> dict, builds a fresh entry (called only when creating).
        mention: the mention dict to append.
        mention_exists: (mentions_list) -> bool, caller's dedup predicate.

        Returns the resulting BibliographyID, or None on error. The title index
        (bibindex#all) is updated with an optimistic version-conditional put; the
        entry (entity#bibliography#{id}) likewise — so concurrent books adding
        references never clobber each other.
        """
        for attempt in range(max_retries):
            try:
                resp = self._table.get_item(Key={"cache_key": self._BIB_INDEX_KEY})
                item = resp.get("Item")
                index: Dict[str, str] = (
                    json.loads(item["data"]) if item and "data" in item else {}
                )
                version = int(item["_version"]) if item and "_version" in item else 0
                existing_id = _fuzzy_index_match(norm_title, index)
                if existing_id:
                    # Match: merge the mention into the existing entry (its own
                    # version-conditional loop). Index unchanged.
                    self._append_bib_mention(existing_id, mention, mention_exists)
                    return existing_id
                # No match: create entry + insert into the index atomically.
                entry = entry_builder()
                new_id = entry.get("BibliographyID", bib_id)
                entry.setdefault("mentions", []).append(mention)
                # Create WITH _version=1 so later _append_bib_mention (which does a
                # version-conditional put) works — a plain put() omits _version and
                # would make every subsequent append's condition fail.
                self._table.put_item(
                    Item=self._item("bibliography", new_id, entry, "", 1)
                )
                index[norm_title] = new_id
                self._table.put_item(
                    Item={
                        "cache_key": self._BIB_INDEX_KEY,
                        "data": json.dumps(index, ensure_ascii=False),
                        "_version": version + 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ConditionExpression=(
                        "attribute_not_exists(cache_key) OR #v = :cur"
                    ),
                    ExpressionAttributeNames={"#v": "_version"},
                    ExpressionAttributeValues={":cur": version},
                )
                return new_id
            except self._table.meta.client.exceptions.ConditionalCheckFailedException:
                logger.info("bib index conflict (attempt %d) — retrying", attempt + 1)
                continue
            except Exception as e:
                logger.warning("store_bibliography failed: %s", e)
                return None
        logger.error("store_bibliography exhausted retries for %s", norm_title)
        return None

    def _append_bib_mention(
        self, bib_id: str, mention: Dict[str, Any], mention_exists, max_retries: int = 8
    ) -> None:
        """Version-conditional append of a mention to an existing bib entry."""
        key = self._key("bibliography", bib_id)
        for _ in range(max_retries):
            try:
                resp = self._table.get_item(Key={"cache_key": key})
                item = resp.get("Item")
                if not item or "data" not in item:
                    return
                entry = json.loads(item["data"])
                version = int(item.get("_version", 0))
                if mention_exists(entry.get("mentions", [])):
                    return  # idempotent — mention already present
                entry.setdefault("mentions", []).append(mention)
                self._table.put_item(
                    Item=self._item("bibliography", bib_id, entry, "", version + 1),
                    ConditionExpression="#v = :cur",
                    ExpressionAttributeNames={"#v": "_version"},
                    ExpressionAttributeValues={":cur": version},
                )
                return
            except self._table.meta.client.exceptions.ConditionalCheckFailedException:
                continue
            except Exception as e:
                logger.warning("_append_bib_mention failed: %s", e)
                return


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
                region=get_aws_region(),
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
        region=aws.get("region", get_aws_region()),
    )
    return _entity_store_cache
