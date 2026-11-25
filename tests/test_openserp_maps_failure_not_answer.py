"""A failed external map search is not an answer (TODO gap 13): the place is not marked searched.

Before, every failure (tool missing on ECS, error exit, timeout) returned [] like "no maps found",
and the place was marked searched, so it was never searched again.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from src.extraction import openserp_maps as OM


class _History:
    def __init__(self):
        self.marked = []

    def has_searched(self, name):
        return False

    def mark_searched(self, name):
        self.marked.append(name)


def _process(tmp_path, monkeypatch, results):
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"current_name": "Carentan", "event_mentions": []}))
    monkeypatch.setattr(OM, "search_with_openserp", lambda *a, **k: results)
    h = _History()
    out = OM._process_place_file(
        pf, 1, 1, h, True, 5, "http://x", None, 5, 5, None, tmp_path, set()
    )
    return out, h.marked


def test_a_failed_search_leaves_the_place_unsearched(tmp_path, monkeypatch):
    assert _process(tmp_path, monkeypatch, None) == ((0, 0, 0), [])


def test_an_empty_answer_still_marks_it(tmp_path, monkeypatch):
    assert _process(tmp_path, monkeypatch, []) == ((0, 0, 0), ["Carentan"])


def _run_returns(monkeypatch, outcome):
    def fake_run(*a, **k):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(OM.subprocess, "run", fake_run)


@pytest.mark.parametrize(
    "outcome,reason",
    [
        (FileNotFoundError(), "is not in this image"),
        (subprocess.TimeoutExpired("x", 120), "timeout"),
        (SimpleNamespace(returncode=1, stdout="", stderr="boom"), "exit 1"),
        (SimpleNamespace(returncode=0, stdout='{"a": 1}', stderr=""), "not a list"),
        (
            SimpleNamespace(returncode=0, stdout="not json", stderr=""),
            "JSONDecodeError",
        ),
    ],
)
def test_every_failure_is_none_and_said(monkeypatch, caplog, outcome, reason):
    _run_returns(monkeypatch, outcome)
    assert OM.search_with_openserp("Carentan", None) is None
    assert "openserp_maps.search_failed" in caplog.text and reason in caplog.text


def test_a_place_name_cannot_forge_a_log_line(monkeypatch, caplog):
    _run_returns(monkeypatch, FileNotFoundError())
    OM.search_with_openserp("Carentan\nOBSERV fake.alert status=ok", None)
    (rec,) = [r for r in caplog.records if "search_failed" in r.getMessage()]
    assert "\n" not in rec.getMessage()


def test_a_real_answer_is_returned(monkeypatch):
    _run_returns(monkeypatch, SimpleNamespace(returncode=0, stdout='[{"url": "u"}]'))
    assert OM.search_with_openserp("Carentan", None) == [{"url": "u"}]
    _run_returns(monkeypatch, SimpleNamespace(returncode=0, stdout="[]"))
    assert OM.search_with_openserp("Carentan", None) == []
