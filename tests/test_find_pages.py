"""A queued find keeps the page it was judged on, and its approval gives Phase 0 that page, not a
stub with the URL and summary (Group 3)."""

import logging

import pytest

import src.enrichment.fetch_review as FR
import src.enrichment.openserp_enrichment as oe
from src.utils import find_pages, url_fetch

URL = "https://example.org/obit/john-doe"
HTML = "<html><body>John Doe served with the 3rd Armored Division.</body></html>"


def test_a_page_is_kept_once_under_its_checksum_and_the_url_points_at_it():
    got = find_pages.keep(URL, HTML)
    assert got["page_kept"] is True and got["page_retrieved_from"] == "live"
    assert got["page_sha256"] == find_pages.content_sha256(HTML.encode())
    assert got["page_bytes"] == len(HTML.encode())
    assert find_pages.load(got) == HTML.encode()
    again = find_pages.kept(URL)
    assert {k: again[k] for k in find_pages.PAGE_FIELDS} == {
        k: got[k] for k in find_pages.PAGE_FIELDS
    }
    pages = list((find_pages._LOCAL_DIR / "pages").iterdir())
    assert [p.name for p in pages] == [got["page_sha256"][7:] + ".html"]


def test_an_archived_copy_says_where_it_came_from():
    prov = {
        "archived_url": "https://web.archive.org/web/2019/https://example.org/obit",
        "wayback_capture_timestamp": "20190101000000",
    }
    got = find_pages.keep(URL, HTML, prov)
    assert got["page_retrieved_from"] == "wayback"
    assert got["page_archived_url"] == prov["archived_url"]
    assert got["page_wayback_capture_timestamp"] == "20190101000000"


def test_nothing_to_keep_is_not_kept():
    assert find_pages.keep(URL, "") == {"page_kept": False}
    assert find_pages.keep("javascript:alert(1)", HTML) == {"page_kept": False}


def test_a_failed_write_is_retried_then_reported(monkeypatch, caplog):
    monkeypatch.setattr(find_pages, "_RETRY_BASE_S", 0)
    tries = []

    class _Down:
        def exists(self, path):
            return False

        def write_bytes(self, path, data):
            tries.append(path)
            raise OSError("s3 down")

    monkeypatch.setattr(find_pages, "_storage", lambda: _Down())
    with caplog.at_level(logging.ERROR):
        assert find_pages.keep(URL, HTML) == {"page_kept": False}
    assert len(tries) == find_pages._WRITE_ATTEMPTS
    assert "OBSERV find_pages.write_failed what=page err=OSError" in caplog.text


def test_a_url_with_no_kept_page_says_so(caplog):
    with caplog.at_level(logging.WARNING):
        assert find_pages.kept("https://example.org/never") == {"page_kept": False}
    assert "OBSERV find_pages.not_kept" in caplog.text


def test_a_missing_or_altered_page_is_unusable():
    got = find_pages.keep(URL, HTML)
    path = find_pages._LOCAL_DIR / "pages" / (got["page_sha256"][7:] + ".html")
    path.write_bytes(b"<html>something else</html>")
    with pytest.raises(find_pages.PageUnusable, match="do not match"):
        find_pages.load(got)
    path.unlink()
    with pytest.raises(find_pages.PageUnusable, match="could not be read"):
        find_pages.load(got)
    with pytest.raises(find_pages.PageUnusable, match="malformed"):
        find_pages.load({"page_sha256": "sha256:../../etc/passwd"})
    assert find_pages.load({}) is None  # queued before pages were kept


def test_in_aws_the_pages_go_to_the_data_bucket(monkeypatch):
    from src.utils.storage import S3Storage

    monkeypatch.setattr(find_pages, "_bucket", lambda: "dev-wwii-data-pipeline")
    store = find_pages._storage()
    assert isinstance(store, S3Storage) and store.bucket == "dev-wwii-data-pipeline"
    assert store._key("pages/x.html") == "find-pages/pages/x.html"  # outside output/


# --- OpenSERP: the page the summary was made from goes with the find ---------------------------


def _fresh_page(monkeypatch):
    monkeypatch.setattr(oe, "_url_verdict_cached", lambda url: None)
    monkeypatch.setattr(oe, "_cache_url_verdict", lambda *a, **k: None)
    monkeypatch.setattr(oe, "_verify_result", lambda *a, **k: True)
    monkeypatch.setattr(oe, "_finalize_url", lambda *a, **k: None)
    monkeypatch.setattr(oe, "_grok_summarize_html", lambda *a, **k: "He served.")
    monkeypatch.setattr(
        url_fetch,
        "fetch",
        lambda url: url_fetch.FetchResult(url=url, source=url_fetch.LIVE, content=HTML),
    )


def test_an_openserp_find_carries_its_kept_page(monkeypatch, queued_finds):
    _fresh_page(monkeypatch)
    origin = oe.find_queue.entity_origin("person", {"PersonID": "01HPERSON"})
    assert oe.find_page(URL, "John Doe", "", "ctx", object(), origin)
    (event,) = queued_finds("openserp-find")
    params = event["request_params"]
    assert params["page_kept"] is True and "page_html" not in params
    assert find_pages.load(params) == HTML.encode()


def test_a_cached_summary_reuses_the_page_and_its_real_fetch_time(monkeypatch):
    _fresh_page(monkeypatch)
    first = oe.process_positive_url(URL, "John Doe", "", "ctx", object())
    monkeypatch.setattr(oe, "_url_verdict_cached", lambda url: "He served.")
    monkeypatch.setattr(url_fetch, "fetch", lambda url: 1 / 0)  # no fetch
    second = oe.process_positive_url(URL, "John Doe", "", "ctx", object())
    assert second["page_sha256"] == first["page_sha256"]
    assert isinstance(first["page_fetched_at"], int)
    assert second["fetched_at"] == first["page_fetched_at"]  # not stamped "now"


def test_a_cached_summary_with_no_kept_page_is_queued_without_one(
    monkeypatch, queued_finds
):
    oe.reset_metrics()
    monkeypatch.setattr(oe, "_url_verdict_cached", lambda url: "He served.")
    monkeypatch.setattr(oe, "_verify_result", lambda *a, **k: True)
    origin = oe.find_queue.entity_origin("person", {"PersonID": "01HPERSON"})
    assert oe.find_page(URL, "John Doe", "", "ctx", object(), origin)
    (event,) = queued_finds("openserp-find")
    assert event["request_params"]["page_kept"] is False
    assert event["request_params"]["fetched_at"] is None  # unknown, not "now"
    assert oe.get_metrics()["pages_not_kept"] == 1


def test_a_failed_pointer_write_keeps_the_page_for_this_find(monkeypatch):
    from src.utils.storage import LocalStorage

    monkeypatch.setattr(find_pages, "_RETRY_BASE_S", 0)

    class _NoPointer(LocalStorage):
        def write_json(self, path, data):
            raise OSError("down")

    monkeypatch.setattr(
        find_pages, "_storage", lambda: _NoPointer(find_pages._LOCAL_DIR)
    )
    got = find_pages.keep(URL, HTML)
    assert got["page_kept"] is True and find_pages.load(got) == HTML.encode()


def test_an_oversized_page_is_not_kept(monkeypatch, caplog):
    monkeypatch.setattr(find_pages, "_MAX_BYTES", 10)
    with caplog.at_level(logging.WARNING):
        assert find_pages.keep(URL, HTML) == {"page_kept": False}
    assert "OBSERV find_pages.oversized" in caplog.text


def test_a_checksum_with_a_trailing_newline_is_malformed():
    with pytest.raises(find_pages.PageUnusable, match="malformed"):
        find_pages.load({"page_sha256": "sha256:" + "a" * 64 + "\n"})


def test_pages_not_kept_show_in_the_summary_and_raise_an_anomaly(caplog):
    oe.reset_metrics()
    oe._metric("pages_not_kept", 3)
    snap = oe.get_metrics()
    with caplog.at_level(logging.INFO):
        oe._emit_openserp_summary(snap)
        oe._emit_openserp_anomalies(snap)
    assert "pages_not_kept=3" in caplog.text
    assert "OBSERV openserp.anomaly pages_not_kept=3" in caplog.text


# --- Grok-suggested biography URLs keep their page too ------------------------------------------


def test_a_grok_suggested_find_carries_its_kept_page(monkeypatch, queued_finds):
    from src.extraction import enrich_biographies as eb

    def fetch(url, provenance=None, **k):
        provenance.update(archived_url="https://web.archive.org/web/2019/x")
        return HTML

    monkeypatch.setattr(eb, "_fetch_url_content", fetch)
    monkeypatch.setattr(eb, "_validate_url_relevance", lambda *a: True)
    profile = {"source_urls": [URL]}
    assert eb._queue_suggested_urls(profile, {"PersonID": "01HP"}, "John Doe", object())
    (event,) = queued_finds("llm-find")
    params = event["request_params"]
    assert params["page_retrieved_from"] == "wayback"
    assert find_pages.load(params) == HTML.encode()


# --- approval: Phase 0 gets the page ------------------------------------------------------------


class _S3:
    def __init__(self):
        self.puts = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append(Body)


def _submit(monkeypatch, find):
    monkeypatch.setattr(FR, "list_affirmed", lambda: [find])
    s3 = _S3()
    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (s3, "b"))
    monkeypatch.setattr(FR, "set_review_status", lambda fid, status: True)
    monkeypatch.setattr(FR, "claimed_content", _always_new)
    return FR.submit_affirmed_batch(), s3.puts


class _always_new:
    def __init__(self, *a):
        pass

    def __enter__(self):
        return True

    def __exit__(self, *a):
        return False


def test_an_approved_find_submits_its_kept_page(monkeypatch):
    page = find_pages.keep(URL, HTML)
    find = {"fetch_event_id": "e1", "endpoint": URL, "request_params": page}
    out, puts = _submit(monkeypatch, find)
    assert out["submitted"] == 1 and puts == [HTML.encode()]


def test_an_altered_kept_page_is_not_submitted(monkeypatch, caplog):
    page = find_pages.keep(URL, HTML)
    (find_pages._LOCAL_DIR / "pages" / (page["page_sha256"][7:] + ".html")).write_text(
        "x"
    )
    find = {"fetch_event_id": "e1", "endpoint": URL, "request_params": page}
    with caplog.at_level(logging.ERROR):
        out, puts = _submit(monkeypatch, find)
    assert out["submitted"] == 0 and out["skipped"] == 1 and puts == []
    assert "OBSERV fetch_review.page_unusable fetch_event_id=e1" in caplog.text


def test_a_find_without_a_kept_page_is_submitted_as_a_stub_and_says_so(
    monkeypatch, caplog
):
    find = {
        "fetch_event_id": "e2",
        "endpoint": URL,
        "provider": "openserp-find",
        "request_params": {"page_kept": False},  # its write failed: a warning
    }
    with caplog.at_level(logging.WARNING):
        out, puts = _submit(monkeypatch, find)
    assert out["submitted"] == 1 and b"Affirmed primary-source find" in puts[0]
    assert "OBSERV fetch_review.submitted_stub fetch_event_id=e2" in caplog.text


def test_a_queue_item_cannot_name_a_local_file(monkeypatch, tmp_path):
    secret = tmp_path / "environ"
    secret.write_text("AWS_SECRET_ACCESS_KEY=x")
    find = {"fetch_event_id": "e3", "endpoint": URL, "quarantine_path": str(secret)}
    out, puts = _submit(monkeypatch, find)
    assert b"AWS_SECRET_ACCESS_KEY" not in puts[0]  # the stub, not the file
