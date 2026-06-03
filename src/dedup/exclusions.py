"""Dedup exclusion persistence — DynamoDB (AWS) or local JSON (local mode).

Exclusions are pairs of filenames that a human reviewed and marked as
"not duplicates." They persist across pipeline runs.

DynamoDB schema (one item per pair):
    PK: exclusion#{entity_type}#{file1}#{file2}  (sorted)
    entity_type: people|places|groups|equipment

Local JSON schema (backwards compatible):
    {"exclusions": [{"person1": "file1.json", "person2": "file2.json"}, ...]}
"""

import json
import logging
from pathlib import Path
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Entity type → local JSON filename
_LOCAL_FILES = {
    "people": "not_duplicates.json",
    "places": "not_duplicates.json",
    "groups": "not_related.json",
    "equipment": "not_duplicates.json",
}


def _make_pair_key(entity_type: str, file1: str, file2: str) -> str:
    """Create a sorted, deterministic DynamoDB key for a pair."""
    a, b = sorted([file1, file2])
    return f"exclusion#{entity_type}#{a}#{b}"


def _normalize_exclusion_name(name: str) -> str:
    """Normalize a name for exclusion matching (survives file recreation)."""
    import re
    import unicodedata

    name = name.strip().lower()
    name = name.replace(",", "").replace(".", "").replace("_", " ")
    name = re.sub(r"\s+", " ", name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return name


def _make_name_pair_key(entity_type: str, name1: str, name2: str) -> str:
    """Create exclusion key from normalized names (stable across re-extraction)."""
    a, b = sorted([_normalize_exclusion_name(name1), _normalize_exclusion_name(name2)])
    return f"name_exclusion#{entity_type}#{a}#{b}"


class ExclusionStore:
    """Read/write dedup exclusions from DynamoDB or local JSON."""

    def __init__(
        self,
        entity_type: str,
        entity_dir: Optional[Path] = None,
        dynamo_table=None,
    ):
        self.entity_type = entity_type
        self.entity_dir = entity_dir
        self._table = dynamo_table

    def load(self) -> Set[Tuple[str, str]]:
        """Load all excluded pairs. Returns set of (file1, file2) tuples (sorted)."""
        if self._table:
            return self._load_dynamo()
        if self.entity_dir:
            return self._load_local()
        return set()

    def add(self, file1: str, file2: str) -> None:
        """Add an exclusion pair."""
        if self._table:
            self._add_dynamo(file1, file2)
        elif self.entity_dir:
            self._add_local(file1, file2)

    def add_group(self, filenames: list[str]) -> None:
        """Add all pairwise exclusions for a group of filenames."""
        for i, f1 in enumerate(filenames):
            for f2 in filenames[i + 1 :]:
                self.add(f1, f2)

    def add_by_name(self, name1: str, name2: str) -> None:
        """Add a name-based exclusion (survives file recreation)."""
        key = _make_name_pair_key(self.entity_type, name1, name2)
        if self._table:
            try:
                self._table.put_item(
                    Item={"cache_key": key, "entity_type": self.entity_type}
                )
            except Exception as e:
                logger.warning("Failed to write name exclusion: %s", e)
        elif self.entity_dir:
            # Store in local file
            path = self.entity_dir / ".name_exclusions.json"
            existing = set()
            if path.exists():
                try:
                    existing = set(
                        tuple(p) for p in json.loads(path.read_text(encoding="utf-8"))
                    )
                except Exception:
                    pass
            pair = tuple(
                sorted(
                    [_normalize_exclusion_name(name1), _normalize_exclusion_name(name2)]
                )
            )
            existing.add(pair)
            path.write_text(json.dumps([list(p) for p in existing]), encoding="utf-8")

    def add_group_by_name(self, names: list[str]) -> None:
        """Add all pairwise name-based exclusions for a group."""
        for i, n1 in enumerate(names):
            for n2 in names[i + 1 :]:
                self.add_by_name(n1, n2)

    def load_name_exclusions(self) -> Set[Tuple[str, str]]:
        """Load all name-based exclusions. Returns set of (norm_name1, norm_name2) tuples."""
        if self._table:
            return self._load_name_dynamo()
        if self.entity_dir:
            return self._load_name_local()
        return set()

    def _load_name_dynamo(self) -> Set[Tuple[str, str]]:
        prefix = f"name_exclusion#{self.entity_type}#"
        pairs: Set[Tuple[str, str]] = set()
        kwargs = {
            "FilterExpression": "begins_with(cache_key, :prefix)",
            "ExpressionAttributeValues": {":prefix": prefix},
            "ProjectionExpression": "cache_key",
        }
        try:
            while True:
                resp = self._table.scan(**kwargs)
                for item in resp.get("Items", []):
                    parts = item["cache_key"].split("#")
                    if len(parts) == 4:
                        pairs.add((parts[2], parts[3]))
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except Exception as e:
            logger.warning("Failed to load name exclusions: %s", e)
        return pairs

    def _load_name_local(self) -> Set[Tuple[str, str]]:
        if not self.entity_dir:
            return set()
        path = self.entity_dir / ".name_exclusions.json"
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return set(tuple(p) for p in data)
        except Exception:
            return set()

    # --- DynamoDB ---

    def _load_dynamo(self) -> Set[Tuple[str, str]]:
        prefix = f"exclusion#{self.entity_type}#"
        pairs: Set[Tuple[str, str]] = set()
        try:
            resp = self._table.scan(
                FilterExpression="begins_with(cache_key, :prefix)",
                ExpressionAttributeValues={":prefix": prefix},
                ProjectionExpression="cache_key",
            )
            for item in resp.get("Items", []):
                parts = item["cache_key"].split("#")
                if len(parts) == 4:
                    pairs.add((parts[2], parts[3]))
            # Handle pagination
            while "LastEvaluatedKey" in resp:
                resp = self._table.scan(
                    FilterExpression="begins_with(cache_key, :prefix)",
                    ExpressionAttributeValues={":prefix": prefix},
                    ProjectionExpression="cache_key",
                    ExclusiveStartKey=resp["LastEvaluatedKey"],
                )
                for item in resp.get("Items", []):
                    parts = item["cache_key"].split("#")
                    if len(parts) == 4:
                        pairs.add((parts[2], parts[3]))
        except Exception as e:
            logger.warning("Failed to load exclusions from DynamoDB: %s", e)
        logger.info(
            "Loaded %d %s exclusions from DynamoDB", len(pairs), self.entity_type
        )
        return pairs

    def _add_dynamo(self, file1: str, file2: str) -> None:
        key = _make_pair_key(self.entity_type, file1, file2)
        try:
            self._table.put_item(
                Item={
                    "cache_key": key,
                    "entity_type": self.entity_type,
                    "file1": min(file1, file2),
                    "file2": max(file1, file2),
                }
            )
        except Exception as e:
            logger.warning("Failed to write exclusion to DynamoDB: %s", e)

    # --- Local JSON ---

    def _local_path(self) -> Optional[Path]:
        if not self.entity_dir:
            return None
        filename = _LOCAL_FILES.get(self.entity_type, "not_duplicates.json")
        return self.entity_dir / filename

    def _load_local(self) -> Set[Tuple[str, str]]:
        path = self._local_path()
        if not path or not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pairs: Set[Tuple[str, str]] = set()
            for pair in data.get("exclusions", []):
                f1 = pair.get("person1", pair.get("file1", ""))
                f2 = pair.get("person2", pair.get("file2", ""))
                if f1 and f2:
                    pairs.add(tuple(sorted([f1, f2])))
            return pairs
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load exclusions from %s: %s", path, e)
            return set()

    def _add_local(self, file1: str, file2: str) -> None:
        path = self._local_path()
        if not path:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            data = {"exclusions": []}
        data["exclusions"].append(
            {"person1": min(file1, file2), "person2": max(file1, file2)}
        )
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def get_exclusion_store(
    entity_type: str,
    entity_dir: Optional[Path] = None,
) -> ExclusionStore:
    """Factory: returns DynamoDB-backed store in AWS mode, local JSON otherwise."""
    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})

    if aws.get("enabled"):
        import boto3

        region = aws.get("region", "us-east-1")
        table_name = aws.get("cache_table", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        return ExclusionStore(entity_type, entity_dir=entity_dir, dynamo_table=table)

    return ExclusionStore(entity_type, entity_dir=entity_dir)


def migrate_local_to_dynamo(entity_type: str, entity_dir: Path) -> int:
    """One-time migration: load local JSON exclusions and write to DynamoDB."""
    local_store = ExclusionStore(entity_type, entity_dir=entity_dir)
    pairs = local_store._load_local()
    if not pairs:
        return 0

    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})
    if not aws.get("enabled"):
        return 0

    import boto3

    region = aws.get("region", "us-east-1")
    table_name = aws.get("cache_table", "dev-wwii-api-cache")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    dynamo_store = ExclusionStore(entity_type, dynamo_table=table)

    for f1, f2 in pairs:
        dynamo_store._add_dynamo(f1, f2)

    logger.info("Migrated %d %s exclusions to DynamoDB", len(pairs), entity_type)
    return len(pairs)


def load_reviewed_pairs(entity_type: str) -> Set[Tuple[str, str]]:
    """Load recently-reviewed pairs from DynamoDB. Returns set of (file1, file2) tuples."""
    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})
    if not aws.get("enabled"):
        return set()

    import boto3

    try:
        region = aws.get("region", "us-east-1")
        table_name = aws.get("cache_table", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        prefix = f"reviewed#{entity_type}#"
        pairs: Set[Tuple[str, str]] = set()
        kwargs = {
            "FilterExpression": "begins_with(cache_key, :prefix)",
            "ExpressionAttributeValues": {":prefix": prefix},
            "ProjectionExpression": "cache_key",
        }
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                # Key format: reviewed#{entity_type}#{file1}#{file2} (sorted)
                remainder = item["cache_key"][len(prefix) :]
                files = remainder.split("#", 1)
                if len(files) == 2:
                    pairs.add(tuple(files))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return pairs
    except Exception as e:
        logger.warning("Failed to load reviewed pairs: %s", e)
        return set()
