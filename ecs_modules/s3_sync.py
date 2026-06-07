"""S3 sync operations for ECS entrypoint."""

import json
import logging
import os
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)

BUCKET = os.environ.get("S3_BUCKET", "")
REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1"))
WORKDIR = Path("/tmp/pipeline")

_downloaded_keys: set = set()
_deleted_keys: set = set()


def _read_s3_manifest(prefix_filter: str = "") -> list:
    """Read the pending manifest from S3. Returns list of S3 keys matching prefix."""
    try:
        s3 = _s3_client()
        resp = s3.get_object(Bucket=BUCKET, Key="manifests/pending.json")
        import json

        keys = json.loads(resp["Body"].read())
        if prefix_filter:
            keys = [k for k in keys if k.startswith(prefix_filter)]
        return keys
    except Exception:
        return []


def _download_keys(keys: list, local_dir: Path) -> int:
    """Download specific S3 keys to local dir. Returns count."""
    s3 = _s3_client()
    count = 0
    # Also download metadata files for the same chapters
    extra_keys = set()
    for key in keys:
        parts = key.split("/")
        if len(parts) >= 3:
            # content/BookName/chapter1/file.md → also get meta.yaml
            chapter_prefix = "/".join(parts[:3]) + "/"
            extra_keys.add(chapter_prefix)

    # Download the specific files plus their chapter metadata
    all_prefixes = extra_keys
    downloaded = set()
    for prefix in all_prefixes:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if k not in downloaded:
                    local = local_dir / k
                    local.parent.mkdir(parents=True, exist_ok=True)
                    s3.download_file(BUCKET, k, str(local))
                    downloaded.add(k)
                    count += 1
    return count


def _clear_manifest() -> None:
    """Clear the pending manifest after processing."""
    try:
        _s3_client().delete_object(Bucket=BUCKET, Key="manifests/pending.json")
        logger.info("Cleared manifest")
    except Exception:
        pass


def _s3_client():
    return boto3.client("s3", region_name=REGION)


def track_deletion(local_path: Path) -> None:
    """Record a local file deletion for S3 cleanup in final sync."""
    try:
        rel = local_path.relative_to(WORKDIR)
        _deleted_keys.add(str(rel))
    except ValueError:
        # Path not under WORKDIR — try relative to /app via symlink resolution
        try:
            resolved = local_path.resolve()
            rel = resolved.relative_to(WORKDIR)
            _deleted_keys.add(str(rel))
        except ValueError:
            pass


def s3_sync_down(prefix: str, local_dir: Path) -> int:
    """Download S3 prefix to local dir. Returns file count."""
    s3 = _s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            _downloaded_keys.add(key)
            local = local_dir / key
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, key, str(local))
            count += 1
    return count


def s3_sync_up(
    local_dir: Path,
    prefix: str,
    exclude_patterns: "list | None" = None,
    skip_keys: "set | None" = None,
) -> tuple:
    """Upload local dir to S3 prefix. Returns (file_count, uploaded_keys)."""
    s3 = _s3_client()
    exclude = exclude_patterns or []
    skip = skip_keys or set()
    count = 0
    uploaded = []
    for f in local_dir.rglob("*"):
        if f.is_file():
            if any(pat in f.name for pat in exclude):
                continue
            key = f"{prefix}/{f.relative_to(local_dir)}"
            if key in skip:
                continue
            s3.upload_file(str(f), BUCKET, key)
            uploaded.append(key)
            count += 1
    return count, uploaded


def _get_book_entity_files(s3, book_name: str) -> list:
    """Get list of entity file S3 keys for a book using the book manifest."""
    from src.utils.book_manifest import BookManifest

    # Try DynamoDB manifest first (fast)
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        manifest = BookManifest(dynamo_table=table)
        all_files = manifest.get_all_files(book_name)
        if all_files:
            keys = []
            for entity_type, filenames in all_files.items():
                for fn in filenames:
                    keys.append(f"output/{entity_type}/{fn}")
            logger.info("Book manifest: %d files for %s", len(keys), book_name)
            return keys
    except Exception as e:
        logger.debug("Book manifest lookup failed: %s", e)

    # Fallback: download all entity indexes (old behavior)
    logger.info(
        "No book manifest for %s, falling back to full index download", book_name
    )
    referenced = set()
    paginator = s3.get_paginator("list_objects_v2")
    for subdir in [
        "people",
        "people_groups",
        "places",
        "equipment",
        "dates",
        "weather",
        "logistics",
        "casualties",
        "bibliography",
    ]:
        index_key = f"output/{subdir}/index.json"
        _download_s3_file(s3, index_key)
        index_path = WORKDIR / index_key
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
                for name, filename in index.items():
                    if isinstance(filename, str):
                        referenced.add(f"output/{subdir}/{filename}")
            except Exception:
                pass

    # Always include full bibliography
    for page in paginator.paginate(Bucket=BUCKET, Prefix="output/bibliography/"):
        for obj in page.get("Contents", []):
            referenced.add(obj["Key"])

    return list(referenced)


def _materialize_from_dynamo() -> bool:
    """Materialize entities from DynamoDB to local files. Returns True if successful."""
    try:
        from src.utils.entity_store import get_entity_store

        store = get_entity_store()
        if not store:
            return False

        entity_types = [
            "people",
            "people_groups",
            "places",
            "dates",
            "equipment",
            "weather",
            "logistics",
            "casualties",
            "maps",
            "supplemental",
            "bibliography",
        ]

        total = 0
        for entity_type in entity_types:
            entities = store.list_all(entity_type)
            if not entities:
                continue
            local_dir = WORKDIR / "output" / entity_type
            local_dir.mkdir(parents=True, exist_ok=True)
            for item in entities:
                entity = item["data"]
                filename = item.get("filename")
                if not filename:
                    # Fallback: use entity ID as filename (no collisions)
                    id_fields = {
                        "people": "PersonID",
                        "people_groups": "GroupID",
                        "places": "PlaceID",
                        "dates": "DateID",
                        "equipment": "EquipmentID",
                        "weather": "WeatherID",
                        "logistics": "LogisticsID",
                        "casualties": "CasualtyID",
                        "maps": "MapID",
                        "supplemental": "BibliographyID",
                        "bibliography": "BibliographyID",
                    }
                    eid = entity.get(id_fields.get(entity_type, ""), "unknown")
                    filename = f"{eid}.json"
                filepath = local_dir / filename
                filepath.write_text(
                    json.dumps(entity, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                total += 1

        if total > 0:
            logger.info(
                "Materialized %d entities from DynamoDB (skipped S3 download)", total
            )
            return True
        return False
    except Exception as e:
        logger.warning("DynamoDB materialization failed, falling back to S3: %s", e)
        return False


def _download_inputs(phase_script: str) -> None:
    """Download the appropriate inputs from S3 for this phase."""
    if "phase1" in phase_script:
        keys = _read_s3_manifest("contentrepository/")
        if keys:
            n = _download_keys(keys, Path("/app"))
            logger.info("Downloaded %d content files (incremental)", n)
            os.environ["_PHASE1_MODE"] = f"Incremental: {n} new files"
        else:
            # Scope full sync to BOOK_NAME if set, otherwise download all
            book_name = os.environ.get("BOOK_NAME", "")
            prefix = (
                f"contentrepository/{book_name}/" if book_name else "contentrepository/"
            )
            if not book_name:
                logger.warning("No manifest and no BOOK_NAME — downloading ALL content")
            n = s3_sync_down(prefix, Path("/app"))
            logger.info("Downloaded %d content files (full, prefix=%s)", n, prefix)
            os.environ["_PHASE1_MODE"] = f"Full re-parse: {n} files"
    elif "phase2" in phase_script:
        n = _download_phase2_inputs()
        logger.info("Downloaded %d files for Phase 2", n)
    elif "phase3" in phase_script or "import" in phase_script:
        # Phase 3: try DynamoDB first (fast), fall back to S3
        if _materialize_from_dynamo():
            return
        # Fallback: download from S3
        entity_dirs = [
            "people",
            "people_groups",
            "places",
            "dates",
            "equipment",
            "weather",
            "logistics",
            "casualties",
            "maps",
            "supplemental",
            "bibliography",
        ]
        s3 = _s3_client()
        book_name = os.environ.get("BOOK_NAME", "")
        total = 0

        if book_name:
            # Scoped download: index.json + files referenced by this book's events
            logger.info("Scoped download for book: %s", book_name)
            referenced = _get_book_entity_files(s3, book_name)
            for subdir in entity_dirs:
                local = WORKDIR / "output" / subdir
                local.mkdir(parents=True, exist_ok=True)
                # Always download index
                _download_s3_file(s3, f"output/{subdir}/index.json")
                # Download only referenced files for this subdir
                subdir_files = [
                    f for f in referenced if f.startswith(f"output/{subdir}/")
                ]
                for key in subdir_files:
                    _download_s3_file(s3, key)
                total += len(subdir_files)
            logger.info("Downloaded %d entity files (scoped to %s)", total, book_name)
        else:
            # Full download (no book scope)
            for subdir in entity_dirs:
                d, s = _download_s3_prefix_skip_existing(s3, f"output/{subdir}/")
                total += d
            logger.info("Downloaded %d entity files for Phase 3 (full)", total)


def _download_phase2_inputs() -> int:
    """Download only unprocessed parsed files + entity dirs for cross-referencing."""
    s3 = _s3_client()

    # Try manifest first (fast — single DynamoDB read)
    manifest_keys = _read_manifest()
    parsed_keys = [k for k in manifest_keys if k.endswith("-parsed.json")]

    if parsed_keys:
        for key in parsed_keys:
            _download_s3_file(s3, key)
        logger.info(
            "Phase 2 incremental (manifest): %d parsed files from manifest",
            len(parsed_keys),
        )
        new_parsed = len(parsed_keys)
    else:
        # Fallback: scan S3 for parsed files without event files
        book_name = os.environ.get("BOOK_NAME", "")
        scan_prefix = f"output/content/{book_name}/" if book_name else "output/content/"
        logger.info(
            "No manifest found, falling back to S3 scan (prefix: %s)", scan_prefix
        )
        force_all = os.environ.get("FORCE_DOWNLOAD_PARSED") == "1"
        if force_all:
            existing_events: set = set()
        else:
            existing_events = _list_s3_keys_matching(s3, scan_prefix, "-event.json")
        new_parsed = _download_new_parsed(s3, existing_events, prefix=scan_prefix)
        logger.info(
            "Phase 2 incremental (S3 scan): %d new parsed files, %d existing events skipped",
            new_parsed,
            len(existing_events),
        )

    # Download entity index files only (not full dirs — extraction uses filename-based indexes)
    index_prefixes = [
        "output/people/index.json",
        "output/people_groups/index.json",
        "output/places/index.json",
        "output/equipment/index.json",
        "output/dates/index.json",
    ]
    for key in index_prefixes:
        _download_s3_file(s3, key)

    # Download bibliography and supplemental dirs (needed for dedup/writing, not just indexing)
    for p in ["output/bibliography/", "output/supplemental/"]:
        _download_s3_prefix(s3, p)

    return new_parsed


def _list_s3_keys_matching(s3, prefix: str, suffix: str) -> set:
    """List S3 keys under prefix that end with suffix."""
    keys: set = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=prefix
    ):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(suffix):
                keys.add(obj["Key"])
    return keys


def _download_new_parsed(
    s3, existing_events: set, prefix: str = "output/content/"
) -> int:
    """Download parsed files without corresponding event files."""
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=prefix
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("-parsed.json"):
                continue
            if key.replace("-parsed.json", "-event.json") in existing_events:
                continue
            _download_s3_file(s3, key)
            count += 1
    return count


def _download_s3_prefix(s3, prefix: str) -> int:
    """Download all files under an S3 prefix."""
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=prefix
    ):
        for obj in page.get("Contents", []):
            _download_s3_file(s3, obj["Key"])
            count += 1
    return count


def _download_s3_prefix_skip_existing(s3, prefix: str) -> tuple:
    """Download files under an S3 prefix, skipping those already local.

    Returns (downloaded, skipped) counts.
    """
    downloaded = 0
    skipped = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=prefix
    ):
        for obj in page.get("Contents", []):
            local = WORKDIR / obj["Key"]
            if local.exists():
                skipped += 1
                continue
            _download_s3_file(s3, obj["Key"])
            downloaded += 1
    return downloaded, skipped


def _download_s3_file(s3, key: str) -> None:
    """Download a single S3 file to the local workdir."""
    from botocore.exceptions import ClientError

    local = WORKDIR / key
    local.parent.mkdir(parents=True, exist_ok=True)
    try:
        s3.download_file(BUCKET, key, str(local))
        _downloaded_keys.add(key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            logger.debug("File not found, skipping: %s", key)
        else:
            raise


def _download_dedup_data() -> None:
    """Download entity files needed for cross-book dedup comparison."""
    # Try DynamoDB first (faster than S3 for entity files)
    if _materialize_from_dynamo():
        # Still need event files from S3 for cross-reference
        s3 = _s3_client()
        d, s = _download_s3_prefix_skip_existing(s3, "output/content/")
        logger.info(
            "Dedup: materialized entities from DynamoDB, downloaded %d event files from S3",
            d,
        )
        return

    s3 = _s3_client()
    dedup_files = [
        "output/people/index.json",
        "output/people_groups/index.json",
        "output/places/index.json",
        "output/equipment/index.json",
    ]
    for key in dedup_files:
        _download_s3_file(s3, key)
    for prefix in [
        "output/people/",
        "output/people_groups/",
        "output/places/",
        "output/equipment/",
    ]:
        _download_s3_prefix_skip_existing(s3, prefix)
    d, s = _download_s3_prefix_skip_existing(s3, "output/content/")
    logger.info(
        "Dedup: downloaded entity + event files (%d event files, %d skipped)",
        d,
        s,
    )


def _write_manifest(keys: list) -> None:
    """Write list of changed S3 keys to DynamoDB for next phase."""
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        table.put_item(
            Item={
                "cache_key": "manifest#phase2",
                "keys": keys,
            }
        )
        logger.info("Wrote manifest to DynamoDB: %d keys", len(keys))
    except Exception as e:
        logger.warning("Failed to write manifest: %s", e)


def _read_manifest() -> list:
    """Read changed file manifest from DynamoDB. Returns empty list if none."""
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        # Try pending#parsed first (written by trigger Lambda)
        resp = table.get_item(Key={"cache_key": "pending#parsed"})
        item = resp.get("Item")
        if item:
            keys = item.get("keys", [])
            if keys:
                logger.info("Read pending#parsed from DynamoDB: %d keys", len(keys))
                table.delete_item(Key={"cache_key": "pending#parsed"})
                return keys
        # Fallback: legacy manifest#phase2
        resp = table.get_item(Key={"cache_key": "manifest#phase2"})
        item = resp.get("Item")
        if item:
            keys = item.get("keys", [])
            logger.info("Read manifest#phase2 from DynamoDB: %d keys", len(keys))
            return keys
    except Exception as e:
        logger.warning("Failed to read manifest: %s", e)
    return []


def _final_sync(phase_script: str = ""):
    """Final upload of new output to S3. Only uploads entity subdirs, not parsed/event files."""
    if "phase1" in phase_script:
        # Phase 1: upload book content (parsed files trigger Phase 2)
        d = WORKDIR / "output" / "content"
        if d.exists():
            n, _ = s3_sync_up(d, "output/content")
            logger.info("Final sync: uploaded %d output files", n)
    elif "phase2" in phase_script or "phase3" in phase_script:
        # Phase 2/3: only upload entity subdirs, skip book dirs with parsed/event files
        entity_dirs = [
            "people",
            "people_groups",
            "places",
            "dates",
            "equipment",
            "weather",
            "logistics",
            "casualties",
            "maps",
            "supplemental",
            "bibliography",
            "metrics",
        ]
        total = 0
        all_uploaded = []
        # Phase 3 modifies existing files — don't skip any
        skip = _downloaded_keys if "phase2" in phase_script else set()
        for subdir in entity_dirs:
            d = WORKDIR / "output" / subdir
            if d.exists():
                n, keys = s3_sync_up(d, f"output/{subdir}", skip_keys=skip)
                total += n
                all_uploaded.extend(keys)
        logger.info("Final sync: uploaded %d entity files", total)

        # Delete merged/removed files from S3
        if _deleted_keys:
            s3 = _s3_client()
            for key in _deleted_keys:
                try:
                    s3.delete_object(Bucket=BUCKET, Key=key)
                except Exception:
                    pass
            logger.info(
                "Final sync: deleted %d merged files from S3", len(_deleted_keys)
            )

        # Write manifest to DynamoDB so next phase only downloads changed files
        if all_uploaded and "phase2" in phase_script:
            _write_manifest(all_uploaded)
    elif "import" in phase_script:
        pass  # import doesn't produce output
