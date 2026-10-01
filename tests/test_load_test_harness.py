"""Tests for the M9 load-test harness dry-run path (spec §12.9/§16).

The dry-run path is fully testable without AWS: sampling, routing, estimation.
The live path (DynamoDB/xAI collection) is exercised in the actual load test.
"""

from pathlib import Path

from scripts import load_test


def _make_docs(root: Path):
    (root / "a.pdf").write_bytes(b"%PDF" + b"x" * 4000)
    (root / "b.pdf").write_bytes(b"%PDF" + b"y" * 4000)
    (root / "c.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"z" * 400)
    (root / "d.txt").write_text("hello world " * 100, encoding="utf-8")


def test_sample_caps_at_n(tmp_path):
    _make_docs(tmp_path)
    picked = load_test.sample_docs(tmp_path, 2)
    assert len(picked) == 2


def test_sample_is_media_balanced(tmp_path):
    _make_docs(tmp_path)
    picked = load_test.sample_docs(tmp_path, 3)
    medias = {load_test._classify_ext(p) for p in picked}
    # Round-robin across media => at least 2 distinct media types in a sample of 3
    assert len(medias) >= 2


def test_sample_handles_fewer_than_n(tmp_path):
    (tmp_path / "only.pdf").write_bytes(b"%PDF")
    picked = load_test.sample_docs(tmp_path, 20)
    assert len(picked) == 1  # can't invent docs


def test_dry_run_reports_mix_and_estimate(tmp_path):
    _make_docs(tmp_path)
    report = load_test.dry_run(tmp_path, 4, pool=4)
    assert report.mode == "dry-run"
    assert report.sample_size == 4
    assert report.est_cost_usd > 0
    assert sum(report.media_mix.values()) == 4
    # pdf routes to narrative, jpg to vision — both tracks present
    assert any("narrative" in k for k in report.media_mix)
    assert any("vision" in k for k in report.media_mix)


def test_dry_run_no_aws_note(tmp_path):
    _make_docs(tmp_path)
    report = load_test.dry_run(tmp_path, 2, pool=4)
    assert any("no spend" in n.lower() for n in report.notes)


def test_report_throughput_computation():
    r = load_test.LoadTestReport(mode="live", sample_size=10, pool=4)
    r.started_at = 1000.0
    r.finished_at = 1000.0 + 3600.0  # 1 hour
    r.docs_done = 8
    assert r.throughput_docs_per_hr == 8.0


def test_main_dry_run_exit_zero(tmp_path):
    _make_docs(tmp_path)
    rc = load_test.main(["--source", str(tmp_path), "--sample", "2"])
    assert rc == 0  # dry-run has no correctness violations
