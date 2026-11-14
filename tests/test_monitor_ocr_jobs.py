"""scripts/utils/monitor_ocr_jobs.py: read-only progress monitor for Chandra OCR Batch jobs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "utils" / "monitor_ocr_jobs.py"
)


@pytest.fixture(scope="module")
def M():
    spec = importlib.util.spec_from_file_location("monitor_ocr_jobs", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = (
        module  # dataclasses resolve their module through sys.modules
    )
    spec.loader.exec_module(module)
    return module


SPEC = {
    "ch01": {
        "pages": "20-64",
        "job_id": "j1",
        "output": "s3://b/scratch/ocr/chunk-p0020-0064/",
    },
    "ch02": {
        "pages": "65-101",
        "job_id": "j2",
        "output": "s3://b/scratch/ocr/chunk-p0065-0101/",
    },
    "front": {
        "pages": "1-19",
        "job_id": "j3",
        "output": "s3://b/scratch/ocr/chunk-p0001-0019/",
    },
}


class FakeBatch:
    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = 0

    def describe_jobs(self, jobs):
        self.calls += 1
        return {"jobs": [self.jobs[j] for j in jobs if j in self.jobs]}


class FakeS3:
    def __init__(self, sizes):
        self.sizes = sizes
        self.keys = []

    def head_object(self, Bucket, Key):
        self.keys.append((Bucket, Key))
        if Key not in self.sizes:
            raise RuntimeError("404")
        return {"ContentLength": self.sizes[Key]}


T0 = 1_000_000.0


def _job(job_id, status, started=None, stopped=None, reason=""):
    out = {"jobId": job_id, "status": status, "statusReason": reason}
    if started is not None:
        out["startedAt"] = int(started * 1000)
    if stopped is not None:
        out["stoppedAt"] = int(stopped * 1000)
    return out


def test_page_count(M):
    assert M.page_count("20-64") == 45 and M.page_count("7") == 1


def test_the_rate_comes_from_finished_jobs(M):
    jobs = [
        M.JobView("front", 19, "SUCCEEDED", started=T0, stopped=T0 + 19 * 40),
        M.JobView("ch01", 45, "RUNNING", started=T0),
    ]
    assert M.seconds_per_page(jobs, T0 + 1000) == pytest.approx(40.0)
    # ch01 needs 45 * 40 = 1800 s and has run 1000: the run ends in about 800 s
    assert M.remaining_seconds(jobs, T0 + 1000) == pytest.approx(800.0)


def test_without_a_finished_job_the_measured_fallback_is_used(M):
    jobs = [M.JobView("ch01", 10, "RUNNABLE")]
    assert M.seconds_per_page(jobs, T0) == M.FALLBACK_SECONDS_PER_PAGE
    assert M.remaining_seconds(jobs, T0) == 10 * M.FALLBACK_SECONDS_PER_PAGE


def test_snapshot_reads_status_times_and_output(M):
    batch = FakeBatch(
        {
            "j1": _job("j1", "RUNNING", started=T0),
            "j2": _job("j2", "FAILED", started=T0, stopped=T0 + 60, reason="OOM"),
            "j3": _job("j3", "SUCCEEDED", started=T0, stopped=T0 + 950),
        }
    )
    s3 = FakeS3({"scratch/ocr/chunk-p0001-0019/input/input.md": 33964})
    jobs = {j.name: j for j in M.snapshot(batch, s3, SPEC)}
    assert jobs["front"].output_bytes == 33964 and jobs["front"].elapsed(T0) == 950
    assert jobs["ch02"].reason == "OOM" and jobs["ch01"].output_bytes is None
    # only finished jobs are looked up in S3, at {output}input/input.md
    assert s3.keys == [("b", "scratch/ocr/chunk-p0001-0019/input/input.md")]


def test_a_job_batch_does_not_know_is_unknown_not_a_crash(M):
    jobs = M.snapshot(FakeBatch({}), FakeS3({}), {"x": SPEC["ch01"]})
    assert jobs[0].status == "UNKNOWN"


def test_render_shows_each_job_and_the_summary(M):
    jobs = [
        M.JobView(
            "front", 19, "SUCCEEDED", started=T0, stopped=T0 + 950, output_bytes=33964
        ),
        M.JobView("ch02", 37, "FAILED", started=T0, stopped=T0 + 60, reason="OOM"),
        M.JobView("ch01", 45, "RUNNING", started=T0),
    ]
    text = M.render(jobs, T0 + 1000)
    assert "front" in text and "33 KB" in text and "0:15:50" in text
    assert "reason: OOM" in text
    assert "19/101 pages done (1 failed, 1 running, 1 succeeded); 50 s/page" in text
    assert text.index("ch01") < text.index("front")  # unfinished jobs first


def _args(M, tmp_path, *extra):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps(SPEC), encoding="utf-8")
    return M._args(["--jobs-file", str(jobs_file), "--interval", "5", *extra])


def test_watch_polls_until_all_are_finished_then_prints_next_steps(M, tmp_path):
    running = {k: _job(v["job_id"], "RUNNING", started=T0) for k, v in SPEC.items()}
    done = {
        v["job_id"]: _job(v["job_id"], "SUCCEEDED", started=T0, stopped=T0 + 900)
        for v in SPEC.values()
    }
    batch = FakeBatch({v["job_id"]: running[k] for k, v in SPEC.items()})
    printed, sleeps = [], []

    def sleep(seconds):
        sleeps.append(seconds)
        batch.jobs = done  # the next poll finds them finished

    code = M.watch(
        _args(M, tmp_path), batch, FakeS3({}), clock=lambda: T0 + 950, sleep=sleep,
        out=printed.append,
    )  # fmt: skip
    assert code == 0 and sleeps == [5] and batch.calls == 2
    text = "\n".join(printed)
    assert "All jobs finished" in text and "enable-rule" in text
    assert "SUCCEEDED without input.md" in text  # FakeS3 has none of the outputs


def test_watch_once_and_a_failure_exit_1(M, tmp_path):
    batch = FakeBatch(
        {v["job_id"]: _job(v["job_id"], "FAILED", reason="Essential container exited")
         for v in SPEC.values()}
    )  # fmt: skip
    printed: list = []
    code = M.watch(_args(M, tmp_path, "--once"), batch, FakeS3({}), out=printed.append)
    assert code == 1
    assert "re-submit the FAILED chunks: ch01, ch02, front" in "\n".join(printed)


def test_main_rejects_a_missing_jobs_file(M, tmp_path, capsys):
    assert M.main(["--jobs-file", str(tmp_path / "absent.json")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_without_the_venv_it_says_how_to_run_instead_of_a_traceback(
    M, monkeypatch, capsys
):
    import builtins

    real_import = builtins.__import__

    def no_boto3(name, *args, **kwargs):
        if name == "boto3":
            raise ModuleNotFoundError("No module named 'boto3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_boto3)
    assert M.main([]) == 2
    err = capsys.readouterr().err
    assert "source .venv/bin/activate" in err and "--jobs-file" in err
