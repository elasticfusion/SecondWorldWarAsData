"""Which OCR chunks make up one submission of a book, so the merge waits for ALL of them.

A PDF longer than ``OCR_CHUNK_PAGES`` is OCR'd as several Batch jobs (``chunk-pNNNN-NNNN``), and
every job's SUCCEEDED event invokes the OCR merge. Without a list of the expected chunks the
merge promoted whatever chunks had finished, so each finished chunk published a partial book and
re-triggered parse + extraction (repeated LLM spend), and a book-wide step (the PDF <-> HTML
cross-check) could never see the whole book. The trigger now writes this manifest BEFORE it
submits the jobs; the merge promotes only once every listed chunk's markdown exists AND was
written after the manifest (a re-OCR's chunk directories still hold the earlier run's output
until each new job overwrites it), merges only those chunks, and claims the merge with a
create-only marker so two last chunks finishing together merge once.

The claim belongs to its submission: only that submission's merge releases it, and a claim left
by a merge that died without releasing (a Lambda timeout / OOM, a killed cross-check task) is
taken over after ``OCR_MERGE_CLAIM_TTL`` seconds (default 3 h) by the next attempt - an operator
re-drive of the merge (see ``REDRIVE``).

Layout under ``ocr-output/{book}/``:

* ``.chunks.json`` - ``{"submission": id, "chunks": ["chunk-p0001-0050", ...], "source_key":
  the uploaded object, "validation_url": its transcription or ""}``; a whole-PDF job is the
  single chunk ``""`` (its output is ``ocr-output/{book}/input/input.md``). The validation URL
  is the upload's ``validation-url`` object metadata (src/ingestion/ocr_crosscheck);
* ``.merged``      - created (``IfNoneMatch='*'``) by the merge that won:
  ``{"submission": id, "claimed_at": epoch seconds}``.

Pure helpers (no AWS) plus two small S3 calls; the caller owns the client and the bucket.
"""

from __future__ import annotations

import json
import os
import time

from botocore.exceptions import ParamValidationError
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

MANIFEST_NAME = ".chunks.json"
MERGED_NAME = ".merged"
WHOLE = ""  # the chunk id of a whole-input (unchunked) job
# A chunk written up to this long before its manifest still counts (clock skew between the
# trigger Lambda and S3); Batch jobs start minutes after the manifest is written.
_SKEW_SECONDS = 60
_CONFLICT = ("PreconditionFailed", "ConditionalRequestConflict", "412", "409")
# "force" takes over a claim still inside its TTL (a merge or cross-check task that died
# without releasing it): use it only when no merge/cross-check task of the book is running.
REDRIVE = (
    "aws lambda invoke --function-name {env}-wwii-ocr-merge --cli-binary-format raw-in-base64-out "
    '--payload \'{{"force": true, "detail": {{"status": "SUCCEEDED", "jobQueue": "chandra", '
    '"jobName": "chandra-{book}"}}}}\' /dev/stdout'
)
STALE_CLAIM_WARN = 1800  # seconds: a claim this old with no result is worth a warning


def redrive_command(book: str) -> str:
    """The operator command that re-runs the OCR merge for ``book``."""
    return REDRIVE.format(env=os.getenv("ENV_NAME", "dev"), book=book)


def _claim_ttl() -> float:
    return float(os.getenv("OCR_MERGE_CLAIM_TTL", str(3 * 3600)))


def chunk_dir(page_range: str) -> str:
    """``"1-50"`` -> ``"chunk-p0001-0050"``; ``""`` (whole input) -> ``""``."""
    if not page_range:
        return WHOLE
    first, last = page_range.split("-")
    return f"chunk-p{int(first):04d}-{int(last):04d}"


def chandra_page_range(page_range: str) -> str:
    """A 1-based *physical* page-range spec as Chandra's 0-based ``--page-range`` argument.

    Chandra's ``--page-range`` is 0-based (``chandra.input.parse_range_str`` matches PDF page
    INDICES). Everything else here - chunk directories, manifests, logs, the merge's page numbers -
    speaks 1-based physical pages. Passing a 1-based spec straight through dropped the FIRST page
    of every chunk and read one page past its end (Cross-Channel Attack Chapter VIII, 2026-11-09:
    chunk "1-50" OCR'd pages 2-51, so the chapter's opening page - its "CHAPTER VIII" heading - was
    never read, and page 52 was read twice). Physical page N -> index N-1; single pages ("7"),
    ranges ("1-50") and comma lists ("21-34,40") are converted. Raises ValueError on anything
    else: a wrong page range must not reach a paid GPU job.
    """
    out = [
        _chandra_token(t.strip(), page_range)
        for t in page_range.split(",")
        if t.strip()
    ]
    if not out:
        raise ValueError(f"empty page range: {page_range!r}")
    return ",".join(out)


def _chandra_token(token: str, spec: str) -> str:
    """One "N" or "A-B" of a 1-based spec as 0-based."""
    first, sep, last = (part.strip() for part in token.partition("-"))
    if not first.isdigit() or int(first) < 1 or (sep and not last.isdigit()):
        raise ValueError(f"not a 1-based page range: {spec!r}")
    if not sep:
        return str(int(first) - 1)
    if int(last) < int(first):
        raise ValueError(f"backwards page range: {spec!r}")
    return f"{int(first) - 1}-{int(last) - 1}"


def book_prefix(book: str) -> str:
    return f"ocr-output/{book}/"


def manifest_key(book: str) -> str:
    return f"{book_prefix(book)}{MANIFEST_NAME}"


def merged_key(book: str) -> str:
    return f"{book_prefix(book)}{MERGED_NAME}"


def chunk_markdown_key(book: str, chunk: str) -> str:
    """Where Chandra writes the markdown of ``chunk`` (``""`` = the whole-input job)."""
    sub = f"{chunk}/" if chunk else ""
    return f"{book_prefix(book)}{sub}input/input.md"


@dataclass
class Manifest:
    submission: str
    chunks: List[str]
    source_key: str = ""
    validation_url: str = ""
    created: float = 0.0  # epoch seconds; 0 = unknown (any chunk output counts)

    def to_json(self) -> bytes:
        body = {
            "submission": self.submission,
            "chunks": self.chunks,
            "source_key": self.source_key,
            "validation_url": self.validation_url,
            "created": self.created,
        }
        return json.dumps(body, indent=1).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "Manifest":
        """Raises ValueError on anything but a well-formed manifest (a merge must not guess)."""
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not _well_formed(data):
            raise ValueError(f"malformed OCR chunk manifest: {data!r}")
        return cls(
            data["submission"],
            list(data["chunks"]),
            data.get("source_key") or "",
            data.get("validation_url") or "",
            float(data.get("created") or 0.0),
        )


def _well_formed(data: dict) -> bool:
    submission = data.get("submission")
    optional = (data.get("source_key") or "", data.get("validation_url") or "")
    created = data.get("created") or 0
    return (
        isinstance(submission, str)
        and bool(submission)
        and _chunks_ok(data.get("chunks"))
        and all(isinstance(v, str) for v in optional)
        and isinstance(created, (int, float))
        and not isinstance(created, bool)
    )


def _chunks_ok(chunks: Any) -> bool:
    """A non-empty list of distinct chunk ids; the whole-input id ``""`` only on its own."""
    if not isinstance(chunks, list) or not chunks:
        return False
    if not all(isinstance(c, str) for c in chunks) or len(set(chunks)) != len(chunks):
        return False
    return WHOLE not in chunks or len(chunks) == 1


def build_manifest(
    submission: str,
    page_ranges: Sequence[str],
    source_key: str = "",
    validation_url: str = "",
) -> Manifest:
    return Manifest(
        submission,
        [chunk_dir(r) for r in page_ranges],
        source_key,
        validation_url,
        time.time(),
    )


def first_page(chunk: str) -> Optional[int]:
    """The first PDF page of a chunk: 1 for the whole-input job, None for a chunk directory that
    carries no page range (a legacy ``chunk-003``)."""
    from src.ingestion.chunk_pages import chunk_start_page_from_dir

    if not chunk:
        return 1
    return chunk_start_page_from_dir(chunk)


def write_manifest(s3: Any, bucket: str, book: str, manifest: Manifest) -> None:
    """Record a NEW submission: write its manifest. The previous submission's merge claim is left
    alone (deleting it could free a merge still running for the old submission): the new
    submission's merge takes it over (``claim_merge``). Raises on failure - without a manifest the merge cannot
    tell a partial book from a whole one, so the submit must not go ahead."""
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key(book),
        Body=manifest.to_json(),
        ContentType="application/json",
    )


def read_manifest(s3: Any, bucket: str, book: str) -> Optional[Manifest]:
    """The book's manifest, or None if it has none (an OCR submitted before manifests existed,
    or by the operator CLI). Raises ValueError for a malformed one; other S3 errors propagate.
    """
    try:
        raw = s3.get_object(Bucket=bucket, Key=manifest_key(book))["Body"].read()
    except Exception as e:  # noqa: BLE001 - only an absent manifest is "no manifest"
        if _is_not_found(e):
            return None
        raise
    return Manifest.from_json(raw)


def missing_chunks(s3: Any, bucket: str, book: str, manifest: Manifest) -> List[str]:
    """The manifest's chunks whose markdown is not in S3 yet - or is an earlier submission's
    (written before this manifest)."""
    missing = []
    for chunk in manifest.chunks:
        try:
            head = s3.head_object(Bucket=bucket, Key=chunk_markdown_key(book, chunk))
        except Exception as e:  # noqa: BLE001
            if not _is_not_found(e):
                raise
            missing.append(chunk)
            continue
        if _older_than(head, manifest.created - _SKEW_SECONDS):
            missing.append(chunk)
    return missing


def _older_than(head: dict, cutoff: float) -> bool:
    modified = head.get("LastModified")
    return cutoff > 0 and modified is not None and modified.timestamp() < cutoff


def claim_merge(
    s3: Any, bucket: str, book: str, manifest: Manifest, force: bool = False
) -> bool:
    """Claim this submission's merge. True = this caller merges. False = another merge of this
    submission holds a live claim. A claim of an EARLIER submission, one older than the TTL (its
    merge died without releasing) or, with ``force`` (an operator re-drive), any claim is taken
    over - conditionally on its ETag, so two takers cannot both win."""
    body = json.dumps({"submission": manifest.submission, "claimed_at": time.time()})
    try:
        s3.put_object(
            Bucket=bucket,
            Key=merged_key(book),
            Body=body.encode("utf-8"),
            IfNoneMatch="*",
        )
        return True
    except Exception as e:  # noqa: BLE001
        if _error_code(e) not in _CONFLICT:
            raise
    held, etag = read_claim(s3, bucket, book)
    if held is None:
        return False  # released or replaced meanwhile: the next event decides
    live = claim_age(held) < _claim_ttl()
    if held.get("submission") == manifest.submission and live and not force:
        return False
    try:
        s3.put_object(
            Bucket=bucket, Key=merged_key(book), Body=body.encode("utf-8"), IfMatch=etag
        )
        return True
    except Exception as e:  # noqa: BLE001
        if _error_code(e) in _CONFLICT:
            return False
        raise


def claim_age(held: dict) -> float:
    """Seconds since a claim (as ``read_claim`` returns it) was made."""
    return time.time() - float(held.get("claimed_at") or 0)


def read_claim(s3: Any, bucket: str, book: str):
    """(claim dict, ETag) of ``.merged``, or (None, None) when absent. A legacy claim (the bare
    submission id) reads as that submission, claimed at an unknown time (so it is stale).
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=merged_key(book))
    except Exception as e:  # noqa: BLE001
        if _is_not_found(e):
            return None, None
        raise
    raw = obj["Body"].read().decode("utf-8", "replace")
    try:
        held = json.loads(raw)
    except ValueError:
        held = None
    if not isinstance(held, dict):
        held = {"submission": raw.strip(), "claimed_at": 0}
    return held, obj.get("ETag")


MAX_CROSSCHECK_RETRIES = (
    1  # a task that dies hard is relaunched this many times, then alerted
)


EXHAUSTED = "exhausted"  # note_retry: the retries are used up
LOST = "lost"  # note_retry: the claim is gone, another submission's, or taken by a concurrent event


def note_retry(s3: Any, bucket: str, book: str, submission: str) -> "int | str":
    """Record one relaunch of ``submission``'s cross-check task on its claim. Returns the retry
    number (1 = the first relaunch), ``EXHAUSTED`` when the retries are used up, or ``LOST`` when
    the claim is not this submission's any more (released, taken over, re-submitted) or another
    event won the race. Conditional on the claim's ETag, so two events cannot both relaunch.
    """
    held, etag = read_claim(s3, bucket, book)
    if held is None or held.get("submission") != submission:
        return LOST
    retries = int(held.get("retries") or 0)
    if retries >= MAX_CROSSCHECK_RETRIES:
        return EXHAUSTED
    # the dead task's id goes: the relaunched task is recorded by record_task once it exists
    body = {**held, "retries": retries + 1, "claimed_at": time.time()}
    body.pop("task", None)
    try:
        s3.put_object(
            Bucket=bucket,
            Key=merged_key(book),
            Body=json.dumps(body).encode("utf-8"),
            IfMatch=etag,
        )
    except Exception as e:  # noqa: BLE001
        if _error_code(e) in _CONFLICT:
            return LOST
        raise
    return retries + 1


def record_task(
    s3: Any, bucket: str, book: str, submission: str, task_arn: str
) -> bool:
    """Record the cross-check task that holds ``submission``'s claim, so a STOPPED event is
    acted on only if it is that task's (a duplicate delivery, or a stale event of an earlier
    task of the same submission, must not relaunch or release anything). Returns whether it
    was recorded; a claim that moved on meanwhile is left alone."""
    for _attempt in range(2):
        held, etag = read_claim(s3, bucket, book)
        if held is None or held.get("submission") != submission:
            return False
        try:
            s3.put_object(
                Bucket=bucket,
                Key=merged_key(book),
                Body=json.dumps({**held, "task": task_arn}).encode("utf-8"),
                IfMatch=etag,
            )
            return True
        except Exception as e:  # noqa: BLE001
            if _error_code(e) not in _CONFLICT:
                raise
    return False


def release_merge(s3: Any, bucket: str, book: str, submission: str) -> bool:
    """Drop ``submission``'s claim after a merge that did not complete, so a retry can merge.
    A claim that now belongs to another (newer) submission is left alone. Returns whether it was
    released."""
    held, etag = read_claim(s3, bucket, book)
    if held is None or held.get("submission") != submission:
        return False
    try:  # only the claim read above: a newer submission may take it over in between
        s3.delete_object(Bucket=bucket, Key=merged_key(book), IfMatch=etag)
    except ParamValidationError:  # a boto3 without conditional deletes
        s3.delete_object(Bucket=bucket, Key=merged_key(book))
    except Exception as e:  # noqa: BLE001
        if _error_code(e) in _CONFLICT:
            return False
        raise
    return True


def _error_code(e: Exception) -> str:
    response = getattr(e, "response", None) or {}
    return str((response.get("Error") or {}).get("Code", ""))


def _is_not_found(e: Exception) -> bool:
    return _error_code(e) in ("404", "NoSuchKey", "NotFound")
