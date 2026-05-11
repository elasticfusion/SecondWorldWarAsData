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


def _get_openserp_alb_dns() -> str:
    """Find the OpenSERP task private IP. Returns empty string if not found."""
    try:
        env = os.environ.get("ENV_NAME", "dev")
        cluster = f"{env}-wwii-pipeline"
        ecs = boto3.client("ecs", region_name=REGION)
        tasks = ecs.list_tasks(
            cluster=cluster, serviceName=f"{env}-wwii-openserp", desiredStatus="RUNNING"
        )
        task_arns = tasks.get("taskArns", [])
        if not task_arns:
            logger.warning("OpenSERP IP discovery: no running tasks found")
            return ""
        resp = ecs.describe_tasks(cluster=cluster, tasks=task_arns[:1])
        for task in resp.get("tasks", []):
            for attachment in task.get("attachments", []):
                for detail in attachment.get("details", []):
                    if detail.get("name") == "privateIPv4Address":
                        return detail["value"]
        logger.debug(
            "OpenSERP IP discovery: task found but no privateIPv4Address in attachments"
        )
    except Exception as e:
        logger.warning("OpenSERP IP discovery failed: %s", e)
    return ""


def _start_openserp_if_needed(phase_script: str) -> None:
    """Scale OpenSERP service to 1 for Phase 2/3 and discover its IP."""
    if "phase1" in phase_script:
        return
    try:
        env = os.environ.get("ENV_NAME", "dev")
        cluster = f"{env}-wwii-pipeline"
        service = f"{env}-wwii-openserp"
        ecs = boto3.client("ecs", region_name=REGION)
        resp = ecs.describe_services(cluster=cluster, services=[service])
        svc = resp.get("services", [{}])[0]
        if svc.get("runningCount", 0) > 0:
            # Already running — just discover IP
            ip = _get_openserp_alb_dns()
            if ip:
                _patch_openserp_url(ip)
                logger.info("OpenSERP already running at %s:7001", ip)
            return

        if svc.get("desiredCount", 0) == 0:
            # Ensure VPC endpoints exist (needed for ECR image pull)
            try:
                boto3.client("lambda", region_name=REGION).invoke(
                    FunctionName=f"{env}-wwii-nat-manager",
                    InvocationType="RequestResponse",
                    Payload=b'{"action": "create"}',
                )
            except Exception as e:
                logger.warning("nat_manager invoke failed: %s", e)

            ecs.update_service(cluster=cluster, service=service, desiredCount=1)
            logger.info("Started OpenSERP service (scaling from 0 to 1)")
        else:
            # Service already desired=1 but not running — force new deployment
            ecs.update_service(
                cluster=cluster,
                service=service,
                desiredCount=1,
                forceNewDeployment=True,
            )
            logger.info("Forced new OpenSERP deployment (was stuck)")

        # Wait for task to be running and get its IP
        import time

        for _ in range(72):  # 12 min max
            ip = _get_openserp_alb_dns()
            if ip:
                _patch_openserp_url(ip)
                logger.info("OpenSERP running at %s:7001", ip)
                return
            time.sleep(10)
        logger.warning("OpenSERP service did not start in 12 min")
    except Exception as e:
        logger.warning("Failed to start OpenSERP: %s", e)


def _patch_openserp_url(ip: str) -> None:
    """Patch config.yaml with OpenSERP task IP."""
    import yaml

    config_path = Path("/app/config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config.setdefault("external_maps", {})["openserp_url"] = f"http://{ip}:7001"
    config.setdefault("supplemental_material", {})["use_openserp"] = True
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)


def run_phase(phase_script: str, extra_args: list) -> None:
    """Run a pipeline phase script with incremental S3 sync."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    _load_secrets()
    _patch_config()
    _start_openserp_if_needed(phase_script)

    if "phase1" in phase_script:
        _clear_all_locks()

    _download_inputs(phase_script)
    _setup_symlinks()

    if "phase3" in phase_script:
        _stamp_schema_versions()
        _reset_openserp_searched()

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

    # Clear manifest before Phase 1 upload so the trigger Lambda's new
    # manifest entries (from S3 notifications) aren't wiped after the fact
    if "phase1" in phase_script:
        _clear_manifest()

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
        # Phase 3 needs full entity directories for enrichment
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
        total = 0
        for subdir in entity_dirs:
            d, s = _download_s3_prefix_skip_existing(s3, f"output/{subdir}/")
            total += d
        logger.info("Downloaded %d entity files for Phase 3", total)


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
        logger.info("No manifest found, falling back to S3 scan")
        existing_events = _list_s3_keys_matching(s3, "output/content/", "-event.json")
        new_parsed = _download_new_parsed(s3, existing_events)
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


def _download_new_parsed(s3, existing_events: set) -> int:
    """Download parsed files without corresponding event files."""
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix="output/content/"
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


def _post_process(phase_script: str, env: dict) -> None:
    """Run post-processing steps after a successful phase."""
    if "phase2" in phase_script:
        _run_dedup_detection(env)
        _check_pending_content()
    if "phase3" not in phase_script:
        _remove_lock(phase_script)
    _stop_openserp_if_running(phase_script)
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
    # Reclassify military units from places to groups
    try:
        from scripts.reclassify_military_units import reclassify

        reclassify(WORKDIR / "output")
    except Exception as e:
        logger.warning("Military unit reclassification failed: %s", e)

    # Clean stale index entries before dedup
    try:
        from scripts.cleanup_indexes import cleanup_index

        for entity, name_field in [
            ("people", "name"),
            ("people_groups", "group_name"),
            ("places", "current_name"),
            ("equipment", "common_name"),
        ]:
            entity_dir = WORKDIR / "output" / entity
            if entity_dir.exists():
                cleanup_index(entity_dir, name_field, dry_run=False)
    except Exception as e:
        logger.warning("Index cleanup failed: %s", e)

    # Migrate local exclusion files to DynamoDB (one-time, idempotent)
    try:
        from src.dedup.exclusions import migrate_local_to_dynamo

        s3_migrate = _s3_client()
        migration_files = [
            "output/people/not_duplicates.json",
            "output/places/not_duplicates.json",
            "output/people_groups/not_related.json",
            "output/equipment/not_duplicates.json",
        ]
        for key in migration_files:
            _download_s3_file(s3_migrate, key)
        for entity, subdir in [
            ("people", "people"),
            ("places", "places"),
            ("groups", "people_groups"),
            ("equipment", "equipment"),
        ]:
            entity_dir = WORKDIR / "output" / subdir
            if entity_dir.exists():
                migrate_local_to_dynamo(entity, entity_dir)
    except Exception as e:
        logger.warning("Exclusion migration failed: %s", e)

    # Download index.json files from S3 for cross-book name matching.
    # Exclusions are now in DynamoDB — no file download needed.
    s3 = _s3_client()
    dedup_files = [
        "output/people/index.json",
        "output/people_groups/index.json",
        "output/places/index.json",
        "output/equipment/index.json",
    ]
    for key in dedup_files:
        _download_s3_file(s3, key)
    # Download event files for cross-book text proximity matching
    d, s = _download_s3_prefix_skip_existing(s3, "output/content/")
    logger.info(
        "Dedup: downloaded index/exclusion files + %d event files (%d skipped)",
        d,
        s,
    )

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


def _reset_openserp_searched() -> None:
    """Reset openserp_searched on files that were searched but got no results."""
    output = Path("/app/output")
    skip = {"index.json", "duplicate_report.json", "not_duplicates.json"}
    reset = 0
    for d in [output / "people", output / "equipment"]:
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            if f.name in skip:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if (
                    data.get("openserp_searched")
                    and not data.get("images")
                    and not data.get("military_awards")
                ):
                    del data["openserp_searched"]
                    f.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    reset += 1
            except Exception:
                continue
    if reset:
        logger.info(
            "Reset openserp_searched on %d files (no results from previous run)", reset
        )


def _stamp_schema_versions() -> None:
    """Stamp _schema_version on all output files that need migration."""
    from src.schemas import SCHEMA_VERSION, inject_metadata, needs_migration

    # Quick check: if first file already has current version, skip entirely
    output = Path("/app/output")
    sample = next((output / "weather").glob("*.json"), None)
    if sample and sample.name != "index.json":
        try:
            if not needs_migration(json.loads(sample.read_text(encoding="utf-8"))):
                logger.info(
                    "Schema migration: already at v%s, skipping", SCHEMA_VERSION
                )
                return
        except Exception:
            pass

    env = os.environ.get("ENV_NAME", "dev")
    trigger_fn = f"{env}-wwii-trigger"
    lam = boto3.client("lambda", region_name=REGION)

    # Disable trigger Lambda during stamping
    try:
        lam.put_function_concurrency(
            FunctionName=trigger_fn, ReservedConcurrentExecutions=0
        )
        logger.info("Disabled trigger Lambda for schema migration")
    except Exception as e:
        logger.warning("Could not disable trigger Lambda: %s", e)

    output = Path("/app/output")
    dirs = [
        "weather",
        "people",
        "people_groups",
        "places",
        "equipment",
        "dates",
        "casualties",
        "logistics",
        "maps",
        "bibliography",
    ]
    skip = {
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_related.json",
        "review_queue.json",
        ".processed_events.json",
    }
    updated = 0
    for d in dirs:
        p = output / d
        if not p.exists():
            continue
        for f in p.glob("*.json"):
            if f.name in skip:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if needs_migration(data):
                    inject_metadata(data)
                    f.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    updated += 1
            except Exception:
                continue
    # Event files
    content = output / "content"
    if content.exists():
        for f in content.rglob("*-event.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if needs_migration(data):
                    inject_metadata(data)
                    f.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    updated += 1
            except Exception:
                continue
    logger.info("Schema migration: stamped %d files to v%s", updated, SCHEMA_VERSION)

    # Re-enable trigger Lambda
    try:
        lam.delete_function_concurrency(FunctionName=trigger_fn)
        logger.info("Re-enabled trigger Lambda")
    except Exception as e:
        logger.warning("Could not re-enable trigger Lambda: %s", e)


def _stop_openserp_if_running(phase_script: str) -> None:
    """Scale OpenSERP to 0 after Phase 2/3 completes."""
    if "phase1" in phase_script:
        return
    try:
        env = os.environ.get("ENV_NAME", "dev")
        cluster = f"{env}-wwii-pipeline"
        service = f"{env}-wwii-openserp"
        ecs = boto3.client("ecs", region_name=REGION)
        ecs.update_service(cluster=cluster, service=service, desiredCount=0)
        logger.info("Scaled OpenSERP to 0")
    except Exception as e:
        logger.warning("Failed to scale OpenSERP to 0: %s", e)


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
