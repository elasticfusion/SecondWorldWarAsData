"""Lambda URL-review UI handler: routes, affirm->stage, reject->won't-do, fail-safe."""

import json
import types
import lambda_handlers.urlreview_ui_handler as H


def _ev(method, path, body=None):
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body else None,
    }


def test_get_ui_serves_html():
    r = H.handler(_ev("GET", "/urlreview"), None)
    assert r["statusCode"] == 200 and "text/html" in r["headers"]["Content-Type"]
    assert "URL Review" in r["body"]


def test_pending_lists_finds(monkeypatch):
    monkeypatch.setattr(
        "src.enrichment.fetch_review.list_pending",
        lambda: [
            {
                "fetch_event_id": "e1",
                "provider": "wayback",
                "endpoint": "http://x",
                "result_summary": "s",
            }
        ],
    )
    r = H.handler(_ev("GET", "/urlreview/api/pending"), None)
    d = json.loads(r["body"])
    assert (
        r["statusCode"] == 200
        and d["count"] == 1
        and d["pending"][0]["url"] == "http://x"
    )


def test_reject_marks_wont_do(monkeypatch):
    find = {"fetch_event_id": "e1", "provider": "nara", "endpoint": "http://x"}
    monkeypatch.setattr("src.enrichment.fetch_review.list_pending", lambda: [find])
    called = {}
    monkeypatch.setattr(
        "src.enrichment.fetch_review.reject_fetch_find",
        lambda f: (called.__setitem__("rej", f), True)[1],
    )
    r = H.handler(
        _ev(
            "POST",
            "/urlreview/api/action",
            {"fetch_event_id": "e1", "decision": "reject"},
        ),
        None,
    )
    d = json.loads(r["body"])
    assert r["statusCode"] == 200 and d["decision"] == "rejected" and d["ok"] is True
    assert called["rej"]["fetch_event_id"] == "e1"


def test_affirm_stages_source(monkeypatch):
    find = {
        "fetch_event_id": "e1",
        "provider": "wayback",
        "endpoint": "http://x",
        "quarantine_path": "/q/x.html",
    }
    monkeypatch.setattr("src.enrichment.fetch_review.list_pending", lambda: [find])
    monkeypatch.setattr(
        "src.enrichment.fetch_review.affirm_fetch_find",
        lambda f, p, **k: types.SimpleNamespace(source_id="fetchfind-e1"),
    )
    r = H.handler(
        _ev(
            "POST",
            "/urlreview/api/action",
            {"fetch_event_id": "e1", "decision": "affirm"},
        ),
        None,
    )
    d = json.loads(r["body"])
    assert (
        r["statusCode"] == 200
        and d["decision"] == "affirmed"
        and d["staged_source_id"] == "fetchfind-e1"
    )


def test_bad_action_400(monkeypatch):
    monkeypatch.setattr("src.enrichment.fetch_review.list_pending", lambda: [])
    r = H.handler(
        _ev(
            "POST", "/urlreview/api/action", {"fetch_event_id": "", "decision": "maybe"}
        ),
        None,
    )
    assert r["statusCode"] == 400


def test_unknown_route_404():
    assert H.handler(_ev("GET", "/urlreview/nope"), None)["statusCode"] == 404
