"""Cache for external search results (Wikipedia, Grokipedia, OpenSERP, Archive.org, LOC).

Stores both positive and negative results in DynamoDB (AWS) or local diskcache (local).
Prevents redundant HTTP calls across runs. TTL-based expiry for re-checking.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Default TTL: 30 days for positive, 7 days for negative
POSITIVE_TTL_DAYS = 30
NEGATIVE_TTL_DAYS = 7

# Sentinel a fetcher returns to signal "transient failure, do NOT negative-cache" (distinct from a
# genuine miss, which IS negative-cached). See cache_result().
TRANSIENT = object()

_cache_backend = None


def _get_backend():
    """Get or create the cache backend."""
    global _cache_backend
    if _cache_backend is not None:
        return _cache_backend

    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})

    if aws.get("enabled"):
        _cache_backend = _DynamoBackend(
            table_name=aws.get("cache_table", "dev-wwii-api-cache"),
            region=aws.get("region", "us-east-1"),
        )
    else:
        _cache_backend = _LocalBackend(Path("cache/search_cache"))

    return _cache_backend


def _make_key(source: str, query: str) -> str:
    """Create cache key from source and query."""
    h = hashlib.sha256(f"{source}:{query}".encode()).hexdigest()[:16]
    return f"search#{source}#{h}"


def get_cached(source: str, query: str) -> Optional[str]:
    """Get cached search result. Returns content string, "NOT_FOUND" for negative cache, or None if not cached."""
    backend = _get_backend()
    key = _make_key(source, query)
    return backend.get(key)


def cache_result(
    source: str,
    query: str,
    result,
    negative_days: Optional[float] = None,
    positive_days: Optional[float] = None,
) -> None:  # result: str | None | TRANSIENT
    """Cache a search result.

    - a truthy string -> positive cache (POSITIVE_TTL_DAYS)
    - None / "" -> NEGATIVE cache ("NOT_FOUND", NEGATIVE_TTL_DAYS) — a genuine miss
    - ``TRANSIENT`` -> DO NOT CACHE. A transient failure (timeout / 5xx / 429 / network
      exception) must NOT be negative-cached, else a one-off outage suppresses a genuinely-available
      resource for NEGATIVE_TTL_DAYS. Callers pass TRANSIENT when the fetch errored vs. truly missed.
    """
    if result is TRANSIENT:
        return  # never persist a transient failure as a negative
    backend = _get_backend()
    key = _make_key(source, query)
    if result:
        days = POSITIVE_TTL_DAYS if positive_days is None else positive_days
        backend.put(key, result, ttl_days=days)
    else:
        backend.put(
            key,
            "NOT_FOUND",
            ttl_days=NEGATIVE_TTL_DAYS if negative_days is None else negative_days,
        )


ANSWER_TTL_DAYS = (
    90  # the re-search window for a source's answer, empty or not (config)
)


MAX_ANSWER_CHARS = 300_000  # under DynamoDB's 400 KB item limit
SCRAPED_TTL_DAYS = 14  # an empty answer from a scraped search engine (OpenSERP)
METADATA_TTL_DAYS = 7  # a record that may still be gaining files (archive.org metadata)


def _window(key: str, default: float, config: Optional[dict]) -> float:
    try:
        if config is None:
            from src.utils.config import load_config

            config = load_config()
        days = float(
            ((config or {}).get("supplemental_material") or {}).get(key, default)
        )
        return days if days > 0 else default
    except Exception:  # noqa: BLE001 - the default window
        return default


def answer_ttl_days(config: Optional[dict] = None) -> float:
    """``supplemental_material.search_cache_days`` (default 90): how long a source's answer -
    INCLUDING an empty one - is reused before that source is asked again (owner 2026-11-20:
    record empty answers so archive.org and Gutenberg are not asked the same thing every run).
    """
    return _window("search_cache_days", ANSWER_TTL_DAYS, config)


def scraped_ttl_days(config: Optional[dict] = None) -> float:
    """``supplemental_material.openserp_cache_days`` (default 14): the shorter window for an
    answer from a scraped search engine - a rate-limited or captcha'd engine can return 200 with
    nothing, which must not hide a book for 90 days."""
    return _window("openserp_cache_days", SCRAPED_TTL_DAYS, config)


def bump_counter(
    namespace: str, ident: str, ttl_days: float, once_per_day: bool = False
) -> int:
    """Count one more occurrence of ``ident`` in the shared cache (it survives tasks and runs;
    the record schemas have no room for it). ``once_per_day``: at most one count per UTC day (a
    redelivered message or a retry loop is not "another run"). Returns the count, or 0 if it
    cannot be kept (callers then behave as before)."""
    import datetime as _dt

    try:
        backend, key = _get_backend(), _make_key(f"counter#{namespace}", ident)
        today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        count, last = 0, ""
        try:
            count_s, _, last = str(backend.get(key) or "0|").partition("|")
            count = int(count_s)
        except ValueError:
            count, last = 0, ""
        if once_per_day and last == today:
            return count
        count += 1
        backend.put(key, f"{count}|{today}", ttl_days=ttl_days)
        return count
    except Exception:  # noqa: BLE001 - not counted
        return 0


def cached_answer(
    source: str,
    query: str,
    fetch: Callable[[], Any],
    ttl_days: Optional[float] = None,
) -> Any:
    """``fetch()``'s answer to ``query`` at ``source``, asked at most once per window (see
    ``answer_ttl_days``) across runs and tasks - an EMPTY answer (no candidates, not found) is
    recorded too, so the source is not asked the same thing again. An answer given while a
    service was down (``service_outage`` saw a failure during ``fetch``) is NOT recorded: it is
    asked again next run. The answer must be JSON-serializable (tuples come back as lists).
    """
    from src.utils import service_outage

    key = _make_key(f"answer#{source}", query)
    try:
        raw = _get_backend().get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception:  # noqa: BLE001 - unknown or not ours: ask the source
        pass
    with service_outage.watch() as outages:
        answer = fetch()
    if outages:
        return answer  # an outage is never recorded as an answer
    try:
        body = json.dumps(answer)
        if len(body) > MAX_ANSWER_CHARS:
            return answer  # too big to keep (never cut: half a JSON is no answer)
        _get_backend().put(
            key,
            body,
            ttl_days=answer_ttl_days() if ttl_days is None else ttl_days,
        )
    except Exception:  # noqa: BLE001 - not recorded: asked again next time
        logger.debug("search answer not recorded source=%s", source)
    return answer


class _DynamoBackend:
    def __init__(self, table_name: str, region: str):
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, key: str) -> Optional[str]:
        from src.utils import service_outage

        try:
            resp = self._table.get_item(Key={"cache_key": key})
            service_outage.ok("dynamodb")
            item = resp.get("Item")
            if not item:
                return None
            # Check TTL
            ttl = item.get("ttl", 0)
            if ttl and int(time.time()) > ttl:
                return None  # Expired
            return item.get("response", "")
        except (
            Exception
        ) as e:  # noqa: BLE001 - a miss: search again (and count the outage)
            service_outage.dynamodb_failed("search_cache_get", e)
            return None

    def put(self, key: str, value: str, ttl_days: float = 30) -> None:
        from src.utils import service_outage

        try:
            self._table.put_item(
                Item={
                    "cache_key": key,
                    "response": value,
                    "ttl": int(time.time() + ttl_days * 86400),
                }
            )
            service_outage.ok("dynamodb")
        except Exception as e:  # noqa: BLE001 - not cached: searched again next time
            service_outage.dynamodb_failed("search_cache_put", e)


class _LocalBackend:
    def __init__(self, cache_dir: Path):
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[str]:
        f = self._dir / f"{key}.json"
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text())
            if data.get("ttl", 0) and int(time.time()) > data["ttl"]:
                f.unlink()
                return None
            return data.get("response", "")
        except Exception:
            return None

    def put(self, key: str, value: str, ttl_days: float = 30) -> None:
        f = self._dir / f"{key}.json"
        f.write_text(
            json.dumps({"response": value, "ttl": int(time.time() + ttl_days * 86400)})
        )
