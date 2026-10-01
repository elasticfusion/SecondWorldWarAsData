"""Antivirus / malicious-document scanning at the ingestion front door.

Demand-launched, binary-only. See docs/current/dataquality/AV_SCANNING_DESIGN.md.

Design contract (all enforced here):
- **Binary-only**: text/markdown/html carry no practical virus risk in this
  pipeline (parsed / sent to Grok as data, never browser-rendered or executed), so
  only binary keys (pdf/image/video/office) are scanned. ``select_binary_keys``
  is the gate; a text-only batch returns ``[]`` and the caller skips AV entirely
  (the scan container is never launched -> zero AV cost for text-only uploads).
- **Fail-closed**: a binary that cannot be scanned (scanner outage / error /
  timeout) is NOT allowed to advance — it is quarantined-for-review. An unscanned
  binary never reaches a parser.
- **Quarantine, not delete**: infected (and unscannable) files are MOVED to a
  ``quarantine/`` prefix (outside contentrepository/, so they never re-trigger),
  reject-to-review + alert. Preserves evidence/provenance and survives false
  positives; a later retention policy can purge confirmed-malicious items.
- **Freeze-on-infection hook**: an infected upload freezes the submitting
  user/API-key owner pending review. No submitter identity exists today (S3-event
  uploads carry no principal), so ``freeze_submitter`` records intent (marker +
  alert) and is a no-op when identity is absent; it flips to real enforcement
  (disable API key / set account frozen) when the API/UI submission path stamps
  identity onto the upload. Fail-safe: never raises, never blocks ingestion.

The real engine (ClamAV on a demand-launched Fargate task) is injected via the
``Scanner`` protocol so unit tests mock it — no AV engine runs in tests.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

import boto3

from src.ingestion.review_reject import reject_to_review

logger = logging.getLogger(__name__)

# Binary extensions whose parsers (fitz / ffmpeg / torch / pandoc) are attack
# surface. Text (.txt/.md/.html) is intentionally excluded — see module docstring.
_BINARY_SUFFIXES = (
    # documents
    ".pdf",
    ".epub",
    ".docx",
    ".doc",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".pptx",
    ".ppt",
    ".rtf",
    # images
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".webp",
    # video / audio
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".mp3",
    ".wav",
    ".m4a",
    # archives (unpacked by downstream tooling)
    ".zip",
)


class Verdict(str, Enum):
    """Per-file scan outcome."""

    CLEAN = "clean"
    INFECTED = "infected"
    #: Could not scan (engine outage/error/timeout). Fail-closed -> quarantine.
    ERROR = "error"


@dataclass
class ScanResult:
    key: str
    verdict: Verdict
    signature: str = ""  # malware name when INFECTED
    detail: str = ""


@dataclass
class AvOutcome:
    """Aggregate result the caller acts on."""

    scanned: bool = False  # was a scan actually attempted (binary present)?
    clean_keys: list[str] = field(default_factory=list)
    quarantined_keys: list[str] = field(default_factory=list)
    results: list[ScanResult] = field(default_factory=list)

    @property
    def all_clear(self) -> bool:
        """True if nothing was quarantined (safe to proceed with every key)."""
        return not self.quarantined_keys


class Scanner(Protocol):
    """Pluggable AV engine. The real impl demand-launches a ClamAV Fargate task;
    tests supply a mock. Must never raise — return Verdict.ERROR on failure so the
    caller fails closed."""

    def scan(self, bucket: str, keys: list[str]) -> list[ScanResult]: ...


def select_binary_keys(keys: list[str]) -> list[str]:
    """The demand-launch gate: binary keys that must be scanned.

    Returns ``[]`` for a text-only batch so the caller skips AV (no container
    launch). Union of ocr/convert/video binary types; text/md/html excluded.
    """
    return [k for k in keys if k.lower().endswith(_BINARY_SUFFIXES)]


def _book_from_key(key: str) -> str:
    base = key.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def _submitter_identity(s3_client, bucket: str, key: str) -> str:
    """Read submitter identity stamped on the upload, if any.

    Today uploads arrive via S3 events with no principal -> returns "". When the
    API/UI submission path lands it MUST stamp x-amz-meta-submitter /
    x-amz-meta-api-key-id (see design doc); this reads it with no redesign.
    """
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata", {}) or {}
        return str(meta.get("api-key-id") or meta.get("submitter") or "")
    except Exception as e:  # noqa: BLE001 - identity is best-effort
        logger.debug("No submitter identity for %s: %s", key, e)
        return ""


def freeze_submitter(
    s3_client, bucket: str, identity: str, *, key: str, signature: str, region: str = ""
) -> None:
    """Freeze the submitting user/API-key owner pending review (hook).

    No submitter identity today -> records nothing enforceable but logs the gap.
    When identity exists, writes quarantine/frozen/{identity}.json (the audit
    trail an operator acts on) + alerts. Fail-safe: never raises, never blocks.
    Future: flip to disable the API Gateway usage-plan key / set account.frozen.
    """
    if not identity:
        logger.warning(
            "Infected upload %s has NO submitter identity (S3-event upload); "
            "freeze-submitter hook is a no-op until the API/UI stamps identity",
            key,
        )
        return
    region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    marker = f"quarantine/frozen/{identity}.json"
    body = json.dumps(
        {
            "identity": identity,
            "reason": "av-infected-upload",
            "trigger_key": key,
            "signature": signature,
            "action": "freeze-pending-review",
            "enforced": False,  # becomes True when API-key disable is wired
        }
    )
    try:
        s3_client.put_object(Bucket=bucket, Key=marker, Body=body.encode("utf-8"))
        logger.warning(
            "FREEZE-SUBMITTER [%s] infected upload %s (%s) -> %s",
            identity,
            key,
            signature,
            marker,
        )
    except Exception as e:  # noqa: BLE001 - best-effort
        logger.warning("Could not write freeze marker for %s: %s", identity, e)
    topic = os.getenv("NOTIFICATION_TOPIC_ARN", "")
    if topic:
        try:
            boto3.client("sns", region_name=region).publish(
                TopicArn=topic,
                Subject="WWII Pipeline: submitter frozen (infected upload)",
                Message=(
                    f"Submitter {identity} flagged for freeze pending review after "
                    f"infected upload {key} ({signature}). Marker: "
                    f"s3://{bucket}/{marker}. Enforcement is manual until API-key "
                    f"disable is wired."
                ),
            )
        except Exception as e:  # noqa: BLE001 - best-effort
            logger.warning("Could not alert on freeze for %s: %s", identity, e)


def _quarantine(s3_client, bucket: str, key: str, *, reason: str, region: str) -> None:
    """Move (copy + delete) an infected/unscannable file to quarantine/ and
    reject-to-review + alert. Copy-then-delete so a failed copy never loses the
    file. Never raises."""
    dest = f"quarantine/{key}"
    try:
        s3_client.copy_object(
            Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=dest
        )
        s3_client.delete_object(Bucket=bucket, Key=key)
        logger.warning("QUARANTINE %s -> s3://%s/%s (%s)", key, bucket, dest, reason)
    except Exception as e:  # noqa: BLE001 - best-effort; still reject-to-review
        logger.warning("Could not move %s to quarantine: %s", key, e)
    reject_to_review(
        s3_client,
        bucket,
        key,
        _book_from_key(key),
        reason,
        category="av-infected",
        region=region,
    )


def scan_and_gate(
    s3_client,
    bucket: str,
    keys: list[str],
    scanner: Scanner,
    *,
    region: str = "",
) -> AvOutcome:
    """Scan binary keys and gate them. The caller passes the full content batch;
    this selects the binaries, scans them, quarantines infected AND unscannable
    (fail-closed) files + fires the freeze hook, and returns which keys are clear
    to proceed. Text/non-binary keys are always clear (never scanned).

    Never raises — any internal failure fails closed (quarantine), so ingestion
    cannot be stalled by this gate but also cannot advance an unscanned binary.
    """
    region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    binaries = select_binary_keys(keys)
    non_binary = [k for k in keys if k not in set(binaries)]
    outcome = AvOutcome(scanned=bool(binaries), clean_keys=list(non_binary))
    if not binaries:
        return outcome  # text-only -> no scan, no container launch

    try:
        results = scanner.scan(bucket, binaries)
    except Exception as e:  # noqa: BLE001 - scanner must not, but fail closed
        logger.error("AV scanner raised (failing closed): %s", e)
        results = [
            ScanResult(k, Verdict.ERROR, detail="scanner raised") for k in binaries
        ]

    # Any binary the scanner didn't report on -> fail closed (treat as ERROR).
    reported = {r.key for r in results}
    for k in binaries:
        if k not in reported:
            results.append(ScanResult(k, Verdict.ERROR, detail="no scan result"))

    outcome.results = results
    for r in results:
        if r.verdict is Verdict.CLEAN:
            outcome.clean_keys.append(r.key)
            continue
        # INFECTED or ERROR -> quarantine (fail-closed), never advance.
        outcome.quarantined_keys.append(r.key)
        reason = (
            f"av-infected: {r.signature or r.detail}"
            if r.verdict is Verdict.INFECTED
            else f"av-unscannable (fail-closed): {r.detail}"
        )
        _quarantine(s3_client, bucket, r.key, reason=reason, region=region)
        if r.verdict is Verdict.INFECTED:
            identity = _submitter_identity(s3_client, bucket, r.key)
            freeze_submitter(
                s3_client,
                bucket,
                identity,
                key=r.key,
                signature=r.signature or "unknown",
                region=region,
            )
    return outcome
