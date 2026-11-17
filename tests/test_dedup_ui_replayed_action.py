"""A dedup action whose group is gone (already committed; the browser saw a network error and
the reviewer clicks Commit All again) is refused with 409 - it never merges another group.
"""

import json

import lambda_handlers.dedup_ui_handler as ui
from tests.test_dedup_ui_rebase_refs import FakeStorage

REPORT = "output/people/duplicate_report.json"


def _storage(groups):
    s = FakeStorage()
    s.write_json(REPORT, {"duplicates": groups})
    return s


def _group(*names):
    return {"people": [{"name": n, "filename": f"{n.lower()}.json"} for n in names]}


def _action(files, action="merge"):
    return {
        "body": json.dumps(
            {"action": action, "entity_type": "people", "group_filenames": files}
        )
    }


def test_a_replayed_action_is_refused_and_merges_nothing(monkeypatch):
    s = _storage([_group("Jodl", "Alfred Jodl"), _group("Kirk", "Alan G. Kirk")])
    merged = []
    monkeypatch.setattr(
        ui, "_do_merge", lambda *a, **k: merged.append(a) or ui._json_response(200, {})
    )
    resp = ui._handle_action(_action(["brooke.json", "alan brooke.json"]), s, {})
    assert resp["statusCode"] == 409 and not merged
    assert "already committed" in json.loads(resp["body"])["error"]


def test_the_group_holding_all_the_files_is_the_one_acted_on():
    s = _storage([_group("Jodl", "Alfred Jodl"), _group("Kirk", "Alan G. Kirk")])
    assert (
        ui._resolve_group_index(s, "people", {"kirk.json", "alan g. kirk.json"}, {})
        == 1
    )
    # a group sharing only one file is not that group
    assert (
        ui._resolve_group_index(s, "people", {"kirk.json", "brooke.json"}, {}) is None
    )
    # an unreadable report never guesses an index
    assert ui._resolve_group_index(FakeStorage(), "people", {"kirk.json"}, {}) is None
    # an action naming no files never guesses an index
    assert ui._resolve_group_index(s, "people", set(), {"group_index": 1}) is None


def test_the_page_keeps_the_queue_until_each_action_is_answered():
    html = ui._serve_ui()["body"]
    assert "localStorage.setItem('dedupPending'" in html
    assert (
        "pendingActions = [];\n  processing = false;" not in html
    )  # the old wipe on failure
    assert "pendingActions.shift();" in html
    # any non-OK answer other than 409 keeps the action queued; a lost response blocks a retry
    # until the first try can have finished (Lambda timeout)
    assert "if (!resp.ok && resp.status !== 409)" in html
    assert "retryAfter = Date.now() + 35000" in html
    assert "localStorage.setItem('dedupRetryAfter'" in html
    assert "load().then(updateCommitBar)" in html
