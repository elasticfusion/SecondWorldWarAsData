"""M4: follow ALL links in parsed HTML -> recognize -> relevancy-gate -> enqueue for human review."""

import json
import pathlib
import tempfile
from unittest import mock

import pytest

from src.extraction.link_assets import extract_link_assets, enqueue_doc_links
from src.extraction import link_relevancy as LR

BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html"


# --- recognizer ---
def test_recognize_types_and_resolve():
    content = (
        "[a](../../MoH_P-T.html#roosevelt) [b](charts/x.jpg) [c](reports/r.pdf) "
        "[d](scan/hi.tif) [e](media/v.mp4) [f](#pinder) "
        '<a href="https://en.wikipedia.org/wiki/Omaha_Beach">w</a>'
    )
    by_text = {la.text: la for la in extract_link_assets(content, BASE)}
    assert by_text["a"].media_type == "html"
    assert by_text["a"].url == "https://www.ibiblio.org/hyperwar/MoH_P-T.html#roosevelt"
    assert by_text["b"].media_type == "image"
    assert by_text["c"].media_type == "pdf"
    assert by_text["d"].media_type == "image"  # .tif
    assert by_text["e"].media_type == "moving_image"  # video
    assert by_text["f"].is_anchor and by_text["f"].media_type == "unsupported"
    assert by_text["w"].media_type == "unsupported"  # extensionless page


def test_excludes_images_and_mailto():
    content = "![photo](icn/37.jpg) [mail](mailto:x@y.z) [ok](docs/a.pdf)"
    out = extract_link_assets(content, BASE)
    assert [la.text for la in out] == ["ok"]


def test_dedup_by_resolved_url():
    content = "[one](charts/x.jpg) [again](charts/x.jpg)"
    assert len(extract_link_assets(content, BASE)) == 1


# --- relevancy gate ---
def test_whitelist_bypasses_grok():
    class Boom:
        def chat_completion(self, p):
            raise AssertionError("grok should NOT be called for a whitelisted domain")

    enq, verdict = LR.should_enqueue_link(
        "https://ibiblio.org/x.pdf", "t", grok_client=Boom(), whitelist=["ibiblio.org"]
    )
    assert enq and verdict == "whitelisted"


def test_subdomain_whitelist_match():
    assert LR.is_whitelisted("https://hyperwar.ibiblio.org/a.html", ["ibiblio.org"])
    assert not LR.is_whitelisted("https://notibiblio.org/a.html", ["ibiblio.org"])


def test_blacklist_dropped():
    enq, verdict = LR.should_enqueue_link(
        "https://ebay.com/x", "t", blacklist=["ebay.com"], whitelist=["ibiblio.org"]
    )
    assert not enq and verdict == "blacklisted"


def test_grok_relevant_and_irrelevant():
    class Yes:
        def chat_completion(self, p):
            return "YES"

    class No:
        def chat_completion(self, p):
            return "NO"

    enq, v = LR.should_enqueue_link(
        "https://x.example/p", "t", grok_client=Yes(), whitelist=[]
    )
    assert enq and v == "relevant"
    enq2, v2 = LR.should_enqueue_link(
        "https://x.example/p", "t", grok_client=No(), whitelist=[]
    )
    assert not enq2 and v2 == "irrelevant"


def test_grok_error_failsafe_enqueue():
    class Err:
        def chat_completion(self, p):
            raise RuntimeError("boom")

    enq, v = LR.should_enqueue_link(
        "https://x.example/p", "t", grok_client=Err(), whitelist=[]
    )
    assert enq and v == "relevant"  # fail-safe: when in doubt, enqueue


def test_without_a_list_the_trusted_sources_decide(monkeypatch):
    """Task 1.9: the configured trusted sources replace the wider ibiblio.org whitelist."""
    import src.utils.config as config

    cfg = {
        "trusted_sources": [{"name": "hyperwar", "match": ["ibiblio.org/hyperwar/"]}]
    }
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    assert LR.is_whitelisted("https://www.ibiblio.org/hyperwar/USA/x.html")
    assert not LR.is_whitelisted("https://www.ibiblio.org/pub/other.html")


def test_an_untrusted_ibiblio_link_is_asked_about(monkeypatch):
    """The intended change: only HyperWar skips the Grok check, not all of ibiblio.org."""
    import src.utils.config as config

    cfg = {
        "trusted_sources": [{"name": "hyperwar", "match": ["ibiblio.org/hyperwar/"]}]
    }
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    asked = []

    class G:
        def chat_completion(self, p):
            asked.append(p)
            return "YES"

    enq, verdict = LR.should_enqueue_link(
        "https://www.ibiblio.org/pub/x.html", "t", grok_client=G()
    )
    assert enq and verdict == "relevant" and len(asked) == 1


# --- enqueue (real fetch-provenance write path) ---
@pytest.fixture
def local_fp(monkeypatch):
    import src.utils.fetch_provenance as fp

    d = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(fp, "_LOCAL_DIR", d)
    return fp, d


def _pending(fp, d):
    events = []
    for jf in d.glob("*.jsonl"):
        for ln in jf.read_text().splitlines():
            if ln.strip():
                events.append(json.loads(ln))
    return [e for e in events if e.get("review_status") == fp.PENDING], events


def test_enqueue_records_pending_with_provenance(local_fp):
    fp, d = local_fp
    content = "[fn63](../../MoH_P-T.html#roosevelt) [chart](charts/x.jpg) [top](#top)"
    s = enqueue_doc_links(
        content, BASE, "XChannel", grok_client=None, whitelist=["ibiblio.org"]
    )
    assert s == {
        "links": 3,
        "enqueued": 2,
        "already_queued": 0,
        "dropped": 0,
        "anchors": 1,
    }
    pend, events = _pending(fp, d)
    assert len(pend) == 2
    assert all(e["provider"] == "link-asset" for e in events)
    p = pend[0]["request_params"]
    assert p["relevancy"] == "whitelisted" and p["source_doc"] == "XChannel"
    assert p["media_type"] in ("html", "image")
    # traceability: the event carries the source document URL + the resolved link endpoint
    assert p["source_url"] == BASE
    assert pend[0]["endpoint"].startswith("https://www.ibiblio.org/")
    # CRITICAL backfill: the review event carries the originating SourceSectionID ULID key
    assert "origin_SourceSectionID" in p


def test_enqueue_drops_blacklisted_and_irrelevant(local_fp):
    fp, d = local_fp

    class No:
        def chat_completion(self, p):
            return "NO"

    content = "[spam](https://ebay.com/x) [ext](https://unknown.example/p) [ok](https://ibiblio.org/a.pdf)"
    s = enqueue_doc_links(
        content,
        BASE,
        "X",
        grok_client=No(),
        whitelist=["ibiblio.org"],
        blacklist=["ebay.com"],
    )
    assert s["enqueued"] == 1 and s["dropped"] == 2
    pend, _ = _pending(fp, d)
    assert len(pend) == 1  # only the ibiblio one


def test_enqueue_failsafe_on_bad_input(local_fp):
    fp, d = local_fp
    s = enqueue_doc_links(None, None, "X", grok_client=None)
    assert s["enqueued"] == 0


def test_enqueue_carries_source_section_ulid(local_fp):
    """CRITICAL backfill: a provided source_section_id lands as origin_SourceSectionID on the event."""
    fp, d = local_fp
    sid = "01HX7SEC0000000000000000AB"
    enqueue_doc_links(
        "[chart](charts/x.jpg)",
        BASE,
        "XChannel",
        grok_client=None,
        whitelist=["ibiblio.org"],
        source_section_id=sid,
    )
    pend, _ = _pending(fp, d)
    assert pend and pend[0]["request_params"]["origin_SourceSectionID"] == sid


def test_a_rerun_does_not_queue_the_same_link_again(local_fp):
    """Task 1.2f: every Phase 3 re-run used to add a second PENDING review item per link."""
    fp, d = local_fp
    content = "[fn63](../../MoH_P-T.html#roosevelt) [chart](charts/x.jpg)"
    kw = dict(grok_client=None, whitelist=["ibiblio.org"])
    first = enqueue_doc_links(content, BASE, "XChannel", **kw)
    again = enqueue_doc_links(content, BASE, "XChannel", **kw)
    assert first["enqueued"] == 2 and again["enqueued"] == 0
    assert again["already_queued"] == 2
    assert len(_pending(fp, d)[0]) == 2
    # the same link from another book is its own review item
    assert enqueue_doc_links(content, BASE, "OtherBook", **kw)["enqueued"] == 2


def test_a_queued_review_item_is_never_overwritten_in_dynamodb(monkeypatch):
    import src.utils.fetch_provenance as fp

    table = mock.Mock()
    table.put_item.side_effect = [None, _ConditionalCheckFailed()]
    monkeypatch.setattr(fp, "_table", lambda: table)
    args = dict(provider="link-asset", endpoint="https://x.org/a", kind=fp.TEXTUAL)
    first = fp.record_fetch_event(**args, once_key=("B", "S1", "https://x.org/a"))
    again = fp.record_fetch_event(**args, once_key=("B", "S1", "https://x.org/a"))
    assert first and again == ""
    cond = table.put_item.call_args_list[0].kwargs["ConditionExpression"]
    assert cond == "attribute_not_exists(cache_key)"
    keys = {c.kwargs["Item"]["cache_key"] for c in table.put_item.call_args_list}
    assert len(keys) == 1  # the same deterministic id both times


class _ConditionalCheckFailed(Exception):
    pass


_ConditionalCheckFailed.__name__ = "ConditionalCheckFailedException"


def test_a_pipe_in_a_url_cannot_collide_two_review_items():
    from src.utils.deterministic_ids import fetch_event_id

    assert fetch_event_id("B", "s|x", "u") != fetch_event_id("B", "s", "x|u")


def test_a_review_item_that_was_not_stored_is_said(monkeypatch, caplog):
    import src.utils.fetch_provenance as fp

    table = mock.Mock()
    table.put_item.side_effect = RuntimeError("ProvisionedThroughputExceeded")
    monkeypatch.setattr(fp, "_table", lambda: table)
    out = fp.record_fetch_event(
        provider="link-asset",
        endpoint="https://x.org/a",
        kind=fp.TEXTUAL,
        once_key=("B",),
    )
    assert out == "" and "OBSERV fetch_provenance.write_failed" in caplog.text
