#!/usr/bin/env python3
"""Watch a set of Chandra OCR Batch jobs until they finish. READ-ONLY: it only describes the
jobs and checks for their output in S3; it changes nothing.

Usage (in its own terminal, from the repo root):
  python scripts/utils/monitor_ocr_jobs.py --jobs-file /tmp/xcheck_book_jobs.json
  python scripts/utils/monitor_ocr_jobs.py --jobs-file jobs.json --interval 30
  python scripts/utils/monitor_ocr_jobs.py --jobs-file jobs.json --once   # one snapshot, then exit

``--jobs-file`` is the JSON the submitter wrote: ``{name: {"pages": "20-64", "job_id": "...",
"output": "s3://bucket/prefix/"}}``. Each poll prints every job's status, run time and whether
its ``input/input.md`` has landed, plus pages done and an estimate of the time left (from the
seconds per page of the jobs that have finished; 50 s/page until one has). AWS access uses
``--profile`` explicitly, so stray AWS_* environment credentials are not picked up. It needs the
repo venv (boto3): ``source .venv/bin/activate`` first; without it, it says so and exits 2.

Exit codes: 0 every job SUCCEEDED; 1 a job FAILED (its reason is printed); 2 bad input or no
venv; 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # scripts/: submit_ocr_job

FALLBACK_SECONDS_PER_PAGE = 50.0  # measured: Chandra ch. VIII, 67 pages in 56 min
TERMINAL = ("SUCCEEDED", "FAILED")


@dataclass
class JobView:
    """One Batch job as the monitor shows it."""

    name: str
    pages: int
    status: str
    started: Optional[float] = None  # epoch seconds
    stopped: Optional[float] = None
    reason: str = ""
    output_bytes: Optional[int] = None  # size of input/input.md, None = not there yet

    def elapsed(self, now: float) -> Optional[float]:
        """Run time so far (or in total, once stopped); None before it starts."""
        if self.started is None:
            return None
        return (self.stopped or now) - self.started


def page_count(pages: str) -> int:
    """'20-64' -> 45; '7' -> 1."""
    first, _, last = pages.partition("-")
    return int(last or first) - int(first) + 1


def seconds_per_page(jobs: List[JobView], now: float) -> float:
    """Measured from the SUCCEEDED jobs (total run time / total pages), else the fallback."""
    done = [j for j in jobs if j.status == "SUCCEEDED" and j.elapsed(now)]
    pages = sum(j.pages for j in done)
    if not pages:
        return FALLBACK_SECONDS_PER_PAGE
    return sum(j.elapsed(now) or 0.0 for j in done) / pages


def remaining_seconds(jobs: List[JobView], now: float) -> float:
    """The jobs run in parallel: the run ends with its slowest unfinished job."""
    rate = seconds_per_page(jobs, now)
    left = [
        max(0.0, j.pages * rate - (j.elapsed(now) or 0.0))
        for j in jobs
        if j.status not in TERMINAL
    ]
    return max(left, default=0.0)


def _clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def render(jobs: List[JobView], now: float) -> str:
    """The progress table and its summary line."""
    lines = [
        f"{'job':34} {'pages':>5} {'status':10} {'run time':>9} output",
        "-" * 72,
    ]
    for j in sorted(jobs, key=lambda j: (j.status in TERMINAL, j.name)):
        lines += _rows(j, now)
    return "\n".join(lines + ["-" * 72, _summary(jobs, now)])


def _rows(j: JobView, now: float) -> List[str]:
    out = "-" if j.output_bytes is None else f"{j.output_bytes / 1024:.0f} KB"
    rows = [
        f"{j.name[:34]:34} {j.pages:>5} {j.status:10} {_clock(j.elapsed(now)):>9} {out}"
    ]
    if j.status == "FAILED":
        rows.append(f"{'':34}   reason: {j.reason or 'none given'}")
    return rows


def _summary(jobs: List[JobView], now: float) -> str:
    total = sum(j.pages for j in jobs)
    done = sum(j.pages for j in jobs if j.status == "SUCCEEDED")
    counts = Counter(j.status.lower() for j in jobs)
    status = ", ".join(f"{counts[s]} {s}" for s in sorted(counts))
    eta = remaining_seconds(jobs, now)
    tail = f"about {_clock(eta)} left" if eta else "all finished"
    return (
        f"{done}/{total} pages done ({status}); "
        f"{seconds_per_page(jobs, now):.0f} s/page; {tail}"
    )


def _epoch(ms: Optional[int]) -> Optional[float]:
    return ms / 1000.0 if ms else None


def snapshot(batch, s3, spec: Dict[str, dict]) -> List[JobView]:
    """Describe every job and look for its output markdown."""
    # imported here, not at the top: submit_ocr_job needs boto3, and without the venv main()
    # must still run far enough to say so
    # pylint: disable-next=import-error,import-outside-toplevel  # via the sys.path entry above
    from submit_ocr_job import _describe_all_jobs

    described = _describe_all_jobs(batch, [v["job_id"] for v in spec.values()])
    views = []
    for name, v in spec.items():
        job = described.get(v["job_id"], {})
        view = JobView(
            name=name,
            pages=page_count(v["pages"]),
            status=job.get("status", "UNKNOWN"),
            started=_epoch(job.get("startedAt")),
            stopped=_epoch(job.get("stoppedAt")),
            reason=job.get("statusReason", ""),
        )
        if view.status == "SUCCEEDED":
            view.output_bytes = _output_size(s3, v["output"])
        views.append(view)
    return views


def _output_size(s3, output: str) -> Optional[int]:
    """Size of ``{output}input/input.md``, or None if it is not (yet) there."""
    bucket, _, prefix = output.removeprefix("s3://").partition("/")
    key = f"{prefix.rstrip('/')}/input/input.md"
    try:
        return s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except Exception:  # noqa: BLE001 - absent (404) or unreadable: shown as "-"
        return None


def next_steps(jobs: List[JobView], spec: Dict[str, dict]) -> str:
    """What to do once every job is terminal (the monitor does none of it itself)."""
    failed = [j.name for j in jobs if j.status == "FAILED"]
    missing = [
        j.name for j in jobs if j.status == "SUCCEEDED" and j.output_bytes is None
    ]
    buckets = sorted(
        {v["output"].removeprefix("s3://").partition("/")[0] for v in spec.values()}
    )
    lines = ["", "All jobs finished. Next (not done by this monitor):"]
    if failed:
        lines.append(f"  - re-submit the FAILED chunks: {', '.join(failed)}")
    if missing:
        lines.append(
            f"  - SUCCEEDED without input.md (check the job log): {', '.join(missing)}"
        )
    lines.append(
        "  - re-enable the idle monitor:  aws events enable-rule "
        "--name dev-wwii-openserp-idle-monitor"
    )
    for bucket in buckets:
        lines.append(
            "  - jobs writing outside ocr-output/ leave a false ocr-empty marker "
            f"(TODO MED ocr_merge_handler): aws s3 ls s3://{bucket}/needs-review/ocr-empty/"
        )
    return "\n".join(lines)


def _args(argv=None):
    ap = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", maxsplit=1)[0]
    )
    ap.add_argument(
        "--jobs-file",
        type=Path,
        required=True,
        help="the submitter's {name: {pages, job_id, output}} JSON",
    )
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--profile", default="wwii")
    ap.add_argument("--region", default="us-east-1")
    return ap.parse_args(argv)


def _clients(args):
    import boto3

    # An explicit profile: botocore then skips AWS_* environment credentials.
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    return session.client("batch"), session.client("s3")


def watch(args, batch, s3, clock=time.time, sleep=time.sleep, out=print) -> int:
    """Poll until every job is terminal (or once). Returns the exit code."""
    spec = json.loads(args.jobs_file.read_text(encoding="utf-8"))
    while True:
        jobs = snapshot(batch, s3, spec)
        now = clock()
        out(f"\n{time.strftime('%H:%M:%S', time.localtime(now))}  {args.jobs_file}")
        out(render(jobs, now))
        finished = all(j.status in TERMINAL for j in jobs)
        if finished:
            out(next_steps(jobs, spec))
        if finished or args.once:
            return 1 if any(j.status == "FAILED" for j in jobs) else 0
        sleep(args.interval)


def _needs_the_venv() -> Optional[str]:
    """Why the script cannot run outside the repo venv (boto3 lives there), if it cannot."""
    try:
        import boto3  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
    except ModuleNotFoundError:
        return (
            "monitor: boto3 is not installed for this Python. Activate the repo venv first:\n"
            "  cd /home/dchristian/projects/SecondWorldWarAsData && source .venv/bin/activate\n"
            "  python scripts/utils/monitor_ocr_jobs.py --jobs-file /tmp/xcheck_book_jobs.json"
        )
    return None


def main(argv=None) -> int:
    """Watch the jobs; the exit code is in the module docstring."""
    problem = _needs_the_venv()
    if problem:
        print(problem, file=sys.stderr)
        return 2
    args = _args(argv)
    if not args.jobs_file.is_file():
        print(f"monitor: --jobs-file {args.jobs_file}: no such file", file=sys.stderr)
        return 2
    try:
        return watch(args, *_clients(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
