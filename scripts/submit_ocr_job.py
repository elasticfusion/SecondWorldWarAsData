#!/usr/bin/env python3
"""Submit Chandra OCR jobs to AWS Batch.

Splits a PDF into page-range chunks, ensures networking is up,
submits parallel GPU jobs, and optionally waits for completion + merge.

Usage:
    python3 scripts/submit_ocr_job.py s3://dev-wwii-data-pipeline/source/ETO_Order_of_Battle.pdf
    python3 scripts/submit_ocr_job.py s3://dev-wwii-data-pipeline/source/book.pdf --chunk-size 25 --wait
    python3 scripts/submit_ocr_job.py s3://dev-wwii-data-pipeline/source/book.pdf --page-range 21-34
"""

import argparse
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

import boto3
import yaml

ENV = "dev"
REGION = None  # Set from --region flag or AWS_DEFAULT_REGION
JOB_QUEUE = None
JOB_DEF = None
NAT_MANAGER_FN = None
DATA_BUCKET = None

# OCR config (loaded from ocr_config.yaml)
OCR_CONFIG: dict = {}
_spot_wait_start: float | None = None  # Tracks when Spot waiting began


def _load_ocr_config() -> dict:
    """Load ocr_config.yaml from project root."""
    config_paths = [
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ocr_config.yaml",
        ),
        "ocr_config.yaml",
    ]
    for path in config_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def _init_config(region: str, env: str = "dev"):
    """Initialize global config from region and environment."""
    global REGION, JOB_QUEUE, JOB_DEF, NAT_MANAGER_FN, DATA_BUCKET, ENV, OCR_CONFIG
    ENV = env
    REGION = region
    JOB_QUEUE = f"{ENV}-wwii-chandra-gpu"
    JOB_DEF = f"{ENV}-wwii-chandra"
    NAT_MANAGER_FN = f"{ENV}-wwii-nat-manager"
    DATA_BUCKET = f"{ENV}-wwii-data-pipeline"
    OCR_CONFIG = _load_ocr_config()


def get_page_count(s3_path: str) -> int:
    """Download PDF and count pages."""
    from PyPDF2 import PdfReader  # pylint: disable=import-outside-toplevel

    s3 = boto3.client("s3", region_name=REGION)
    bucket, key = _parse_s3_path(s3_path)

    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        s3.download_file(bucket, key, tmp.name)
        reader = PdfReader(tmp.name)
        return len(reader.pages)


def _parse_s3_path(s3_path: str) -> tuple:
    """Parse s3://bucket/key into (bucket, key)."""
    path = s3_path.replace("s3://", "")
    bucket = path.split("/")[0]
    key = "/".join(path.split("/")[1:])
    return bucket, key


def ensure_networking():
    """Invoke nat_manager to create NAT + VPC endpoints for ECR pulls."""
    print("Ensuring networking (NAT + VPC endpoints)...")
    lam = boto3.client("lambda", region_name=REGION)
    resp = lam.invoke(
        FunctionName=NAT_MANAGER_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps({"action": "create"}).encode(),
    )
    result = json.loads(resp["Payload"].read())
    print(f"  Networking: {result.get('status', 'unknown')}")

    # Disable idle monitor so it doesn't tear down networking while jobs run
    _disable_idle_monitor()


def _spot_prices_all_too_close(ec2, on_demand_prices: dict, max_proximity: int) -> bool:
    """Print current Spot prices; return True if ALL are within max_proximity."""
    try:
        from datetime import datetime, timezone, timedelta

        start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        price_resp = ec2.describe_spot_price_history(
            InstanceTypes=["g4dn.xlarge", "g5.xlarge", "g6.xlarge"],
            ProductDescriptions=["Linux/UNIX"],
            StartTime=start_time,
        )
        prices = {}
        for p in price_resp.get("SpotPriceHistory", []):
            key = (p["InstanceType"], p["AvailabilityZone"])
            if key not in prices:
                prices[key] = p
        if not prices:
            return False

        print("  Current Spot Prices:")
        for (itype, az), p in sorted(prices.items()):
            spot_price = float(p["SpotPrice"])
            savings = int((1 - spot_price / on_demand_prices.get(itype, 1.0)) * 100)
            indicator = "✓" if savings >= 50 else "⚠" if savings >= 20 else "✗"
            print(
                f"    {indicator} {itype:14s} {az}: ${spot_price:.3f}/hr ({savings}% off)"
            )
        print()

        all_close = all(
            (1 - float(p["SpotPrice"]) / on_demand_prices.get(p["InstanceType"], 1.0))
            * 100
            <= max_proximity
            for p in prices.values()
        )
        if all_close:
            print(
                f"  ⚠ All Spot prices within {max_proximity}% of on-demand — "
                "recommending on-demand."
            )
        return all_close
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"  ⚠ Could not check Spot prices: {e}\n")
        return False


def _best_placement_az(ec2, min_score: int) -> "str | None":
    """Return the best-scoring AZ name, or None if no AZ meets ``min_score``."""
    try:
        resp = ec2.get_spot_placement_scores(
            InstanceTypes=[
                "g4dn.xlarge",
                "g4dn.2xlarge",
                "g5.xlarge",
                "g5.2xlarge",
                "g5.4xlarge",
                "g6.xlarge",
            ],
            TargetCapacity=1,
            RegionNames=[REGION],
            SingleAvailabilityZone=True,
        )
        scores = resp.get("SpotPlacementScores", [])
        if not scores:
            print("  ⚠ No placement scores returned")
            return None
        scores.sort(key=lambda s: s.get("Score", 0), reverse=True)

        print("  AZ Placement Scores:")
        for s in scores:
            az = s.get("AvailabilityZoneId", s.get("Region", "?"))
            score = s.get("Score", 0)
            indicator = "✓" if score >= 5 else "⚠" if score >= min_score else "✗"
            print(f"    {indicator} {az}: {score}/10")

        best = scores[0]
        best_az_id = best.get("AvailabilityZoneId", "")
        best_score = best.get("Score", 0)
        if best_score < min_score:
            print(
                f"\n  ⚠ Best Spot score is {best_score}/10 (min: {min_score}) — "
                "Spot unlikely to be fulfilled."
            )
            print(
                "    Recommendation: Use on-demand (Type: EC2 in cloudformation/ocr.yaml)"
            )
            return None
        az_name = _az_id_to_name(ec2, best_az_id)
        print(f"\n  Best AZ: {az_name} ({best_az_id}) — score {best_score}/10")
        return az_name
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"  ⚠ Could not check Spot placement: {e}")
        return None


def _check_spot_placement():
    """Check Spot prices + placement scores; return the best AZ or None."""
    print("\nChecking Spot availability...")
    ec2 = boto3.client("ec2", region_name=REGION)
    spot_config = OCR_CONFIG.get("spot", {})
    max_proximity = spot_config.get("max_price_proximity_percent", 10)
    min_score = spot_config.get("min_placement_score", 3)
    on_demand_prices = OCR_CONFIG.get(
        "on_demand_prices",
        {"g4dn.xlarge": 0.526, "g5.xlarge": 1.006, "g6.xlarge": 0.978},
    )

    if _spot_prices_all_too_close(ec2, on_demand_prices, max_proximity):
        return None
    return _best_placement_az(ec2, min_score)


def _az_id_to_name(ec2, az_id: str) -> str:
    """Convert AZ ID (use1-az2) to AZ name (us-east-1b)."""
    try:
        resp = ec2.describe_availability_zones(
            Filters=[{"Name": "zone-id", "Values": [az_id]}]
        )
        zones = resp.get("AvailabilityZones", [])
        if zones:
            return zones[0]["ZoneName"]
    except Exception:
        pass
    return az_id


def _target_az(az_name: str):
    """Update Batch compute environment to prefer subnet in the best AZ."""
    ec2 = boto3.client("ec2", region_name=REGION)
    batch = boto3.client("batch", region_name=REGION)

    # Find all private subnets in this AZ
    resp = ec2.describe_subnets(
        Filters=[
            {"Name": "availability-zone", "Values": [az_name]},
            {"Name": "tag:Name", "Values": [f"{ENV}-private-*"]},
        ]
    )
    target_subnets = [s["SubnetId"] for s in resp.get("Subnets", [])]

    if not target_subnets:
        print(f"  ⚠ No private subnet found in {az_name} — using all subnets")
        return

    # Also include other GPU-capable AZs as fallback
    all_subnets = list(target_subnets)  # best AZ first
    resp = ec2.describe_subnets(
        Filters=[{"Name": "tag:Name", "Values": [f"{ENV}-private-*"]}]
    )
    for s in resp.get("Subnets", []):
        if s["SubnetId"] not in all_subnets:
            all_subnets.append(s["SubnetId"])

    try:
        batch.update_compute_environment(
            computeEnvironment=JOB_QUEUE,
            computeResources={"subnets": all_subnets},
        )
        print(
            f"  Compute environment updated — prioritizing {az_name} ({target_subnets[0]})"
        )
    except Exception as e:
        print(f"  ⚠ Could not update compute environment: {e}")


def _disable_idle_monitor():
    """Disable the OpenSERP idle monitor to prevent NAT teardown during OCR jobs."""
    try:
        events = boto3.client("events", region_name=REGION)
        events.disable_rule(Name=f"{ENV}-wwii-openserp-idle-monitor")
        print("  Idle monitor: disabled (will re-enable after jobs complete)")
    except Exception as e:
        print(f"  ⚠ Could not disable idle monitor: {e}")


def _enable_idle_monitor():
    """Re-enable the OpenSERP idle monitor after OCR jobs complete."""
    try:
        events = boto3.client("events", region_name=REGION)
        events.enable_rule(Name=f"{ENV}-wwii-openserp-idle-monitor")
        print("  Idle monitor: re-enabled")
    except Exception as e:
        print(f"  ⚠ Could not re-enable idle monitor: {e}")


def _chunk_dir(page_range: str) -> str:
    """Return a stable, collision-free chunk directory name for a page range.

    Historically the output dir was ``chunk-{i:03d}`` where ``i`` was the job's
    index *within a single submission*. That silently collided across
    submissions: a re-run of a failed range (or any separate ``--page-range``
    submission) started again at ``i=0`` and overwrote ``chunk-000`` — clobbering
    earlier output. Deriving the dir from the page range itself makes every
    submission (auto-chunk, manifest, single range, and any re-run) write to a
    deterministic, non-overlapping location, and makes re-running one range
    idempotent (it overwrites only its own range).

    Page numbers are zero-padded so lexical sort == page order (``merge_outputs``
    concatenates by sorted key). ``"1-50"`` -> ``chunk-p0001-0050``;
    ``"101-125"`` -> ``chunk-p0101-0125``; a bare ``"7"`` -> ``chunk-p0007``.
    """
    parts = [p.strip() for p in page_range.replace(",", "-").split("-") if p.strip()]
    try:
        nums = [f"{int(p):04d}" for p in parts]
    except ValueError:
        # Non-numeric range — fall back to a sanitized literal (still unique).
        safe = "".join(c if c.isalnum() else "-" for c in page_range)
        return f"chunk-p{safe}"
    return "chunk-p" + "-".join(nums)


def _container_overrides(command: list, dpi: int | None = None) -> dict:
    """Build Batch containerOverrides, injecting IMAGE_DPI when dpi is given.

    Chandra's render DPI is a pydantic BaseSettings field (``IMAGE_DPI``,
    default 192), overridable via the environment with no image rebuild — so a
    higher DPI (e.g. Chandra's recommended 300) is passed as a container env
    var. See docs/current/dataquality/CHANDRA_OCR_DESIGN.md "Render DPI".
    """
    overrides: dict = {"command": command}
    if dpi is not None:
        overrides["environment"] = [{"name": "IMAGE_DPI", "value": str(dpi)}]
    return overrides


def submit_from_manifest(
    s3_path: str, manifest_path: str, dpi: int | None = None
) -> list:
    """Submit one job per line in a manifest file.

    Manifest format (one per line):
        page-range #label
        21-34 #1st infantry
        35-45 #2nd infantry

    Lines starting with # or empty lines are skipped.
    """
    batch = boto3.client("batch", region_name=REGION)
    bucket, key = _parse_s3_path(s3_path)
    pdf_name = key.split("/")[-1].replace(".pdf", "")
    output_prefix = f"ocr-output/{pdf_name}"

    # Parse manifest
    entries = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("#", 1)
            page_range = parts[0].strip()
            label = parts[1].strip() if len(parts) > 1 else page_range
            if not page_range:
                continue
            entries.append((page_range, label))

    print(f"\nManifest: {manifest_path} ({len(entries)} jobs)")
    print("\nSubmitting jobs:")

    jobs = []
    for i, (page_range, label) in enumerate(entries):
        # Sanitize label for job name (alphanumeric, dash, underscore only)
        safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
        job_name = f"chandra-{pdf_name}-{safe_label}"[:128]

        chunk_dir = _chunk_dir(page_range)
        resp = batch.submit_job(
            jobName=job_name,
            jobQueue=JOB_QUEUE,
            jobDefinition=JOB_DEF,
            containerOverrides=_container_overrides(
                [
                    s3_path,
                    f"s3://{bucket}/{output_prefix}/{chunk_dir}/",
                    "--page-range",
                    page_range,
                ],
                dpi=dpi,
            ),
        )
        jobs.append(
            {
                "jobId": resp["jobId"],
                "jobName": job_name,
                "pages": page_range,
                "label": label,
                "chunk_dir": chunk_dir,
            }
        )
        print(
            f"  [{i+1:02d}/{len(entries)}] {label:30s} pages {page_range:10s} → {resp['jobId'][:12]}"
        )

    return jobs


def submit_jobs(
    s3_path: str,
    total_pages: int,
    chunk_size: int,
    page_range: str | None = None,
    dpi: int | None = None,
) -> list:
    """Submit Batch jobs for each chunk. Returns list of job IDs."""
    batch = boto3.client("batch", region_name=REGION)
    bucket, key = _parse_s3_path(s3_path)
    pdf_name = key.split("/")[-1].replace(".pdf", "")
    output_prefix = f"ocr-output/{pdf_name}"

    jobs = []

    if page_range:
        # Single job with explicit page range
        job_name = f"chandra-{pdf_name}-p{page_range.replace(',', '-')}"[:128]
        chunk_dir = _chunk_dir(page_range)
        resp = batch.submit_job(
            jobName=job_name,
            jobQueue=JOB_QUEUE,
            jobDefinition=JOB_DEF,
            containerOverrides=_container_overrides(
                [
                    s3_path,
                    f"s3://{bucket}/{output_prefix}/{chunk_dir}/",
                    "--page-range",
                    page_range,
                ],
                dpi=dpi,
            ),
        )
        jobs.append(
            {
                "jobId": resp["jobId"],
                "jobName": job_name,
                "pages": page_range,
                "chunk_dir": chunk_dir,
            }
        )
        print(f"  Submitted: {job_name} (pages {page_range}) → {resp['jobId'][:12]}")
    else:
        # Split into chunks
        num_chunks = math.ceil(total_pages / chunk_size)
        for i in range(num_chunks):
            start = i * chunk_size + 1
            end = min((i + 1) * chunk_size, total_pages)
            page_spec = f"{start}-{end}"
            job_name = f"chandra-{pdf_name}-p{start}-{end}"[:128]

            chunk_dir = _chunk_dir(page_spec)
            resp = batch.submit_job(
                jobName=job_name,
                jobQueue=JOB_QUEUE,
                jobDefinition=JOB_DEF,
                containerOverrides=_container_overrides(
                    [
                        s3_path,
                        f"s3://{bucket}/{output_prefix}/{chunk_dir}/",
                        "--page-range",
                        page_spec,
                    ],
                    dpi=dpi,
                ),
            )
            jobs.append(
                {
                    "jobId": resp["jobId"],
                    "jobName": job_name,
                    "pages": page_spec,
                    "chunk_dir": chunk_dir,
                }
            )
            print(f"  Submitted: {job_name} (pages {page_spec}) → {resp['jobId'][:12]}")

    return jobs


def _bucket_jobs(statuses: dict) -> tuple:
    """Return (succeeded, failed, running, pending) job-id lists from statuses."""
    succeeded = [jid for jid, j in statuses.items() if j["status"] == "SUCCEEDED"]
    failed = [jid for jid, j in statuses.items() if j["status"] == "FAILED"]
    running = [jid for jid, j in statuses.items() if j["status"] == "RUNNING"]
    pending = [
        jid
        for jid, j in statuses.items()
        if j["status"] in ("SUBMITTED", "PENDING", "RUNNABLE")
    ]
    return succeeded, failed, running, pending


def wait_for_jobs(jobs: list, poll_interval: int = 30) -> bool:
    """Wait for all jobs to complete. Returns True if all succeeded."""
    batch = boto3.client("batch", region_name=REGION)
    job_ids = [j["jobId"] for j in jobs]
    job_labels = {j["jobId"]: j.get("label", j["jobName"]) for j in jobs}
    total = len(job_ids)

    poll_interval = OCR_CONFIG.get("jobs", {}).get("poll_interval", poll_interval)

    print(f"\n{'═' * 60}")
    print(f"  Waiting for {total} job(s)  |  Polling every {poll_interval}s")
    print(f"  Queue: {JOB_QUEUE}")
    print(f"{'═' * 60}")

    seen: dict = {"running": set(), "succeeded": set(), "failed": set()}
    start_time = time.time()

    while True:
        statuses = _describe_all_jobs(batch, job_ids)
        succeeded, failed, running, pending = _bucket_jobs(statuses)

        _log_status_transitions(statuses, job_labels, start_time, seen)
        _maybe_run_diagnostics(
            batch, running, pending, succeeded, start_time, poll_interval
        )

        done = len(succeeded) + len(failed)
        elapsed = _format_elapsed(start_time)
        _print_queue_status(
            jobs,
            statuses,
            elapsed,
            done,
            total,
            len(running),
            len(pending),
            len(failed),
        )

        if done >= total:
            return _print_completion_summary(
                statuses, job_labels, succeeded, failed, _format_elapsed(start_time)
            )

        time.sleep(poll_interval)


def _describe_all_jobs(batch, job_ids: list) -> dict:
    """Return {jobId: job} for all job_ids (Batch describe_jobs caps at 100)."""
    statuses = {}
    for i in range(0, len(job_ids), 100):
        resp = batch.describe_jobs(jobs=job_ids[i : i + 100])
        for j in resp["jobs"]:
            statuses[j["jobId"]] = j
    return statuses


def _failure_reason(job: dict) -> str:
    """Best-effort failure reason: container reason, else statusReason."""
    attempts = job.get("attempts", [])
    if attempts:
        reason = attempts[-1].get("container", {}).get("reason", "")
        if reason:
            return reason
    return job.get("statusReason", "unknown")


def _log_status_transitions(
    statuses: dict,
    job_labels: dict,
    start_time: float,
    seen: dict,
) -> None:
    """Print a line for each job newly entering RUNNING/SUCCEEDED/FAILED.

    ``seen`` holds the sets of already-logged job ids per status, mutated here so
    each transition is logged once across poll iterations.
    """
    transitions = (
        ("RUNNING", seen["running"], "▶ RUNNING "),
        ("SUCCEEDED", seen["succeeded"], "✓ SUCCESS "),
        ("FAILED", seen["failed"], "✗ FAILED  "),
    )
    for status, seen_set, marker in transitions:
        for jid, job in statuses.items():
            if job["status"] != status or jid in seen_set:
                continue
            seen_set.add(jid)
            elapsed = _format_elapsed(start_time)
            print(f"  [{elapsed}] {marker} {job_labels[jid]}")
            if status == "FAILED":
                print(f"             └─ {_failure_reason(job)[:100]}")


def _print_completion_summary(
    statuses: dict, job_labels: dict, succeeded: list, failed: list, elapsed: str
) -> bool:
    """Print the terminal summary block; return True if all jobs succeeded."""
    print(f"\n{'═' * 60}")
    if failed:
        print(
            f"  ✗ COMPLETE ({elapsed}) — {len(succeeded)} succeeded, {len(failed)} failed"
        )
        print("\n  Failed jobs:")
        for jid in failed:
            print(f"    {job_labels[jid]}: {_failure_reason(statuses[jid])[:120]}")
        print(f"{'═' * 60}")
        return False
    print(f"  ✓ ALL SUCCEEDED ({elapsed}) — {len(succeeded)} jobs")
    print(f"{'═' * 60}")
    return True


def _maybe_run_diagnostics(
    batch, running: list, pending: list, succeeded: list, start_time: float, poll: int
) -> None:
    """Run compute diagnostics when jobs are stuck RUNNABLE with no progress."""
    if not (pending and not running and not succeeded):
        return
    diag_interval = OCR_CONFIG.get("jobs", {}).get("diagnostics_interval", 150)
    elapsed_secs = int(time.time() - start_time)
    if elapsed_secs > 0 and elapsed_secs % diag_interval < poll:
        _check_compute_status(batch)


def _print_queue_status(jobs, statuses, elapsed, done, total, running, pending, failed):
    """Print a compact queue view showing status of each manifest entry."""
    status_chars = []
    for j in jobs:
        jid = j["jobId"]
        if jid not in statuses:
            status_chars.append("·")
        else:
            s = statuses[jid]["status"]
            if s == "SUCCEEDED":
                status_chars.append("✓")
            elif s == "FAILED":
                status_chars.append("✗")
            elif s == "RUNNING":
                status_chars.append("▶")
            else:
                status_chars.append("·")

    # Print compact queue bar
    queue_bar = "".join(status_chars)
    print(
        f"  [{elapsed}] [{queue_bar}] "
        f"{done}/{total} done | {running} run | {pending} queue | {failed} err",
        end="\r",
    )


def _format_elapsed(start_time: float) -> str:
    """Format elapsed time as MM:SS."""
    elapsed = int(time.time() - start_time)
    minutes = elapsed // 60
    seconds = elapsed % 60
    return f"{minutes:02d}:{seconds:02d}"


def _fix_nat_down(nat_up: bool) -> bool:
    """Fix 1: bring NAT up if it's down. Returns True if this case handled it."""
    if nat_up:
        return False
    print("\n  ⚠ NAT is DOWN — bringing it up...")
    try:
        lam = boto3.client("lambda", region_name=REGION)
        lam.invoke(
            FunctionName=NAT_MANAGER_FN,
            InvocationType="Event",  # async, don't block
            Payload=json.dumps({"action": "create"}).encode(),
        )
        print("    → nat_manager invoked (async, ~2 min to be ready)")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"    → Failed to invoke nat_manager: {e}")
    _disable_idle_monitor()  # also ensure idle monitor is disabled
    print()
    return True


def _fix_no_spot_capacity(instance_count: int, desired: int, compute_type: str) -> bool:
    """Fix 2: no Spot instances — retarget AZ or fall back to on-demand."""
    if not (instance_count == 0 and desired > 0 and compute_type == "SPOT"):
        return False
    global _spot_wait_start
    max_wait = OCR_CONFIG.get("spot", {}).get("max_wait_minutes", 120)
    if _spot_wait_start is None:
        _spot_wait_start = time.time()
    wait_minutes = (time.time() - _spot_wait_start) / 60

    if wait_minutes >= max_wait:
        print(
            f"\n  ⚠ Spot wait exceeded {max_wait} min — forcing switch to on-demand..."
        )
        _recreate_compute_as_ondemand()
        _spot_wait_start = None
        print()
        return True

    print(
        f"\n  ⚠ No Spot capacity (waiting {int(wait_minutes)}/{max_wait} min) — "
        "checking placement scores..."
    )
    best_az = _check_spot_placement()
    if best_az:
        _target_az(best_az)
    else:
        print("    Switching compute environment to on-demand...")
        _recreate_compute_as_ondemand()
        _spot_wait_start = None
    print()
    return True


def _fix_wrong_az(
    ec2, instance_count: int, desired: int, compute_type: str, subnets, instance_types
) -> bool:
    """Fix 3: on-demand with no instances — subnets may be in a non-GPU AZ."""
    if not (instance_count == 0 and desired > 0 and compute_type == "EC2"):
        return False
    print("\n  ⚠ On-demand requested but no instances launching...")
    gpu_subnet = _find_gpu_subnet(ec2, subnets, instance_types)
    if gpu_subnet and gpu_subnet not in subnets:
        print(f"    Subnets are in wrong AZs — updating to {gpu_subnet}...")
        try:
            boto3.client("batch", region_name=REGION).update_compute_environment(
                computeEnvironment=JOB_QUEUE,
                computeResources={"subnets": [gpu_subnet]},
            )
            print("    → Updated subnet")
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"    → Failed: {e}")
    else:
        subnet_azs = []
        if subnets:
            sub_resp = ec2.describe_subnets(SubnetIds=subnets)
            subnet_azs = [s["AvailabilityZone"] for s in sub_resp.get("Subnets", [])]
        print(f"    Instance types: {', '.join(instance_types)}")
        print(f"    Subnets/AZs: {', '.join(subnet_azs)}")
        print("    Waiting for EC2 to provision...")
    print()
    return True


def _count_gpu_instances(ec2, instance_types: list) -> int:
    """Count pending/running instances of the CE's GPU instance types."""
    instances = ec2.describe_instances(
        Filters=[
            {
                "Name": "instance-type",
                "Values": instance_types or ["g5.xlarge", "g5.2xlarge"],
            },
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ]
    )
    return sum(len(r["Instances"]) for r in instances.get("Reservations", []))


def _nat_is_up(ec2) -> bool:
    """True when the environment's NAT gateway is available."""
    nats = ec2.describe_nat_gateways(
        Filters=[
            {"Name": "tag:Name", "Values": [f"{ENV}-nat"]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    return len(nats.get("NatGateways", [])) > 0


def _check_compute_status(batch):
    """Check compute environment health when jobs are stuck pending; self-heal.

    Gathers CE + instance + NAT state, then applies the first matching
    corrective handler (NAT down, no Spot capacity, wrong-AZ subnets). If none
    apply, reports progress. Each handler is self-contained and returns True
    when it owns the situation.
    """
    try:
        resp = batch.describe_compute_environments(computeEnvironments=[JOB_QUEUE])
        resources = resp["computeEnvironments"][0].get("computeResources", {})
        desired = resources.get("desiredvCpus", 0)
        compute_type = resources.get("type", "unknown")
        instance_types = resources.get("instanceTypes", [])
        subnets = resources.get("subnets", [])

        ec2 = boto3.client("ec2", region_name=REGION)
        instance_count = _count_gpu_instances(ec2, instance_types)
        nat_up = _nat_is_up(ec2)

        # Apply the first corrective handler that owns this situation.
        if _fix_nat_down(nat_up):
            return
        if _fix_no_spot_capacity(instance_count, desired, compute_type):
            return
        if _fix_wrong_az(
            ec2, instance_count, desired, compute_type, subnets, instance_types
        ):
            return

        # Info: instances up, waiting for image pull.
        if instance_count > 0 and nat_up:
            print(
                f"\n  ℹ {instance_count} instance(s) up — pulling Docker image "
                "(~15GB, may take 5-10 min)\n"
            )
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"\n  ⚠ Could not check compute status: {e}\n")


def _find_gpu_subnet(ec2, _current_subnets: list, instance_types: list) -> str | None:
    """Find a subnet in an AZ that offers the requested GPU instance types."""
    # Get all private subnets
    resp = ec2.describe_subnets(
        Filters=[{"Name": "tag:Name", "Values": [f"{ENV}-private-*"]}]
    )
    all_subnets = resp.get("Subnets", [])

    for subnet in all_subnets:
        az = subnet["AvailabilityZone"]
        # Check if this AZ has GPU instances
        offerings = ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[
                {
                    "Name": "instance-type",
                    "Values": instance_types[:1],
                },  # check first type
                {"Name": "location", "Values": [az]},
            ],
        )
        if offerings.get("InstanceTypeOfferings"):
            return subnet["SubnetId"]
    return None


def _recreate_compute_as_ondemand():
    """Delete and recreate the OCR CloudFormation stack as on-demand."""
    import subprocess

    cf = boto3.client("cloudformation", region_name=REGION)
    stack_name = f"wwii-ocr-{ENV}"

    print(f"    Deleting stack {stack_name} (SPOT → on-demand requires recreation)...")
    try:
        cf.delete_stack(StackName=stack_name)
        # Wait for deletion
        waiter = cf.get_waiter("stack_delete_complete")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        print("    → Stack deleted")
    except Exception as e:
        print(f"    → Delete failed: {e}")
        return

    # Redeploy with on-demand (deploy_all.sh --ocr-standalone skips image build if already pushed)
    print("    Redeploying as on-demand...")
    try:
        result = subprocess.run(
            ["bash", "scripts/deploy_all.sh", "--ocr-standalone"],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if result.returncode == 0:
            print("    → Stack recreated as on-demand")
        else:
            # Show last few lines of output
            lines = (result.stdout + result.stderr).strip().split("\n")
            for line in lines[-5:]:
                print(f"    {line}")
    except Exception as e:
        print(f"    → Redeploy failed: {e}")


def merge_outputs(s3_path: str, _num_chunks: int, output_key: str | None = None):
    """Merge chunk outputs into a single markdown file."""
    s3 = boto3.client("s3", region_name=REGION)
    bucket, key = _parse_s3_path(s3_path)
    pdf_name = key.split("/")[-1].replace(".pdf", "")
    prefix = f"ocr-output/{pdf_name}/"

    if not output_key:
        output_key = f"contentrepository/{pdf_name}/{pdf_name}.md"

    print(f"\nMerging chunks from s3://{bucket}/{prefix}")

    # List all .md files in chunk directories, sorted by chunk number then filename
    merged = []
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    md_files = sorted(
        [obj["Key"] for obj in resp.get("Contents", []) if obj["Key"].endswith(".md")]
    )

    for md_key in md_files:
        body = s3.get_object(Bucket=bucket, Key=md_key)["Body"].read().decode("utf-8")
        merged.append(body)

    final = "\n\n".join(merged)
    s3.put_object(Bucket=bucket, Key=output_key, Body=final.encode("utf-8"))
    print(f"  Merged {len(md_files)} files → s3://{bucket}/{output_key}")
    print(f"  Total size: {len(final):,} chars")


def publish_individual_outputs(
    s3_path: str, jobs: list, output_prefix: str | None = None
):
    """Copy each chunk output to its own named file based on the job label.

    Produces files like:
        contentrepository/ETO_Order_of_Battle/1st_infantry.md
        contentrepository/ETO_Order_of_Battle/2nd_infantry.md
    """
    s3 = boto3.client("s3", region_name=REGION)
    bucket, key = _parse_s3_path(s3_path)
    pdf_name = key.split("/")[-1].replace(".pdf", "")

    if not output_prefix:
        output_prefix = f"contentrepository/{pdf_name}"

    print(f"\nPublishing individual files to s3://{bucket}/{output_prefix}/")

    published = 0
    for i, job in enumerate(jobs):
        label = job.get("label", f"chunk-{i:03d}")
        # Convert label to filename: lowercase, spaces/special chars to underscores
        filename = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        filename = filename.strip("_").lower() + ".md"

        # Find the chunk output — use the dir recorded at submit time (derived
        # from the page range), falling back to the range for older jobs.
        chunk_sub = job.get("chunk_dir") or _chunk_dir(job.get("pages", ""))
        chunk_prefix = f"ocr-output/{pdf_name}/{chunk_sub}/"
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=chunk_prefix)
        md_files = [
            obj["Key"] for obj in resp.get("Contents", []) if obj["Key"].endswith(".md")
        ]

        if not md_files:
            print(f"  ⚠ No output for: {label} ({chunk_sub})")
            continue

        # Concatenate all .md files in this chunk (usually just one)
        content = []
        for md_key in sorted(md_files):
            body = (
                s3.get_object(Bucket=bucket, Key=md_key)["Body"].read().decode("utf-8")
            )
            content.append(body)

        output_key = f"{output_prefix}/{filename}"
        s3.put_object(
            Bucket=bucket, Key=output_key, Body="\n\n".join(content).encode("utf-8")
        )
        print(f"  {label:30s} → {filename}")
        published += 1

    print(f"\n  Published {published} files to s3://{bucket}/{output_prefix}/")


def _spot_price_decision(ec2, on_demand_prices: dict, max_proximity: int):
    """Spot-price phase of the compute decision.

    Returns "EC2" to force on-demand (all Spot within proximity, or prices
    unavailable), or None to continue to the placement-score check.
    """
    try:
        from datetime import datetime, timezone, timedelta

        start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        price_resp = ec2.describe_spot_price_history(
            InstanceTypes=list(on_demand_prices.keys()),
            ProductDescriptions=["Linux/UNIX"],
            StartTime=start_time,
        )
        prices = {}
        for p in price_resp.get("SpotPriceHistory", []):
            key = (p["InstanceType"], p["AvailabilityZone"])
            if key not in prices:
                prices[key] = p
        if not prices:
            return None

        best_savings = 0
        print("  Spot Prices:")
        for (itype, az), p in sorted(prices.items()):
            spot_price = float(p["SpotPrice"])
            od_price = on_demand_prices.get(itype, 1.0)
            savings = int((1 - spot_price / od_price) * 100)
            best_savings = max(best_savings, savings)
            indicator = (
                "✓" if savings >= 50 else "⚠" if savings > max_proximity else "✗"
            )
            print(
                f"    {indicator} {itype:14s} {az}: ${spot_price:.3f}/hr ({savings}% off)"
            )

        if best_savings <= max_proximity:
            print(f"\n  → All Spot prices within {max_proximity}% of on-demand.")
            print("  → Decision: ON-DEMAND (guaranteed capacity, similar cost)")
            return "EC2"
        print(f"\n  Best savings: {best_savings}% off on-demand")
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"  ⚠ Could not check Spot prices: {e}")
        print("  → Decision: ON-DEMAND (can't verify Spot value)")
        return "EC2"


def _placement_score_decision(ec2, on_demand_prices: dict, min_score: int):
    """Placement-score phase. Returns "EC2" if score too low, else None."""
    try:
        resp = ec2.get_spot_placement_scores(
            InstanceTypes=list(on_demand_prices.keys()),
            TargetCapacity=1,
            RegionNames=[REGION],
            SingleAvailabilityZone=True,
        )
        scores = resp.get("SpotPlacementScores", [])
        if scores:
            scores.sort(key=lambda s: s.get("Score", 0), reverse=True)
            best_score = scores[0].get("Score", 0)
            print(f"  Best placement score: {best_score}/10 (min: {min_score})")
            if best_score < min_score:
                print("  → Decision: ON-DEMAND (placement score too low)")
                return "EC2"
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"  ⚠ Could not check placement scores: {e}")
    return None


def _decide_compute_type() -> str:
    """Decide SPOT vs EC2 from current Spot prices and placement scores."""
    spot_config = OCR_CONFIG.get("spot", {})
    max_proximity = spot_config.get("max_price_proximity_percent", 10)
    min_score = spot_config.get("min_placement_score", 3)
    on_demand_prices = OCR_CONFIG.get(
        "on_demand_prices",
        {"g4dn.xlarge": 0.526, "g5.xlarge": 1.006, "g6.xlarge": 0.978},
    )

    print("\n=== Compute Type Decision ===")
    ec2 = boto3.client("ec2", region_name=REGION)

    decision = _spot_price_decision(ec2, on_demand_prices, max_proximity)
    if decision:
        return decision
    decision = _placement_score_decision(ec2, on_demand_prices, min_score)
    if decision:
        return decision

    print("  → Decision: SPOT (good price + availability)")
    return "SPOT"


def _current_compute_type(stack: dict) -> "str | None":
    """Read ComputeType from stack params, falling back to the live CE."""
    for param in stack.get("Parameters", []):
        if param["ParameterKey"] == "ComputeType":
            return param["ParameterValue"]
    try:
        batch = boto3.client("batch", region_name=REGION)
        env_resp = batch.describe_compute_environments(computeEnvironments=[JOB_QUEUE])
        if env_resp["computeEnvironments"]:
            return env_resp["computeEnvironments"][0]["computeResources"]["type"]
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return None


def _inspect_ocr_stack(cf, stack_name: str) -> tuple:
    """Return (stack_exists, current_type, needs_recreate) for the OCR stack."""
    broken_states = {
        "ROLLBACK_COMPLETE",
        "CREATE_FAILED",
        "DELETE_FAILED",
        "UPDATE_ROLLBACK_COMPLETE",
        "ROLLBACK_FAILED",
    }
    try:
        stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    except cf.exceptions.ClientError:
        print("\n  OCR stack: does not exist")
        return False, None, False

    status = stack["StackStatus"]
    if status in broken_states:
        print(f"\n  OCR stack: {status} — needs recreation")
        return True, None, True
    current_type = _current_compute_type(stack)
    print(f"\n  OCR stack: {status} (current: {current_type or 'unknown'})")
    return True, current_type, False


def _delete_stack_and_wait(cf, stack_name: str) -> None:
    """Delete a CloudFormation stack and wait for completion (best-effort)."""
    try:
        cf.delete_stack(StackName=stack_name)
        waiter = cf.get_waiter("stack_delete_complete")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        print("  → Deleted")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"  → Delete failed: {e}")


def _ensure_ocr_stack(compute_type: str):
    """Ensure the OCR CloudFormation stack exists with the correct compute type."""
    cf = boto3.client("cloudformation", region_name=REGION)
    stack_name = f"wwii-ocr-{ENV}"

    stack_exists, current_type, needs_recreate = _inspect_ocr_stack(cf, stack_name)

    # Broken stack: delete so it can be recreated cleanly.
    if needs_recreate:
        print("  Deleting broken stack...")
        _delete_stack_and_wait(cf, stack_name)
        stack_exists, current_type = False, None

    # Right stack, wrong compute type: cancel jobs, delete, recreate.
    if stack_exists and current_type and current_type != compute_type:
        print(f"  Stack has {current_type} but need {compute_type} — recreating...")
        _cancel_all_jobs()
        print("  Deleting stack...")
        _delete_stack_and_wait(cf, stack_name)
        stack_exists = False

    if not stack_exists:
        print(f"  Deploying OCR stack as {compute_type}...")
        _deploy_ocr_stack(compute_type)
    else:
        print(f"  Stack ready ({compute_type})")


def _deploy_ocr_stack(compute_type: str):
    """Deploy the OCR stack with the specified compute type."""
    import subprocess

    # Set environment variable so deploy_all.sh knows the compute type
    # Also skip image build if image already exists (saves 10+ min)
    env = os.environ.copy()
    env["OCR_COMPUTE_TYPE"] = compute_type
    env["OCR_SKIP_BUILD"] = "1"  # Image already in ECR from initial deploy

    # Run deploy without capturing output so user sees progress
    print(f"    Running: deploy_all.sh --ocr-standalone (ComputeType={compute_type})")
    result = subprocess.run(
        ["bash", "scripts/deploy_all.sh", "--ocr-standalone"],
        env=env,
        timeout=1200,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"OCR stack deployment failed (exit {result.returncode})")
    print(f"  → Stack deployed as {compute_type}")


def _cancel_all_jobs():
    """Cancel all jobs in the OCR queue."""
    batch = boto3.client("batch", region_name=REGION)
    for status in ["SUBMITTED", "PENDING", "RUNNABLE"]:
        try:
            resp = batch.list_jobs(jobQueue=JOB_QUEUE, jobStatus=status)
            for job in resp.get("jobSummaryList", []):
                batch.cancel_job(jobId=job["jobId"], reason="switching compute type")
        except Exception:
            pass


def _archive_manifest(manifest_path: str):
    """Move completed manifest to manifests_complete/ subdirectory."""
    import shutil  # pylint: disable=import-outside-toplevel

    src = Path(manifest_path)
    if not src.exists():
        return

    dest_dir = src.parent / "manifests_complete"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / src.name

    # Add timestamp if file already exists in archive
    if dest.exists():
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        dest = dest_dir / f"{src.stem}_{timestamp}{src.suffix}"

    shutil.move(str(src), str(dest))
    print(f"\n  Manifest archived: {dest}")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Submit Chandra OCR jobs to AWS Batch")
    parser.add_argument("s3_path", help="S3 path to PDF (s3://bucket/key.pdf)")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50,
        help="Pages per job (default: 50)",
    )
    parser.add_argument(
        "--page-range",
        type=str,
        default=None,
        help="Explicit page range (e.g., '21-34'). Submits single job.",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Path to manifest file (one 'page-range #label' per line). Submits one job per line.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for jobs to complete and merge outputs",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Don't concatenate outputs. Publish each chunk as a separate file named by its label.",
    )
    parser.add_argument(
        "--output-key",
        type=str,
        default=None,
        help="S3 key for merged output (default: contentrepository/{name}/{name}.md)",
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default=None,
        help="S3 prefix for individual files with --no-merge (default: contentrepository/{name}/)",
    )
    parser.add_argument(
        "--skip-networking",
        action="store_true",
        help="Skip nat_manager invocation (networking already up)",
    )
    parser.add_argument(
        "--no-recover-tables",
        action="store_true",
        help="Do not auto-route flattened 2-D tables to PP-StructureV3 after "
        "OCR. By default, pages whose OCR markdown contains a flattened "
        "task-org table are sent to the Paddle recovery worker.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Chandra render DPI (default 300, recommended for dense/table "
        "pages; pass a lower value such as 192 for plain text pages). Passed "
        "as IMAGE_DPI container env var — no image rebuild. See "
        "docs/current/dataquality/CHANDRA_OCR_DESIGN.md.",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        help="AWS region (default: AWS_DEFAULT_REGION or us-east-1)",
    )
    return parser


def _submit_jobs_for_args(args) -> list:
    """Submit jobs per the CLI args (manifest / page-range / full-document)."""
    if args.manifest:
        return submit_from_manifest(args.s3_path, args.manifest, dpi=args.dpi)
    if args.page_range:
        print(f"\nSubmitting single job for pages {args.page_range}")
        if args.dpi:
            print(f"  Render DPI override: IMAGE_DPI={args.dpi}")
        return submit_jobs(args.s3_path, 0, 0, args.page_range, dpi=args.dpi)
    print("\nCounting pages...")
    total_pages = get_page_count(args.s3_path)
    num_chunks = math.ceil(total_pages / args.chunk_size)
    print(f"  {total_pages} pages → {num_chunks} job(s) of {args.chunk_size} pages")
    if args.dpi:
        print(f"  Render DPI override: IMAGE_DPI={args.dpi}")
    print("\nSubmitting jobs:")
    return submit_jobs(args.s3_path, total_pages, args.chunk_size, dpi=args.dpi)


def _discover_flattened_pages(s3, bucket: str, base_prefix: str) -> list:
    """Scan a book's chunk markdown and return ``[(physical_page, chunk_prefix)]``.

    Splits each ``chunk-*/input/input.md`` into physical pages (via the
    ``--paginate_output`` separators) and keeps only pages whose markdown
    contains a flattened task-org table.
    """
    from src.ingestion.chunk_pages import (  # pylint: disable=import-outside-toplevel
        chunk_start_page_from_dir,
        flattened_pages_in_chunk,
    )

    chunk_dirs: list = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=base_prefix, Delimiter="/"):
        for cp_entry in page.get("CommonPrefixes", []) or []:
            chunk_dirs.append(cp_entry["Prefix"])

    flattened: list = []
    for chunk_prefix in sorted(chunk_dirs):
        start_page = chunk_start_page_from_dir(chunk_prefix)
        if start_page is None:
            print(
                f"  \u26a0 {chunk_prefix}: legacy chunk dir without page range — "
                "cannot map to physical pages; skipping."
            )
            continue
        md_key = f"{chunk_prefix}input/input.md"
        try:
            body = (
                s3.get_object(Bucket=bucket, Key=md_key)["Body"]
                .read()
                .decode("utf-8", errors="replace")
            )
        except s3.exceptions.NoSuchKey:
            continue
        for phys_page in flattened_pages_in_chunk(body, start_page):
            flattened.append((phys_page, chunk_prefix))
    flattened.sort()
    return flattened


def auto_route_table_recovery(
    s3_path: str,
    region: str,
    env: str = "dev",
    dpi: int = 300,
    dry_run: bool = False,
) -> list:  # pylint: disable=too-many-locals
    """Auto-route Chandra's flattened 2-D tables to the PP-StructureV3 worker.

    Runs *after* OCR. For each ``chunk-*/input/input.md`` under
    ``ocr-output/{pdf}/``, splits the (``--paginate_output``) markdown into
    physical pages via :mod:`src.ingestion.chunk_pages`, detects which physical
    pages contain a flattened task-org table, and submits one Paddle
    (``{env}-wwii-paddle``) recovery job per such page — so PP-StructureV3 cost
    is spent only where Chandra flattened a table, never on prose/normal pages.

    Reuses the render/submit helpers from :mod:`scripts.submit_table_recovery`
    so there is a single implementation of the page render + Paddle submission.
    Returns the list of submitted job IDs (empty if nothing needed recovery).
    Factored as a standalone function so Phase 0 orchestration can call it too.
    """
    from submit_table_recovery import (  # pylint: disable=import-outside-toplevel,import-error
        _submit_page,
    )

    s3 = boto3.client("s3", region_name=region)
    bucket, key = _parse_s3_path(s3_path)
    pdf_name = key.rsplit("/", maxsplit=1)[-1].replace(".pdf", "")
    base_prefix = f"ocr-output/{pdf_name}/"

    flattened_pages = _discover_flattened_pages(s3, bucket, base_prefix)
    if not flattened_pages:
        print(
            "\n  Table recovery: no flattened 2-D tables detected — nothing to route."
        )
        return []

    flattened_pages.sort()
    pages_str = ", ".join(f"p{n}" for n, _ in flattened_pages)
    print(
        f"\n  Table recovery: {len(flattened_pages)} flattened page(s) "
        f"detected → routing to PP-StructureV3: {pages_str}"
    )
    if dry_run:
        return []

    # Render each flagged page from the source PDF and submit a Paddle job.
    pdf_local = Path(tempfile.mkstemp(prefix="recover_src_", suffix=".pdf")[1])
    s3.download_file(bucket, key, str(pdf_local))
    batch = boto3.client("batch", region_name=region)
    job_queue = f"{env}-wwii-chandra-gpu"  # shared GPU queue
    job_def = f"{env}-wwii-paddle"
    submitted: list = []
    try:
        for phys_page, chunk_prefix in flattened_pages:
            submitted.append(
                _submit_page(
                    batch,
                    s3,
                    bucket=bucket,
                    md_key=f"{chunk_prefix}input/input.md",
                    page_number=phys_page,
                    pdf_local=pdf_local,
                    out_prefix=f"{base_prefix}recovery/",
                    job_queue=job_queue,
                    job_def=job_def,
                    dpi=dpi,
                )
            )
    finally:
        pdf_local.unlink(missing_ok=True)

    print(f"  Submitted {len(submitted)} recovery job(s) to {job_queue}")
    return submitted


def _publish_results(args, jobs: list, success: bool) -> None:
    """Publish/merge outputs and archive the manifest based on run outcome."""
    if success:
        if args.no_merge:
            publish_individual_outputs(args.s3_path, jobs, args.output_prefix)
        else:
            merge_outputs(args.s3_path, len(jobs), args.output_key)
        if args.manifest:
            _archive_manifest(args.manifest)
        # Auto-route flattened 2-D tables to PP-StructureV3 (opt-out).
        if not getattr(args, "no_recover_tables", False):
            try:
                auto_route_table_recovery(args.s3_path, REGION, env=ENV, dpi=args.dpi)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Recovery is best-effort and must never fail the OCR run.
                print(f"  ⚠ Table-recovery routing skipped (non-fatal): {exc}")
        return
    # Partial success — still publish what completed.
    if args.no_merge:
        print("\n  Publishing outputs for succeeded jobs...")
        publish_individual_outputs(args.s3_path, jobs, args.output_prefix)
    if args.manifest:
        print("  Manifest NOT archived (some jobs failed — fix and resubmit)")


def _wait_and_publish(args, jobs: list, compute_type: str) -> None:
    """Block on jobs, publish outputs, and always re-enable the idle monitor."""
    try:
        success = wait_for_jobs(jobs)
        if not success and compute_type == "SPOT":
            print("\n  Spot failed — checking if on-demand fallback is needed...")
            print("  To resubmit as on-demand:")
            print(
                f"    python3 scripts/submit_ocr_job.py {args.s3_path} "
                f"--manifest {args.manifest} --wait --no-merge --region {REGION}"
            )
        _publish_results(args, jobs, success)
    except KeyboardInterrupt:
        print("\n\nInterrupted. Jobs continue running in AWS Batch.")
    finally:
        if not args.skip_networking:
            _enable_idle_monitor()


def main():
    """CLI entry point: submit Chandra OCR jobs, optionally wait and publish."""
    args = _build_arg_parser().parse_args()

    if not args.s3_path.startswith("s3://"):
        print("Error: s3_path must start with s3://")
        sys.exit(1)

    _init_config(args.region)

    # 1. Ensure networking
    if not args.skip_networking:
        ensure_networking()

    # 2. Decide compute type: SPOT or EC2 (on-demand)
    compute_type = _decide_compute_type()

    # 3. Ensure OCR stack is deployed with correct compute type
    _ensure_ocr_stack(compute_type)

    # 4. Submit jobs.
    jobs = _submit_jobs_for_args(args)

    print(f"\n{'─' * 50}")
    print(f"Submitted {len(jobs)} job(s) to queue: {JOB_QUEUE} ({compute_type})")
    print(f"Monitor: aws batch list-jobs --job-queue {JOB_QUEUE} --region {REGION}")

    # 5. Wait, monitor, publish (or print manual next steps).
    if args.wait:
        _wait_and_publish(args, jobs, compute_type)
    else:
        print("\nJobs submitted. Use --wait to block until complete and auto-merge.")
        print(f"Manual merge: python3 scripts/submit_ocr_job.py {args.s3_path} --wait")
        if not args.skip_networking:
            print("\n⚠ Idle monitor is DISABLED. Re-enable after jobs finish:")
            print(
                f"  aws events enable-rule --name {ENV}-wwii-openserp-idle-monitor --region {REGION}"
            )


if __name__ == "__main__":
    main()
