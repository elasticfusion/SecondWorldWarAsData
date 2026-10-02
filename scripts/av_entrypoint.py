#!/usr/bin/env python3
"""ClamAV container entrypoint — see Dockerfile.clamav + AV_SCANNING_DESIGN.md.

Commands (argv[1]):
  scan      Download AV_KEYS from S3, clamscan each, write the verdict JSON to
            s3://S3_BUCKET/AV_RESULT_KEY = {key: {"verdict": clean|infected,
            "signature": "..."}}. Syncs signatures from S3 first (fail-closed:
            if signatures are missing/empty, every key is reported ERROR so the
            trigger quarantines rather than passing unscanned bytes).
  freshclam Run freshclam to refresh the signature DB, then upload the .cvd/.cld
            files to s3://S3_BUCKET/AV_SIG_PREFIX for the scan task to sync.

Verdicts map to src/ingestion/av_scan.Verdict: a key absent from the result JSON
is treated as ERROR by the trigger (fail-closed), so partial output is safe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import boto3

SIG_PREFIX = os.environ.get("AV_SIG_PREFIX", "clamav-sigs/")
SIG_DIR = "/var/lib/clamav"

_S3 = None


def _bucket() -> str:
    """Read S3_BUCKET lazily (only when a command actually needs it) so the
    entrypoint can dispatch / reject an unknown command without a hard KeyError
    crash when the env is absent."""
    return os.environ["S3_BUCKET"]


def _s3():
    global _S3
    if _S3 is None:
        _S3 = boto3.client(
            "s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        )
    return _S3


def _sync_signatures_from_s3(s3, bucket: str) -> bool:
    """Download signature DB files from S3 into SIG_DIR. Returns True if any
    signature file was fetched (scanning without signatures is unsafe)."""
    got = False
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=SIG_PREFIX):
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if not name:
                continue
            s3.download_file(bucket, obj["Key"], os.path.join(SIG_DIR, name))
            got = True
    return got


def _scan() -> None:
    s3, bucket = _s3(), _bucket()
    keys = json.loads(os.environ.get("AV_KEYS", "[]"))
    result_key = os.environ["AV_RESULT_KEY"]
    results: dict[str, dict[str, str]] = {}

    have_sigs = False
    try:
        have_sigs = _sync_signatures_from_s3(s3, bucket)
    except Exception as e:  # noqa: BLE001
        print(f"signature sync failed: {e}", file=sys.stderr)

    if not have_sigs:
        # Fail-closed: no signatures -> report nothing clean. Trigger quarantines.
        print("No signatures available; reporting unscannable (fail-closed)")
        s3.put_object(
            Bucket=bucket, Key=result_key, Body=json.dumps({}).encode("utf-8")
        )
        return

    with tempfile.TemporaryDirectory() as tmp:
        for key in keys:
            local = os.path.join(tmp, key.replace("/", "_"))
            try:
                s3.download_file(bucket, key, local)
                proc = subprocess.run(
                    ["clamscan", "--no-summary", "--database", SIG_DIR, local],
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )
                if proc.returncode == 0:
                    results[key] = {"verdict": "clean", "signature": ""}
                elif proc.returncode == 1:
                    sig = ""
                    for line in proc.stdout.splitlines():
                        if line.endswith("FOUND"):
                            sig = line.split(":", 1)[-1].replace(" FOUND", "").strip()
                            break
                    results[key] = {"verdict": "infected", "signature": sig}
                # returncode 2 == error -> omit key -> trigger treats as ERROR
            except Exception as e:  # noqa: BLE001 - omit -> fail-closed ERROR
                print(f"scan error for {key}: {e}", file=sys.stderr)

    s3.put_object(
        Bucket=bucket, Key=result_key, Body=json.dumps(results).encode("utf-8")
    )
    print(f"Wrote {len(results)} verdicts to {result_key}")


def _freshclam() -> None:
    s3, bucket = _s3(), _bucket()
    subprocess.run(["freshclam", "--datadir", SIG_DIR], check=False, timeout=1800)
    uploaded = 0
    for name in os.listdir(SIG_DIR):
        if name.endswith((".cvd", ".cld")):
            s3.upload_file(os.path.join(SIG_DIR, name), bucket, f"{SIG_PREFIX}{name}")
            uploaded += 1
    print(f"Uploaded {uploaded} signature files to s3://{bucket}/{SIG_PREFIX}")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        _scan()
    elif cmd == "freshclam":
        _freshclam()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
