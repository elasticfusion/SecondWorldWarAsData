"""Site-level exclusion: registry store, Grok scope decision, handler action, OpenSERP filter."""

import json
import types


# --- url_registry: exclude_domain / is_domain_excluded / decide short-circuit ---
def test_registry_exclude_and_check(monkeypatch):
    import src.utils.url_registry as R

    store = {}

    class _T:
        def put_item(self, Item):
            store[Item["cache_key"]] = Item

        def get_item(self, Key):
            it = store.get(Key["cache_key"])
            return {"Item": it} if it else {}

    monkeypatch.setattr(R, "_resolve_table", lambda: _T())
    assert (
        R.exclude_domain("https://www.spam.example/x?q=1", reason="r", source="s")
        is True
    )
    # www-stripped host keyed
    assert R.is_domain_excluded("http://spam.example/other") is True
    assert R.is_domain_excluded("http://good.example/page") is False


def test_registry_decide_skips_excluded(monkeypatch):
    import src.utils.url_registry as R

    monkeypatch.setattr(R, "is_domain_excluded", lambda u: True)
    action, rec = R.decide("https://spam.example/abc")
    assert action == R.SKIP_KNOWN and rec is None


def test_host_of_normalizes():
    import src.utils.url_registry as R

    assert R._host_of("https://www.Example.com/a") == "example.com"
    assert R._host_of("Example.com") == "example.com"
    assert R._host_of("spam.example/other/path") == "spam.example"
    assert R._host_of("www.spam.example:8080/x") == "spam.example"
    assert R._host_of("") == ""


# --- fetch_review: Grok scope decision (fail-closed) + exclude_fetch_find ---
def test_decide_scope_failclosed_without_grok():
    import src.enrichment.fetch_review as FR

    assert FR.decide_exclude_scope({"endpoint": "http://x"}, None) == FR.SCOPE_URL


def test_decide_scope_site_when_grok_says_site():
    import src.enrichment.fetch_review as FR

    gc = types.SimpleNamespace(chat_completion=lambda **k: "SITE")
    assert FR.decide_exclude_scope({"endpoint": "http://x"}, gc) == FR.SCOPE_SITE


def test_decide_scope_url_on_grok_error():
    import src.enrichment.fetch_review as FR

    def _boom(**k):
        raise RuntimeError("grok down")

    gc = types.SimpleNamespace(chat_completion=_boom)
    assert FR.decide_exclude_scope({"endpoint": "http://x"}, gc) == FR.SCOPE_URL


def test_exclude_fetch_find_site_calls_registry(monkeypatch):
    import src.enrichment.fetch_review as FR

    monkeypatch.setattr(FR, "decide_exclude_scope", lambda f, g: FR.SCOPE_SITE)
    monkeypatch.setattr(FR, "reject_fetch_find", lambda f: True)
    called = {}
    monkeypatch.setattr(
        "src.utils.url_registry.exclude_domain",
        lambda u, **k: called.setdefault("u", u) or True,
    )
    monkeypatch.setattr("src.utils.url_registry._host_of", lambda u: "spam.example")
    out = FR.exclude_fetch_find(
        {"endpoint": "http://spam.example/x", "provider": "web"}, object()
    )
    assert (
        out["scope"] == "site" and out["ok"] is True and out["host"] == "spam.example"
    )
    assert called["u"] == "http://spam.example/x"


def test_exclude_fetch_find_url_scope_no_registry(monkeypatch):
    import src.enrichment.fetch_review as FR

    monkeypatch.setattr(FR, "decide_exclude_scope", lambda f, g: FR.SCOPE_URL)
    monkeypatch.setattr(FR, "reject_fetch_find", lambda f: True)
    # exclude_domain must NOT be called in URL scope
    monkeypatch.setattr(
        "src.utils.url_registry.exclude_domain",
        lambda u, **k: (_ for _ in ()).throw(
            AssertionError("should not exclude domain")
        ),
    )
    out = FR.exclude_fetch_find({"endpoint": "http://ok.example/x"}, object())
    assert out["scope"] == "url" and out["ok"] is True


# --- openserp: _drop_excluded_domains filter ---
def test_openserp_drops_excluded_results(monkeypatch):
    import src.enrichment.openserp_enrichment as O

    monkeypatch.setattr(
        "src.utils.url_registry.is_domain_excluded", lambda u: "spam" in u
    )
    monkeypatch.setattr(O, "_metric", lambda *a, **k: None)
    results = [{"url": "http://spam.example/1"}, {"url": "http://good.example/2"}]
    kept = O._drop_excluded_domains(results)
    assert [r["url"] for r in kept] == ["http://good.example/2"]


# --- handler: exclude action wired to exclude_fetch_find ---
def test_handler_exclude_action(monkeypatch):
    import lambda_handlers.urlreview_ui_handler as H

    find = {
        "fetch_event_id": "e1",
        "endpoint": "http://spam.example/x",
        "provider": "web",
    }
    monkeypatch.setattr("src.enrichment.fetch_review.list_pending", lambda: [find])
    monkeypatch.setattr(H, "_grok_client", lambda: None)
    monkeypatch.setattr(
        "src.enrichment.fetch_review.exclude_fetch_find",
        lambda f, g: {"scope": "site", "ok": True, "host": "spam.example"},
    )
    ev = {
        "httpMethod": "POST",
        "path": "/urlreview/api/action",
        "body": json.dumps({"fetch_event_id": "e1", "decision": "exclude"}),
    }
    r = H.handler(ev, None)
    d = json.loads(r["body"])
    assert (
        r["statusCode"] == 200
        and d["decision"] == "excluded"
        and d["scope"] == "site"
        and d["host"] == "spam.example"
    )
