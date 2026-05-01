"""ECS entrypoint — run pipeline phase with local filesystem, sync to S3 incrementally."""

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import boto3

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s"
)
logger = logging.getLogger(__name__)

BUCKET = os.environ["S3_BUCKET"]
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
WORKDIR = Path("/tmp/pipeline")
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "120"))  # seconds


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


_downloaded_keys: set = set()


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


class BackgroundSync:
    """Periodically sync output and cache dirs to S3."""

    def __init__(self, interval: int = 120):
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Start the background sync thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Background S3 sync started (every %ds)", self._interval)

    def stop(self):
        """Stop the background sync thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def _run(self):
        while not self._stop.wait(self._interval):
            self._sync()

    def _sync(self):
        try:
            for name, prefix in [("output", "output")]:
                d = WORKDIR / name
                if d.exists():
                    n, _ = s3_sync_up(
                        d,
                        prefix,
                        exclude_patterns=["-parsed.json", "-event.json"],
                        skip_keys=_downloaded_keys,
                    )
                    if n:
                        logger.info("Background sync: uploaded %d %s files", n, name)
        except Exception as e:
            logger.warning("Background sync error: %s", e)


def _load_secrets():
    """Fetch GROK_API_KEY from Secrets Manager if not already set."""
    if os.environ.get("GROK_API_KEY"):
        return
    secret_id = os.environ.get("SECRETS_ID", "")
    if not secret_id:
        return
    try:
        sm = boto3.client("secretsmanager", region_name=REGION)
        resp = sm.get_secret_value(SecretId=secret_id)
        os.environ["GROK_API_KEY"] = resp["SecretString"]
        logger.info("Loaded API key from Secrets Manager")
    except Exception as e:
        logger.error("Failed to load secret %s: %s", secret_id, e)


def _setup_symlinks():
    """Symlink workdir paths into /app so scripts find them."""
    app_dir = Path("/app")
    symlinks = {
        "contentrepository": WORKDIR / "content",
        "output": WORKDIR / "output",
        "cache": WORKDIR / "cache",
        "review": WORKDIR / "review",
        "logs": WORKDIR / "logs",
    }
    for name, target in symlinks.items():
        link = app_dir / name
        target.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)


def _patch_config():
    """Enable AWS mode in config.yaml so scripts use DynamoDB cache."""
    import yaml  # pylint: disable=import-outside-toplevel

    config_path = Path("/app/config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["aws"]["enabled"] = True
    config["aws"]["s3_bucket"] = BUCKET
    config["aws"]["region"] = REGION
    cache_table = os.environ.get("CACHE_TABLE", "")
    if cache_table:
        config["aws"]["cache_table"] = cache_table

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)
    logger.info("Patched config.yaml: aws.enabled=true, s3_bucket=%s", BUCKET)


def run_phase(phase_script: str, extra_args: list) -> None:
    """Run a pipeline phase script with incremental S3 sync."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    _load_secrets()
    _patch_config()

    if "phase1" in phase_script:
        _clear_all_locks()

    _download_inputs(phase_script)
    _setup_symlinks()

    sync = BackgroundSync(SYNC_INTERVAL)
    if "phase2" in phase_script or "phase3" in phase_script:
        sync.start()

    env = os.environ.copy()
    cmd = [sys.executable, phase_script] + extra_args
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd="/app", env=env, check=False)

    sync.stop()

    if result.returncode != 0:
        logger.error("Phase script exited with code %d", result.returncode)
        _final_sync(phase_script)
        if "phase3" not in phase_script:
            _remove_lock(phase_script)
        sys.exit(result.returncode)

    _final_sync(phase_script)
    _post_process(phase_script, env)


def _download_inputs(phase_script: str) -> None:
    """Download the appropriate inputs from S3 for this phase."""
    if "phase1" in phase_script:
        keys = _read_s3_manifest("content/")
        if keys:
            n = _download_keys(keys, WORKDIR)
            logger.info("Downloaded %d content files (incremental)", n)
        else:
            n = s3_sync_down("content/", WORKDIR)
            logger.info("Downloaded %d content files (full)", n)
    elif "phase2" in phase_script:
        n = _download_phase2_inputs()
        logger.info("Downloaded %d files for Phase 2", n)
    elif "phase3" in phase_script or "import" in phase_script:
        manifest = _read_manifest()
        if manifest:
            s3 = _s3_client()
            for key in manifest:
                local = WORKDIR / key
                local.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(BUCKET, key, str(local))
                _downloaded_keys.add(key)
            logger.info(
                "Downloaded %d files from manifest (incremental)", len(manifest)
            )
        else:
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
            ]
            total = 0
            for subdir in entity_dirs:
                n = s3_sync_down(f"output/{subdir}/", WORKDIR)
                total += n
            logger.info("Downloaded %d entity files from S3 (full, no manifest)", total)


def _download_phase2_inputs() -> int:
    """Download only unprocessed parsed files + entity dirs for cross-referencing."""
    s3 = _s3_client()

    existing_events = _list_s3_keys_matching(s3, "output/", "-event.json")
    new_parsed = _download_new_parsed(s3, existing_events)

    entity_prefixes = [
        "output/people/",
        "output/people_groups/",
        "output/places/",
        "output/dates/",
        "output/equipment/",
        "output/weather/",
        "output/supplemental/",
        "output/bibliography/",
    ]
    entity_count = sum(_download_s3_prefix(s3, p) for p in entity_prefixes)

    total = new_parsed + entity_count
    logger.info(
        "Phase 2 incremental: %d new parsed files, %d existing events skipped",
        new_parsed,
        len(existing_events),
    )
    return total


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


def _download_new_parsed(s3, existing_events: set) -> int:
    """Download parsed files without corresponding event files."""
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix="output/"
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


def _post_process(phase_script: str, env: dict) -> None:
    """Run post-processing steps after a successful phase."""
    if "phase1" in phase_script:
        _clear_manifest()
    if "phase2" in phase_script:
        _run_dedup_detection(env)
        _check_pending_content()
    if "phase3" not in phase_script:
        _remove_lock(phase_script)
    _notify_complete(phase_script)


def _check_pending_content() -> None:
    """Check DynamoDB for queued content and re-trigger Phase 1 if found."""
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        resp = table.get_item(Key={"cache_key": "pending#content"})
        item = resp.get("Item")
        if not item or not item.get("keys"):
            return
        keys = item["keys"]
        logger.info("Found %d pending content files, re-triggering pipeline", len(keys))
        # Clear pending before re-trigger
        table.delete_item(Key={"cache_key": "pending#content"})
        # Write keys as S3 manifest for Phase 1
        _s3_client().put_object(
            Bucket=BUCKET,
            Key="manifests/pending.json",
            Body=json.dumps(list(keys)).encode(),
        )
        # Trigger Phase 1 by publishing to content-uploaded topic
        topic_arn = os.environ.get("CONTENT_TOPIC_ARN", "")
        if topic_arn:
            sns = boto3.client("sns", region_name=REGION)
            sns.publish(TopicArn=topic_arn, Message=json.dumps({"pending": True}))
            logger.info("Re-triggered pipeline for pending content")
    except Exception as e:
        logger.warning("Failed to check pending content: %s", e)


def _run_dedup_detection(env: dict) -> None:
    """Run duplicate detection scripts after Phase 2."""
    # Download ALL entity files from S3 so dedup sees the full inventory
    s3 = _s3_client()
    entity_prefixes = ["output/people/", "output/people_groups/", "output/places/", "output/equipment/"]
    for prefix in entity_prefixes:
        _download_s3_prefix(s3, prefix)
    logger.info("Downloaded full entity dirs for dedup detection")

    dedup_scripts = [
        "scripts/find_duplicate_people.py",
        "scripts/find_duplicate_places_v2.py",
        "scripts/find_duplicate_groups.py",
        "scripts/find_duplicate_equipment.py",
    ]
    for script in dedup_scripts:
        if Path(f"/app/{script}").exists():
            logger.info("Running dedup: %s", script)
            result = subprocess.run(
                [sys.executable, script], cwd="/app", env=env, check=False
            )
            if result.returncode != 0:
                logger.warning(
                    "Dedup script %s exited with code %d", script, result.returncode
                )
    # Sync dedup reports to S3 (only entity dirs, not book dirs)
    entity_dirs = ["people", "people_groups", "places", "equipment"]
    for subdir in entity_dirs:
        d = WORKDIR / "output" / subdir
        report = d / "duplicate_report.json"
        if report.exists():
            _s3_client().upload_file(
                str(report), BUCKET, f"output/{subdir}/duplicate_report.json"
            )
    logger.info("Uploaded dedup reports to S3")


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
        logger.info("Wrote manifest: %d keys", len(keys))
    except Exception as e:
        logger.warning("Failed to write manifest: %s", e)


def _read_manifest() -> list:
    """Read changed file manifest from DynamoDB. Returns empty list if none."""
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        resp = table.get_item(Key={"cache_key": "manifest#phase2"})
        item = resp.get("Item")
        if item:
            keys = item.get("keys", [])
            logger.info("Read manifest: %d keys", len(keys))
            return keys
    except Exception as e:
        logger.warning("Failed to read manifest: %s", e)
    return []


def _final_sync(phase_script: str = ""):
    """Final upload of new output to S3. Only uploads entity subdirs, not parsed/event files."""
    if "phase1" in phase_script:
        # Phase 1: upload everything (parsed files trigger Phase 2)
        d = WORKDIR / "output"
        if d.exists():
            n, _ = s3_sync_up(d, "output")
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
        for subdir in entity_dirs:
            d = WORKDIR / "output" / subdir
            if d.exists():
                n, keys = s3_sync_up(d, f"output/{subdir}", skip_keys=_downloaded_keys)
                total += n
                all_uploaded.extend(keys)
        logger.info("Final sync: uploaded %d entity files", total)

        # Write manifest to DynamoDB so next phase only downloads changed files
        if all_uploaded and "phase2" in phase_script:
            _write_manifest(all_uploaded)
    elif "import" in phase_script:
        pass  # import doesn't produce output


PHASE_NAMES = {
    "phase1_parse.py": "Phase 1 (Parse)",
    "phase2_extract.py": "Phase 2 (Extract)",
    "phase3_enrich_data.py": "Phase 3 (Enrich)",
    "import_to_dynamodb.py": "Import",
}

PHASE_SUFFIXES = {
    "phase1_parse.py": "phase1-parse",
    "phase2_extract.py": "phase2-extract",
    "phase3_enrich_data.py": "phase3-enrich",
    "import_to_dynamodb.py": "import",
}


def _notify_complete(phase_script: str) -> None:
    """Publish completion notification to SNS."""
    topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
    if not topic_arn:
        return
    phase_name = PHASE_NAMES.get(phase_script, phase_script)
    dedup_url = os.environ.get("DEDUP_REVIEW_URL", "")
    message = f"{phase_name} completed successfully.\nBucket: {BUCKET}"
    if "phase2" in phase_script and dedup_url:
        message += (
            f"\n\nPhase 3 is blocked until you review duplicates."
            f"\nDedup Review UI: {dedup_url}"
        )
    try:
        sns = boto3.client("sns", region_name=REGION)
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"WWII Pipeline: {phase_name} complete",
            Message=message,
        )
        logger.info("Sent completion notification for %s", phase_name)
    except Exception as e:
        logger.warning("Failed to send notification: %s", e)


def _acquire_lock(phase_script: str) -> bool:
    """Acquire a DynamoDB lock for this phase. Returns True if acquired."""
    family_suffix = PHASE_SUFFIXES.get(phase_script)
    if not family_suffix:
        return True
    env_name = os.environ.get("ENV_NAME", "dev")
    lock_key = f"lock#{env_name}-wwii-{family_suffix}"
    try:
        import time

        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        table.put_item(
            Item={
                "cache_key": lock_key,
                "response": str(int(time.time())),
                "ttl": int(time.time()) + 86400,
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    except Exception as e:
        logger.warning("Lock check failed: %s, proceeding anyway", e)
        return True


def _remove_lock(phase_script: str) -> None:
    """Remove the DynamoDB lock for this phase."""
    family_suffix = PHASE_SUFFIXES.get(phase_script)
    if not family_suffix:
        return
    env_name = os.environ.get("ENV_NAME", "dev")
    lock_key = f"lock#{env_name}-wwii-{family_suffix}"
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        table.delete_item(Key={"cache_key": lock_key})
        logger.info("Removed lock: %s", lock_key)
    except Exception as e:
        logger.warning("Failed to remove lock %s: %s", lock_key, e)


def _clear_all_locks() -> None:
    """Clear all pipeline locks at start of new run."""
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        resp = table.scan(
            FilterExpression="begins_with(cache_key, :prefix)",
            ExpressionAttributeValues={":prefix": "lock#"},
            ProjectionExpression="cache_key",
        )
        for item in resp.get("Items", []):
            table.delete_item(Key={"cache_key": item["cache_key"]})
            logger.info("Cleared lock: %s", item["cache_key"])
    except Exception as e:
        logger.warning("Failed to clear locks: %s", e)

    # Reset dedup review status so Phase 3 requires approval
    try:
        s3 = _s3_client()
        s3.put_object(
            Bucket=BUCKET,
            Key="dedup/review_status.json",
            Body=json.dumps({"complete": False, "reviewed": {}}).encode(),
        )
        logger.info("Reset dedup review status")
    except Exception as e:
        logger.warning("Failed to reset dedup status: %s", e)

    # Clear stale manifest
    try:
        table.delete_item(Key={"cache_key": "manifest#phase2"})
        logger.info("Cleared manifest")
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ecs_entrypoint.py <phase_script> [args...]")
        sys.exit(1)
    run_phase(sys.argv[1], sys.argv[2:])
