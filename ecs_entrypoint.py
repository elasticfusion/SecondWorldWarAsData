"""ECS entrypoint — run pipeline phase with local filesystem, sync to S3 incrementally."""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import boto3

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s"
)
# Use structured JSON logging in ECS for CloudWatch Logs Insights
if os.environ.get("ECS_CONTAINER_METADATA_URI"):
    from src.utils.json_logging import configure_json_logging

    configure_json_logging()
    # Resolve ECS task ID for log correlation
    _meta_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4", "")
    if _meta_uri and not os.environ.get("ECS_TASK_ID"):
        try:
            import requests as _req

            _task_meta = _req.get(f"{_meta_uri}/task", timeout=2).json()
            os.environ["ECS_TASK_ID"] = _task_meta.get("TaskARN", "").split("/")[-1]
        except Exception:
            pass
logger = logging.getLogger(__name__)

BUCKET = os.environ["S3_BUCKET"]
REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1"))
WORKDIR = Path("/tmp/pipeline")
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "120"))  # seconds

# Global state for SIGTERM handler
_current_phase_script = ""


def _handle_sigterm(_signum, _frame):
    """Emergency sync on spot termination — 30 second window."""
    logger.warning("SIGTERM received — performing emergency S3 sync")
    try:
        _final_sync(_current_phase_script)
        _remove_lock(_current_phase_script)
        logger.info("Emergency sync complete — exiting cleanly")
    except Exception as e:
        logger.error("Emergency sync failed: %s", e)
    sys.exit(143)


signal.signal(signal.SIGTERM, _handle_sigterm)


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


def _get_account_id() -> str:
    """Get AWS account ID from STS."""
    return boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]


_downloaded_keys: set = set()
_deleted_keys: set = (
    set()
)  # Files deleted locally (merged) — remove from S3 in final sync


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


class BackgroundSync:
    """Periodically sync output and cache dirs to S3. Includes watchdog."""

    # Max time with no uploads before self-terminating (4 hours)
    WATCHDOG_TIMEOUT = int(os.environ.get("WATCHDOG_TIMEOUT", "14400"))

    def __init__(self, interval: int = 120):
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._uploaded_mtimes: dict = {}  # key → mtime of last upload
        self._last_activity_time = __import__("time").time()

    def ping(self):
        """Signal activity to the watchdog (call from heartbeat or any progress)."""
        self._last_activity_time = __import__("time").time()

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
                    n = self._sync_changed(d, prefix)
                    if n:
                        self._last_activity_time = __import__("time").time()
                        logger.info("Background sync: uploaded %d %s files", n, name)
            # Watchdog: self-terminate if no activity for too long
            import time as _t

            idle = _t.time() - self._last_activity_time
            if idle > self.WATCHDOG_TIMEOUT:
                logger.error(
                    "WATCHDOG: No activity for %.0f minutes — task appears stuck. Terminating.",
                    idle / 60,
                )
                self._stop.set()
                try:
                    _notify_failure(_current_phase_script, -1)
                except Exception:
                    pass
                os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            logger.warning("Background sync error: %s", e)

    def _sync_changed(self, local_dir: Path, prefix: str) -> int:
        """Upload only files modified since last sync."""
        s3 = _s3_client()
        exclude = [
            "-parsed.json"
        ]  # Keep parsed excluded (triggers Phase 2 via S3 notification)
        count = 0
        for f in local_dir.rglob("*"):
            if not f.is_file():
                continue
            if any(pat in f.name for pat in exclude):
                continue
            key = f"{prefix}/{f.relative_to(local_dir)}"
            if key in _downloaded_keys:
                continue
            mtime = f.stat().st_mtime
            if self._uploaded_mtimes.get(key) == mtime:
                continue
            s3.upload_file(str(f), BUCKET, key)
            self._uploaded_mtimes[key] = mtime
            count += 1
        return count


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
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            import shutil

            shutil.rmtree(link)
        elif link.exists():
            link.unlink()
        link.symlink_to(target)


def _cancel_stale_teardown() -> None:
    """Cancel any pending delayed networking teardown from a previous task."""
    try:
        env = os.environ.get("ENV_NAME", "dev")
        scheduler = boto3.client("scheduler", region_name=REGION)
        scheduler.delete_schedule(Name=f"{env}-wwii-delayed-teardown")
        logger.info("Cancelled stale delayed teardown")
    except Exception:
        pass  # No schedule exists — normal


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
    config.setdefault("storage", {})["entity_backend"] = "dynamodb"

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


def _preflight_credit_check() -> None:
    """Verify Grok API credits are available by making a minimal request."""
    api_key = os.environ.get("GROK_API_KEY", "")
    if not api_key:
        return
    try:
        import requests as _req

        resp = _req.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "grok-3-mini-fast",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
            timeout=15,
        )
        if resp.status_code == 402 or (
            resp.status_code == 403 and "credit" in resp.text.lower()
        ):
            logger.error("PREFLIGHT FAILED: Grok API credits depleted. Aborting.")
            sys.exit(1)
        if resp.status_code == 401:
            logger.error("PREFLIGHT FAILED: Invalid API key. Aborting.")
            sys.exit(1)
        resp.raise_for_status()
        logger.info("Preflight OK: Grok API credits available")
    except SystemExit:
        raise
    except Exception as e:
        logger.warning("Preflight check failed (proceeding anyway): %s", e)


def run_phase(phase_script: str, extra_args: list) -> None:
    """Run a pipeline phase script with incremental S3 sync."""
    global _current_phase_script
    _current_phase_script = phase_script
    phase_name = Path(phase_script).stem
    WORKDIR.mkdir(parents=True, exist_ok=True)

    # Cancel any stale delayed teardown from a previous task
    _cancel_stale_teardown()

    logger.info("[step] %s: loading secrets", phase_name)
    _load_secrets()
    logger.info("[step] %s: preflight credit check", phase_name)
    _preflight_credit_check()
    _patch_config()
    _start_openserp_if_needed(phase_script)

    if "phase1" in phase_script:
        _prepare_phase1()

    _setup_symlinks()
    logger.info("[step] %s: downloading inputs from S3", phase_name)
    _download_inputs(phase_script)

    if "phase3" in phase_script:
        _stamp_schema_versions()
        _reset_openserp_searched()

    sync = BackgroundSync(SYNC_INTERVAL)
    if "phase2" in phase_script or "phase3" in phase_script:
        sync.start()

    # Delegate to submit-only if batch mode enabled for this phase
    if _should_use_batch_mode(phase_script):
        sync.stop()
        run_submit_only(phase_script, extra_args)
        return

    env = os.environ.copy()
    env["PIPELINE_PHASE"] = phase_name
    cmd = [sys.executable, phase_script] + extra_args
    logger.info("[step] %s: running extraction", phase_name)
    logger.info(
        "Phase started: %s",
        phase_name,
        extra={"extra_fields": {"event": "phase_start", "phase": phase_name}},
    )
    import time as _time

    _phase_start = _time.monotonic()
    result = subprocess.run(cmd, cwd="/app", env=env, check=False)
    _phase_duration = _time.monotonic() - _phase_start

    sync.stop()

    if result.returncode != 0:
        logger.error(
            "Phase failed: %s (code %d, %.0fs)",
            phase_name,
            result.returncode,
            _phase_duration,
            extra={
                "extra_fields": {
                    "event": "phase_failed",
                    "phase": phase_name,
                    "returncode": result.returncode,
                    "duration_s": round(_phase_duration),
                }
            },
        )
        _notify_failure(phase_script, result.returncode)
        _final_sync(phase_script)
        if "phase3" not in phase_script:
            _remove_lock(phase_script)
        sys.exit(result.returncode)

    logger.info(
        "Phase complete: %s (%.0fs)",
        phase_name,
        _phase_duration,
        extra={
            "extra_fields": {
                "event": "phase_complete",
                "phase": phase_name,
                "duration_s": round(_phase_duration),
            }
        },
    )

    if "phase1" in phase_script:
        _clear_manifest()

    logger.info("[step] %s: final S3 sync", phase_name)
    _final_sync(phase_script)
    _post_process(phase_script, env)


def _prepare_phase1() -> None:
    """Phase 1 pre-processing: clear own lock and reset dedup status."""
    _remove_lock(_current_phase_script)
    try:
        _s3_client().put_object(
            Bucket=BUCKET,
            Key="dedup/review_status.json",
            Body=json.dumps({"complete": False, "reviewed": {}}).encode(),
        )
        logger.info("Reset dedup review status")
    except Exception as e:
        logger.warning("Failed to reset dedup status: %s", e)


def _should_use_batch_mode(phase_script: str) -> bool:
    """Check if batch mode is enabled for this phase in config."""
    import yaml

    try:
        with open("/app/config.yaml") as f:
            cfg = yaml.safe_load(f)
        batch_cfg = cfg.get("batch", {})
        phase_key = (
            "phase2"
            if "phase2" in phase_script
            else "phase3" if "phase3" in phase_script else ""
        )
        if phase_key and batch_cfg.get(phase_key, False):
            logger.info("Batch mode enabled for %s — using submit-only flow", phase_key)
            return True
    except Exception:
        pass
    return False


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
        _download_phase1_inputs()
    elif "phase2" in phase_script:
        n = _download_phase2_inputs()
        logger.info("Downloaded %d files for Phase 2", n)
    elif "phase3" in phase_script or "import" in phase_script:
        if not _materialize_from_dynamo():
            _download_phase3_from_s3()


def _download_phase1_inputs() -> None:
    """Download content files for Phase 1 (incremental or full)."""
    keys = _read_s3_manifest("contentrepository/")
    if keys:
        n = _download_keys(keys, Path("/app"))
        logger.info("Downloaded %d content files (incremental)", n)
        os.environ["_PHASE1_MODE"] = f"Incremental: {n} new files"
        return

    book_name = os.environ.get("BOOK_NAME", "")
    prefix = f"contentrepository/{book_name}/" if book_name else "contentrepository/"
    if not book_name:
        logger.warning("No manifest and no BOOK_NAME — downloading ALL content")

    n = s3_sync_down(prefix, Path("/app"))

    # Case-insensitive fallback if exact prefix found nothing
    if n == 0 and book_name:
        prefix = _find_book_prefix_case_insensitive(book_name) or prefix
        if prefix != f"contentrepository/{book_name}/":
            n = s3_sync_down(prefix, Path("/app"))

    logger.info("Downloaded %d content files (full, prefix=%s)", n, prefix)
    os.environ["_PHASE1_MODE"] = f"Full re-parse: {n} files"


def _find_book_prefix_case_insensitive(book_name: str) -> str:
    """Find S3 prefix with case-insensitive book name match."""
    s3 = _s3_client()
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="contentrepository/", Delimiter="/")
    for cp in resp.get("CommonPrefixes", []):
        folder = cp["Prefix"].rstrip("/").split("/")[-1]
        if folder.lower() == book_name.lower() and folder != book_name:
            logger.warning("Book name case mismatch: %s → %s", book_name, folder)
            return f"contentrepository/{folder}/"
    return ""


def _download_phase3_from_s3() -> None:
    """Fallback: download entity files from S3 for Phase 3."""
    entity_dirs = [
        "people", "people_groups", "places", "dates", "equipment",
        "weather", "logistics", "casualties", "maps", "supplemental", "bibliography",
    ]
    s3 = _s3_client()
    book_name = os.environ.get("BOOK_NAME", "")
    total = 0

    if book_name:
        logger.info("Scoped download for book: %s", book_name)
        referenced = _get_book_entity_files(s3, book_name)
        for subdir in entity_dirs:
            (WORKDIR / "output" / subdir).mkdir(parents=True, exist_ok=True)
            _download_s3_file(s3, f"output/{subdir}/index.json")
            subdir_files = [f for f in referenced if f.startswith(f"output/{subdir}/")]
            for key in subdir_files:
                _download_s3_file(s3, key)
            total += len(subdir_files)
        logger.info("Downloaded %d entity files (scoped to %s)", total, book_name)
    else:
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


def _post_process(phase_script: str, env: dict) -> None:
    """Run post-processing steps after a successful phase."""
    if "phase1" in phase_script:
        # Clear pending content queue — Phase 1 has processed it
        try:
            table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
            table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
            table.delete_item(Key={"cache_key": "pending#content"})
            logger.info("Cleared pending#content queue")
        except Exception:
            pass
        # Trigger Phase 2 directly (don't rely on S3 notification chain)
        try:
            book_name = os.environ.get("BOOK_NAME", "")
            lambda_client = boto3.client("lambda", region_name=REGION)
            env_name = os.environ.get("ENV_NAME", "dev")
            payload = json.dumps({"source": "manual", "book": book_name, "phase": "2"})
            lambda_client.invoke(
                FunctionName=f"{env_name}-wwii-trigger",
                InvocationType="Event",
                Payload=payload.encode(),
            )
            logger.info("Triggered Phase 2 for book=%s", book_name)
        except Exception as e:
            logger.warning("Failed to trigger Phase 2: %s", e)
    if "phase2" in phase_script:
        dedup_ok = False
        for attempt in range(2):
            try:
                _run_dedup_detection(env)
                dedup_ok = True
                break
            except Exception as e:
                logger.error(
                    "Dedup detection failed (attempt %d/2): %s", attempt + 1, e
                )
        if not dedup_ok:
            logger.error(
                "Dedup detection failed after 2 attempts — sending notification anyway"
            )
        # Auto-trigger Phase 3 if no duplicates need review
        if dedup_ok and _dedup_has_no_pending():
            logger.info("No duplicates found — auto-triggering Phase 3")
            try:
                env_name = os.environ.get("ENV_NAME", "dev")
                book_name = os.environ.get("BOOK_NAME", "")
                lambda_client = boto3.client("lambda", region_name=REGION)
                payload = json.dumps(
                    {"source": "manual", "book": book_name, "phase": "3"}
                )
                lambda_client.invoke(
                    FunctionName=f"{env_name}-wwii-trigger",
                    InvocationType="Event",
                    Payload=payload.encode(),
                )
            except Exception as e:
                logger.warning("Failed to auto-trigger Phase 3: %s", e)
        else:
            _schedule_delayed_teardown()
        _check_pending_content()
    if "phase3" not in phase_script:
        _remove_lock(phase_script)
    _stop_openserp_if_running(phase_script)
    _notify_complete(phase_script)


def _check_pending_content() -> None:
    """Check DynamoDB for queued content and re-trigger Phase 1 if found."""
    logger.info("Checking DynamoDB for pending content")
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        resp = table.get_item(Key={"cache_key": "pending#content"})
        item = resp.get("Item")
        if not item or not item.get("keys"):
            logger.info("No pending content in DynamoDB")
            return
        keys = item["keys"]
        logger.info("Found %d pending content files, re-triggering pipeline", len(keys))
        # Write keys as S3 manifest for Phase 1
        _s3_client().put_object(
            Bucket=BUCKET,
            Key="manifests/pending.json",
            Body=json.dumps(list(keys)).encode(),
        )
        # Trigger Phase 1 — publish BEFORE deleting pending entry
        topic_arn = os.environ.get("CONTENT_TOPIC_ARN", "")
        if topic_arn:
            sns = boto3.client("sns", region_name=REGION)
            sns.publish(TopicArn=topic_arn, Message=json.dumps({"pending": True}))
            logger.info("Re-triggered pipeline for pending content")
        # Only delete after successful publish
        table.delete_item(Key={"cache_key": "pending#content"})
    except Exception as e:
        logger.warning("Failed to check pending content: %s", e)


def _dedup_has_no_pending() -> bool:
    """Check if dedup reports have zero duplicates requiring review."""
    for subdir in ["people", "people_groups", "places", "equipment"]:
        report = WORKDIR / "output" / subdir / "duplicate_report.json"
        if report.exists():
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
                if data.get("duplicate_groups", 0) > 0:
                    return False
            except Exception:
                return False
    return True


def _run_dedup_detection(env: dict) -> None:
    """Run duplicate detection scripts after Phase 2."""
    from src.dedup.merge import set_deletion_callback

    set_deletion_callback(track_deletion)
    _reclassify_military_units()
    _cleanup_entity_indexes()
    _migrate_exclusions_to_dynamo()
    _download_dedup_data()
    _execute_dedup_scripts()


def _reclassify_military_units() -> None:
    """Reclassify military units from places to groups."""
    try:
        from scripts.reclassify_military_units import reclassify

        reclassify(WORKDIR / "output")
    except Exception as e:
        logger.warning("Military unit reclassification failed: %s", e)


def _cleanup_entity_indexes() -> None:
    """Clean stale index entries before dedup."""
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


def _migrate_exclusions_to_dynamo() -> None:
    """Migrate local exclusion files to DynamoDB (one-time, idempotent)."""
    try:
        from src.dedup.exclusions import migrate_local_to_dynamo

        logger.info("Migrating dedup exclusions to DynamoDB")
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


def _auto_merge_exact_duplicates() -> None:
    """Auto-merge entity pairs with identical normalized names (no human review needed)."""
    entity_configs = [
        ("people", "PersonID"),
        ("people_groups", "PeopleGroupID"),
        ("places", "PlaceID"),
        ("equipment", "EquipmentID"),
    ]
    total = 0
    for subdir, id_field in entity_configs:
        total += _auto_merge_entity_type(WORKDIR / "output" / subdir, id_field)
    if total:
        logger.info("Auto-merged %d exact duplicate entities", total)


def _auto_merge_entity_type(entity_dir: Path, id_field: str) -> int:
    """Auto-merge exact duplicates for one entity type. Returns merge count."""
    from src.dedup.merge import merge_generic
    from src.utils.text_utils import normalize_name_ascii

    report_file = entity_dir / "duplicate_report.json"
    if not report_file.exists():
        return 0
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    groups_key = "duplicate_groups" if "duplicate_groups" in report else "groups"
    groups = report.get(groups_key, [])
    remaining = []
    merged = 0

    for group in groups:
        people = group.get("people", [])
        names = [p.get("name", "") for p in people]
        normalized = {normalize_name_ascii(n) for n in names if n}
        if len(normalized) == 1 and len(people) >= 2:
            merge_generic(entity_dir, people, 0, id_field)
            merged += len(people) - 1
        else:
            remaining.append(group)

    report[groups_key] = remaining
    report_file.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return merged


def _extract_coords_from_place(data: dict) -> tuple:
    """Extract (lat, lon) from a place data dict. Returns (None, None) if missing."""
    lat = data.get("latitude") or data.get("lat")
    lon = data.get("longitude") or data.get("lon") or data.get("lng")
    if lat is None:
        c = data.get("coordinates", {})
        if isinstance(c, dict):
            lat = c.get("latitude") or c.get("lat")
            lon = c.get("longitude") or c.get("lon")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (ValueError, TypeError):
            pass
    return None, None


def _generate_places_coords() -> None:
    """Generate coords.json for cross-book place dedup distance matching."""
    places_dir = WORKDIR / "output" / "places"
    if not places_dir.exists():
        return
    skip = {"index.json", "coords.json", "duplicate_report.json", "not_duplicates.json"}
    coords: dict = {}
    for f in places_dir.glob("*.json"):
        if f.name in skip:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            lat, lon = _extract_coords_from_place(data)
            if lat is not None:
                coords[f.name] = {
                    "lat": lat,
                    "lon": lon,
                    "PlaceID": data.get("PlaceID", ""),
                }
        except (json.JSONDecodeError, OSError):
            pass
    coords_file = places_dir / "coords.json"
    coords_file.write_text(json.dumps(coords), encoding="utf-8")
    logger.info("Generated places coords.json (%d entries)", len(coords))

    # Also store in DynamoDB for cross-run persistence (per-place items)
    try:
        table_name = os.environ.get("CACHE_TABLE", "")
        if table_name:
            logger.info("Writing %d place coords to DynamoDB", len(coords))
            table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
            with table.batch_writer() as batch:
                for filename, c in coords.items():
                    batch.put_item(
                        Item={
                            "cache_key": f"place_coords#{filename}",
                            "lat": str(c["lat"]),
                            "lon": str(c["lon"]),
                            "PlaceID": c["PlaceID"],
                        }
                    )
            logger.info("Wrote %d place coords to DynamoDB", len(coords))
    except Exception as e:
        logger.warning("Failed to store coords in DynamoDB: %s", e)


def _execute_dedup_scripts() -> None:
    """Run dedup detection scripts and upload reports."""
    # Generate places coords cache for cross-book distance matching
    _generate_places_coords()

    dedup_scripts = [
        "scripts/find_duplicate_people.py",
        "scripts/find_duplicate_places_v2.py",
        "scripts/find_duplicate_groups.py",
        "scripts/find_duplicate_equipment.py",
    ]
    dedup_env = os.environ.copy()
    dedup_env["PYTHONPATH"] = "/app"
    for script in dedup_scripts:
        if Path(f"/app/{script}").exists():
            logger.info("Running dedup: %s", script)
            result = subprocess.run(
                [sys.executable, script], cwd="/app", env=dedup_env, check=False
            )
            if result.returncode != 0:
                logger.warning(
                    "Dedup script %s exited with code %d", script, result.returncode
                )
    # Auto-merge exact duplicates (identical normalized names) without human review
    _auto_merge_exact_duplicates()

    # Record dedup run timestamps for incremental mode
    from src.dedup.incremental import set_last_dedup_run

    for entity_type in ["people", "places", "groups", "equipment"]:
        set_last_dedup_run(entity_type)

    # Sync dedup reports to S3
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


def _stamp_file(filepath: Path, needs_migration, inject_metadata) -> int:
    """Stamp a single file if it needs migration. Returns 1 if stamped, 0 otherwise."""
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        if needs_migration(data):
            inject_metadata(data)
            import tempfile

            tmp_fd, tmp_path = tempfile.mkstemp(dir=filepath.parent, suffix=".tmp")
            try:
                os.fdopen(tmp_fd, "w", encoding="utf-8").write(
                    json.dumps(data, indent=2, ensure_ascii=False)
                )
                os.replace(tmp_path, filepath)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
            return 1
    except Exception:
        pass
    return 0


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

    try:
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
                updated += _stamp_file(f, needs_migration, inject_metadata)
        # Event files
        content = output / "content"
        if content.exists():
            for f in content.rglob("*-event.json"):
                updated += _stamp_file(f, needs_migration, inject_metadata)
        logger.info(
            "Schema migration: stamped %d files to v%s", updated, SCHEMA_VERSION
        )

    finally:
        # Re-enable trigger Lambda (always, even on crash)
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


def _notify_failure(phase_script: str, returncode: int) -> None:
    """Publish failure notification to SNS."""
    topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
    if not topic_arn:
        return
    phase_name = PHASE_NAMES.get(phase_script, phase_script)
    try:
        sns = boto3.client("sns", region_name=REGION)
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"WWII Pipeline: {phase_name} FAILED",
            Message=f"{phase_name} failed with exit code {returncode}.\nBucket: {BUCKET}\n\nCheck logs: aws logs tail /ecs/dev-wwii-pipeline --region us-east-1 --since 30m",
        )
    except Exception as e:
        logger.warning("Failed to send failure notification: %s", e)


def _notify_complete(phase_script: str) -> None:
    """Publish completion notification to SNS."""
    topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
    if not topic_arn:
        return
    phase_name = PHASE_NAMES.get(phase_script, phase_script)
    message = f"{phase_name} completed successfully.\nBucket: {BUCKET}"
    message += _build_results_section()
    message += _build_phase_section(phase_script)

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


def _build_results_section() -> str:
    """Build entity counts / processed stats from .phase_results.json."""
    results_file = WORKDIR / "output" / ".phase_results.json"
    if not results_file.exists():
        return ""
    try:
        results = json.loads(results_file.read_text(encoding="utf-8"))
    except Exception:
        return ""
    parts = []
    counts = results.get("entity_counts", {})
    if counts:
        parts.append("\n\nEntity counts:")
        parts.extend(f"\n  {t}: {c}" for t, c in sorted(counts.items()))
    if "processed" in results:
        parts.append(f"\n\nProcessed: {results['processed']}, Failed: {results.get('failed', 0)}")
    if "enriched" in results:
        parts.append(f"\n\nEnriched: {results['enriched']} items")
    return "".join(parts)


def _build_phase_section(phase_script: str) -> str:
    """Build phase-specific notification content."""
    parts = []
    if "phase1" in phase_script:
        mode = os.environ.get("_PHASE1_MODE", "")
        if mode:
            parts.append(f"\nMode: {mode}")
        content_dir = WORKDIR / "output" / "content"
        if content_dir.exists():
            parsed = sorted(f.name for f in content_dir.rglob("*-parsed.json"))
            if parsed:
                parts.append(f"\n\nParsed {len(parsed)} file(s):\n")
                parts.append("\n".join(f"  {f}" for f in parsed))
    elif "phase2" in phase_script:
        dedup_url = os.environ.get("DEDUP_REVIEW_URL", "")
        if dedup_url:
            parts.append(
                f"\n\nPhase 3 is blocked until you review duplicates."
                f"\nDedup Review UI: {dedup_url}"
            )
    elif "phase3" in phase_script:
        parts.append("\n\nPipeline run complete. All entities enriched.")
    return "".join(parts)


def _acquire_lock(phase_script: str) -> bool:
    """Acquire a DynamoDB lock for this phase. Returns True if acquired."""
    family_suffix = PHASE_SUFFIXES.get(phase_script)
    if not family_suffix:
        return True
    env_name = os.environ.get("ENV_NAME", "dev")
    lock_key = f"lock#{env_name}-wwii-{family_suffix}"
    logger.info("Acquiring DynamoDB lock: %s", lock_key)
    try:
        import time

        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        table.put_item(
            Item={
                "cache_key": lock_key,
                "response": str(int(time.time())),
                "ttl": int(time.time()) + 7200,  # 2h — auto-expire stale locks
            },
            ConditionExpression="attribute_not_exists(cache_key)",
        )
        logger.info("Acquired DynamoDB lock: %s", lock_key)
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.warning("DynamoDB lock already held: %s", lock_key)
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


def _schedule_delayed_teardown(delay_minutes: int = None) -> None:
    """Schedule networking teardown after a delay via EventBridge Scheduler.

    If Phase 3 launches before the delay expires, the trigger Lambda
    cancels this schedule. Avoids churn when dedup review is fast.
    """
    if delay_minutes is None:
        delay_minutes = int(os.environ.get("TEARDOWN_DELAY_MINUTES", "30"))
    import datetime

    env = os.environ.get("ENV_NAME", "dev")
    schedule_name = f"{env}-wwii-delayed-teardown"
    run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=delay_minutes
    )
    nat_fn_arn = (
        f"arn:aws:lambda:{REGION}:{_get_account_id()}:function:{env}-wwii-nat-manager"
    )
    role_arn = os.environ.get(
        "SCHEDULER_ROLE_ARN",
        f"arn:aws:iam::{_get_account_id()}:role/{env}-wwii-scheduler-role",
    )
    try:
        scheduler = boto3.client("scheduler", region_name=REGION)
    except Exception as e:
        logger.warning("Failed to create scheduler client: %s", e)
        return
    try:
        scheduler.create_schedule(
            Name=schedule_name,
            ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": nat_fn_arn,
                "RoleArn": role_arn,
                "Input": '{"action": "delete"}',
            },
            ActionAfterCompletion="DELETE",
        )
        logger.info("Scheduled networking teardown in %d minutes", delay_minutes)
    except scheduler.exceptions.ConflictException:
        # Schedule already exists — update it
        try:
            scheduler.update_schedule(
                Name=schedule_name,
                ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
                ScheduleExpressionTimezone="UTC",
                FlexibleTimeWindow={"Mode": "OFF"},
                Target={
                    "Arn": nat_fn_arn,
                    "RoleArn": role_arn,
                    "Input": '{"action": "delete"}',
                },
                ActionAfterCompletion="DELETE",
            )
            logger.info(
                "Updated delayed teardown schedule to %d minutes", delay_minutes
            )
        except Exception as e:
            logger.warning("Failed to update delayed teardown: %s", e)
    except Exception as e:
        logger.warning("Failed to schedule delayed teardown: %s", e)


def _teardown_networking() -> None:
    """Scale down OpenSERP and invoke nat_manager to delete NAT + VPC endpoints."""
    try:
        env = os.environ.get("ENV_NAME", "dev")
        ecs = boto3.client("ecs", region_name=REGION)
        ecs.update_service(
            cluster=f"{env}-wwii-pipeline",
            service=f"{env}-wwii-openserp",
            desiredCount=0,
        )
        logger.info("Scaled OpenSERP to 0")
    except Exception as e:
        logger.warning("Failed to scale OpenSERP: %s", e)
    try:
        env = os.environ.get("ENV_NAME", "dev")
        lam = boto3.client("lambda", region_name=REGION)
        lam.invoke(
            FunctionName=f"{env}-wwii-nat-manager",
            InvocationType="RequestResponse",
            Payload=json.dumps({"action": "delete"}).encode(),
        )
        logger.info("Networking torn down (NAT + VPC endpoints)")
    except Exception as e:
        logger.warning("Failed to tear down networking: %s", e)


def run_submit_only(phase_script: str, extra_args: list) -> None:
    """Run phase in batch mode, submit to Grok, enqueue job, then exit immediately."""
    global _current_phase_script
    _current_phase_script = phase_script
    phase_name = Path(phase_script).stem
    if "--batch" not in extra_args:
        extra_args = ["--batch"] + extra_args

    WORKDIR.mkdir(parents=True, exist_ok=True)
    logger.info("[step] %s: loading secrets", phase_name)
    _load_secrets()
    logger.info("[step] %s: preflight credit check", phase_name)
    _preflight_credit_check()
    _patch_config()
    _start_openserp_if_needed(phase_script)

    _setup_symlinks()
    logger.info("[step] %s: downloading inputs from S3", phase_name)
    _download_inputs(phase_script)

    if "phase3" in phase_script:
        _stamp_schema_versions()
        _reset_openserp_searched()

    logger.info("[step] %s: submitting batch to Grok API", phase_name)
    # Monkey-patch poll_batch/retrieve_results so submit_batch returns after upload
    import src.utils.batch_api as _batch_mod

    _orig_poll = _batch_mod.poll_batch
    _orig_retrieve = _batch_mod.retrieve_results
    _batch_mod.poll_batch = lambda *a, **kw: {"_submit_only": True, "state": {}}
    _batch_mod.retrieve_results = lambda *a, **kw: {}

    sync = BackgroundSync(SYNC_INTERVAL)
    if "phase2" in phase_script or "phase3" in phase_script:
        sync.start()

    cmd = [sys.executable, phase_script] + extra_args
    logger.info("Running (submit-only): %s", " ".join(cmd))
    env = os.environ.copy()
    env["PIPELINE_PHASE"] = Path(phase_script).stem
    result = subprocess.run(cmd, cwd="/app", env=env, check=False)

    sync.stop()
    _batch_mod.poll_batch = _orig_poll
    _batch_mod.retrieve_results = _orig_retrieve

    # Enqueue the batch job (returns False if no batch was actually submitted)
    logger.info("[step] %s: enqueueing batch job", phase_name)
    batch_enqueued = _enqueue_from_metrics(phase_script)

    logger.info("[step] %s: final S3 sync", phase_name)
    _final_sync(phase_script)
    _stop_openserp_if_running(phase_script)

    if result.returncode != 0 and batch_enqueued:
        logger.error("Submit-only exited with code %d", result.returncode)
        sys.exit(result.returncode)

    # If no batch was submitted (all events cached), run non-batch to extract entities.
    # The batch submit only collects event requests; people/places/groups/optional
    # entities need live API calls which only happen in non-batch mode.
    if not batch_enqueued:
        logger.info(
            "[step] %s: no batch submitted (all cached) — running non-batch for entities",
            phase_name,
        )
        # Force-download parsed files (incremental logic skips them when events exist)
        os.environ["FORCE_DOWNLOAD_PARSED"] = "1"
        _download_inputs(phase_script)
        del os.environ["FORCE_DOWNLOAD_PARSED"]

        clean_args = [a for a in extra_args if a != "--batch"]
        env = os.environ.copy()
        env["PIPELINE_PHASE"] = Path(phase_script).stem
        cmd = [sys.executable, phase_script] + clean_args
        sync2 = BackgroundSync(SYNC_INTERVAL)
        sync2.start()
        result = subprocess.run(cmd, cwd="/app", env=env, check=False)
        sync2.stop()
        _final_sync(phase_script)
        if result.returncode != 0:
            logger.error("Non-batch entity run exited with code %d", result.returncode)
        _post_process(phase_script, os.environ.copy())
        _teardown_networking()
        logger.info("Non-batch entity run complete.")
        return

    # Tear down networking (NAT + VPC endpoints) — Lambda poller will recreate on completion
    logger.info("[step] %s: tearing down networking", phase_name)
    _teardown_networking()

    # Phase 3 does real work in submit-only mode — notify on completion
    if "phase3" in phase_script:
        _notify_complete(phase_script)

    logger.info("Submit-only complete — batch enqueued, infra torn down, exiting.")


def _enqueue_from_metrics(phase_script: str) -> bool:
    """Find the latest batch metrics and enqueue the job. Returns True if a batch was enqueued."""
    import time as _t

    from src.utils.job_queue import BatchJob, enqueue_job

    metrics_dir = WORKDIR / "output" / "metrics"
    if not metrics_dir.exists():
        logger.warning("No metrics dir — batch may not have submitted")
        return False

    files = sorted(metrics_dir.glob("batch_*.json"), key=lambda f: f.stat().st_mtime)
    if not files:
        logger.warning("No batch metrics files found")
        return False

    with open(files[-1]) as f:
        metrics = json.load(f)

    batch_id = metrics.get("batch_id", "")
    if not batch_id:
        logger.warning("No batch_id in %s", files[-1].name)
        return False

    phase = "phase2" if "phase2" in phase_script else "phase3"
    book = os.environ.get("BOOK_NAME", "unknown")
    request_count = metrics.get("total_requests", 0)

    # Dedup guard: skip if identical batch already pending or recently completed
    try:
        import time as _time

        from src.utils.job_queue import get_active_jobs, _get_table

        # Check pending jobs
        for existing in get_active_jobs():
            if (
                existing.phase == phase
                and existing.book == book
                and existing.request_count == request_count
            ):
                logger.warning(
                    "Skipping batch submission — identical job already pending "
                    "(batch_id=%s, book=%s, requests=%d)",
                    existing.batch_id,
                    book,
                    request_count,
                )
                return False

        # Check recently completed jobs (within last hour)
        one_hour_ago = int(_time.time()) - 3600
        table = _get_table()
        resp = table.scan(
            FilterExpression=(
                "begins_with(cache_key, :prefix) AND #s = :status "
                "AND completed_at > :since"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":prefix": "batch_job#",
                ":status": "complete",
                ":since": one_hour_ago,
            },
        )
        for item in resp.get("Items", []):
            if (
                item.get("phase") == phase
                and item.get("book") == book
                and int(item.get("request_count", 0)) == request_count
            ):
                logger.warning(
                    "Skipping batch submission — identical job completed recently "
                    "(batch_id=%s, book=%s, requests=%d)",
                    item.get("batch_id"),
                    book,
                    request_count,
                )
                return False
    except Exception as e:
        logger.debug("Dedup guard check failed (proceeding): %s", e)

    logger.info(
        "Enqueueing batch job to DynamoDB: batch_id=%s, phase=%s, book=%s, requests=%d",
        batch_id,
        phase,
        book,
        request_count,
    )
    enqueue_job(
        BatchJob(
            batch_id=batch_id,
            phase=phase,
            book=book,
            batch_name=metrics.get("batch_name", ""),
            submitted_at=int(_t.time()),
            status="pending",
            request_count=request_count,
        )
    )
    logger.info("Enqueued batch job to DynamoDB: %s", batch_id)
    _notify_batch_submitted(phase, book, batch_id, request_count)
    return True


def _notify_batch_submitted(
    phase: str, book: str, batch_id: str, request_count: int
) -> None:
    """Send SNS notification that batch was submitted."""
    try:
        topic_arn = os.environ.get("SNS_TOPIC_ARN", "")
        if not topic_arn:
            return
        sns = boto3.client("sns", region_name=REGION)
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"WWII Pipeline: {phase} batch submitted ({request_count} requests)",
            Message=(
                f"Batch submitted successfully.\n\n"
                f"Phase: {phase}\n"
                f"Book: {book}\n"
                f"Batch ID: {batch_id}\n"
                f"Requests: {request_count}\n\n"
                f"The batch poller will retrieve results when complete."
            ),
        )
    except Exception as e:
        logger.warning("Failed to send batch submitted notification: %s", e)


def run_retrieve_only(phase_script: str, extra_args: list, batch_id: str) -> None:
    """Retrieve completed batch results and re-run phase with cached data."""
    import re as _re

    from src.utils.batch_api import retrieve_results
    from src.utils.job_queue import get_job, update_job_status

    WORKDIR.mkdir(parents=True, exist_ok=True)
    _load_secrets()
    _patch_config()

    job = get_job(batch_id)
    if not job:
        logger.error("Job %s not found in queue", batch_id)
        sys.exit(1)

    logger.info(
        "Retrieving batch %s (%s/%s, %d reqs)",
        batch_id,
        job.phase,
        job.book,
        job.request_count,
    )

    # Scope downloads to the book being retrieved (avoid processing entire backlog)
    if job.book and job.book != "unknown":
        os.environ["BOOK_NAME"] = job.book
    os.environ["FORCE_DOWNLOAD_PARSED"] = "1"  # Retrieve needs all parsed files
    _setup_symlinks()
    _download_inputs(phase_script)
    # Download metrics so cache_type mapping is available for result classification
    _download_s3_prefix(_s3_client(), "output/metrics/")

    if "phase3" in phase_script:
        _stamp_schema_versions()

    # Retrieve and populate cache
    api_key = os.environ.get("GROK_API_KEY", "")
    results = retrieve_results(api_key, batch_id)
    logger.info("Retrieved %d results", len(results))

    # Load cache_type mapping from metrics
    cache_type_map = {}
    metrics_dir = WORKDIR / "output" / "metrics"
    for mf in sorted(
        metrics_dir.glob("batch_*.json"), key=lambda f: f.stat().st_mtime, reverse=True
    ):
        with open(mf) as f:
            metrics = json.load(f)
        if metrics.get("batch_id") == batch_id:
            for d in metrics.get("request_details", []):
                cache_type_map[d["request_id"]] = d.get("cache_type", "default")
            break

    # Populate cache
    from src.grok_client import GrokClient

    cache_dir = Path("/app/cache/api")
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir)

    populated = 0
    for request_id, br in results.items():
        if br.finish_reason == "stop" and br.content:
            cache_type = cache_type_map.get(request_id, "default")
            cache = grok_client._get_cache(cache_type)
            cache[request_id] = _re.sub(
                r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", br.content
            )
            populated += 1
    logger.info("Populated %d cache entries", populated)

    # Re-run phase without --batch (hits cache, skip retry since batch results are authoritative)
    clean_args = [a for a in extra_args if a != "--batch"]
    sync = BackgroundSync(SYNC_INTERVAL)
    sync.start()

    env = os.environ.copy()
    env["SKIP_RETRY"] = "1"
    env["PIPELINE_PHASE"] = Path(phase_script).stem
    cmd = [sys.executable, phase_script] + clean_args
    logger.info("Re-running with cached results: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd="/app", env=env, check=False)

    sync.stop()
    _final_sync(phase_script)

    if result.returncode != 0:
        logger.error("Retrieve phase exited with code %d", result.returncode)
        update_job_status(batch_id, "failed")
        sys.exit(result.returncode)

    update_job_status(batch_id, "retrieved")
    _post_process(phase_script, os.environ.copy())
    logger.info("Retrieve-only complete for batch %s", batch_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: ecs_entrypoint.py <phase_script> [args...]\n"
            "       ecs_entrypoint.py --submit-only <phase_script> [args...]\n"
            "       ecs_entrypoint.py --retrieve-only <batch_id> <phase_script> [args...]"
        )
        sys.exit(1)

    if sys.argv[1] == "--submit-only":
        run_submit_only(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "--retrieve-only":
        run_retrieve_only(sys.argv[3], sys.argv[4:], batch_id=sys.argv[2])
    else:
        run_phase(sys.argv[1], sys.argv[2:])
