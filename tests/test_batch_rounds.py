"""Phase 2 batch ROUNDS: collect -> submit -> re-run until a pass queues nothing; past the cap the
run stops (no more spend, no live fallback) with an alert + diagnostics (src/utils/batch_rounds),
and the phase FAILS so nothing downstream runs on the incomplete book."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import phase2_extract as p2
from src.utils import batch_rounds as br
from src.utils.batch_api import BatchCollector, BatchRequest


def _req(rid, kind):
    return BatchRequest(rid, [{"role": "user", "content": rid}], "m", 0.1, kind)


class Client:
    """Each submit 'answers' the round; ``plan`` says what each following pass queues."""

    def __init__(self, plan, cached=()):
        self.plan, self.submitted, self.cached = list(plan), [], set(cached)
        self.batch_mode = True
        self._batch_collector = BatchCollector()
        for r in self.plan.pop(0):
            self._batch_collector.add(r)

    def submit_batch(self, batch_name=""):
        self.submitted.append((batch_name, len(self._batch_collector)))
        self._batch_collector, self.batch_mode = None, False
        return f"batch-{len(self.submitted)}"

    def _get_cache(self, kind):
        return self.cached

    def run_pass(self, *a, **k):
        for r in self.plan.pop(0) if self.plan else []:
            self._batch_collector.add(r)
        return 5, 0


def _rounds(client, cap=8):
    files = [Path("/x/USA-E-XChannel/chapter8-parsed.json")]
    with (
        mock.patch.object(p2, "_run_core_extraction", side_effect=client.run_pass),
        mock.patch.object(p2, "_retry_missing_events", return_value=(0, 0)) as retry,
        mock.patch.object(br, "publish_alert") as alert,
    ):
        try:
            out = p2._run_batch_rounds(
                files,
                client,
                Path("/x"),
                {"batch": {"max_rounds": cap}},
                {},
                3,
                mock.Mock(),
                (5, 0),
            )
        except br.BatchRoundsExceeded as e:
            out = e
    return out, alert, retry


def test_rounds_continue_until_a_pass_queues_nothing():
    client = Client(
        [
            [_req("ev", "events")],
            [_req("pl", "places"), _req("br", "equipment_broad")],
            [_req("rf1", "equipment_refine"), _req("rf2", "equipment_refine")],
            [],
        ]
    )
    out, alert, retry = _rounds(client)
    assert [n for _name, n in client.submitted] == [1, 2, 2]
    rounds = [name.split("-r")[1].split("-")[0] for name, _n in client.submitted]
    assert rounds == ["1", "2", "3"]
    assert "USA-E-XChannel" in client.submitted[0][0]
    alert.assert_not_called()
    retry.assert_called_once()  # live retry of still-failed events, after the rounds
    assert client.batch_mode is False and out == (5, 0)


def test_past_the_cap_the_run_stops_with_an_alert_and_nothing_more_is_sent():
    endless = [[_req(f"k{i}", "equipment_refine")] for i in range(10)]
    client = Client(endless)
    out, alert, retry = _rounds(client, cap=3)
    assert isinstance(out, br.BatchRoundsExceeded)  # the phase fails: no Phase 3 on it
    assert len(client.submitted) == 3  # rounds 1-3 sent; the 4th was not
    alert.assert_called_once()
    subject, text = alert.call_args.args
    assert "over cap" in subject and "USA-E-XChannel" in subject
    assert "submitted 3 batch rounds" in text and "round 4, which was NOT sent" in text
    assert "equipment_refine" in text and "none repeated" in text
    retry.assert_not_called()  # no live fallback


def test_nothing_queued_still_leaves_batch_mode_and_retries_failed_events():
    client = Client([[]])
    _out, alert, retry = _rounds(client)
    assert client.submitted == [] and not alert.called
    retry.assert_called_once()
    assert client.batch_mode is False


def test_a_request_asked_again_after_its_answer_is_diagnosed():
    rounds = br.BatchRounds(scope="B", cap=1)
    assert rounds.admit([_req("a", "equipment_refine"), _req("b", "equipment_refine")])
    cache = SimpleNamespace(_get_cache=lambda kind: {"a"})
    with (
        mock.patch.object(br, "publish_alert") as alert,
        mock.patch.object(br, "observe_warn") as warn,
    ):
        ok = rounds.admit(
            [_req("a", "equipment_refine"), _req("b", "equipment_refine")], cache
        )
    assert not ok
    assert warn.call_args.args[1] == "round_repeats"  # flagged as it happens
    d = rounds.diagnose(cache)
    assert d["repeated"] == 2
    assert d["repeated_lookup_key_differs"] == {
        "equipment_refine": 1
    }  # "a": stored, missed
    assert d["repeated_answer_not_stored"] == {
        "equipment_refine": 1
    }  # "b": never stored
    text = alert.call_args.args[1]
    assert "different key" in text and "never stored" in text


def test_an_unreadable_cache_is_reported_as_unknown_not_missing():
    rounds = br.BatchRounds(scope="B", cap=5)
    rounds.admit([_req("a", "places")])
    rounds.admit([_req("a", "places")])

    def boom(kind):
        raise OSError("cache down")

    d = rounds.diagnose(SimpleNamespace(_get_cache=boom))
    assert d["repeated_cache_unreadable"] == {"places": 1}
    assert d["repeated_answer_not_stored"] == {}


def test_the_cap_marks_the_phase_results_incomplete(tmp_path):
    e = br.BatchRoundsExceeded(8, "B")
    p2._write_phase_results(tmp_path, 3, 0, incomplete=str(e))
    results = json.loads((tmp_path / ".phase_results.json").read_text())
    assert "over cap" in results["incomplete"] and results["processed"] == 3
