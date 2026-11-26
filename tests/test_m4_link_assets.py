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
class _G:
    """A Grok client with one fixed reply (or error)."""

    def __init__(self, reply="YES", boom=False):
        self.reply, self.boom, self.asked = reply, boom, []

    def chat_completion(self, p):
        self.asked.append(p)
        if self.boom:
            raise RuntimeError("boom")
        return self.reply


def test_a_trusted_source_is_asked_about_too():
    """Owner 2026-11-17: Grok judges EVERY link before a human sees it."""
    g = _G("YES")
    enq, verdict = LR.should_enqueue_link(
        "https://ibiblio.org/x.pdf", "t", grok_client=g, whitelist=["ibiblio.org"]
    )
    assert enq and verdict == "relevant" and len(g.asked) == 1


def test_subdomain_whitelist_match():
    assert LR.is_whitelisted("https://hyperwar.ibiblio.org/a.html", ["ibiblio.org"])
    assert not LR.is_whitelisted("https://notibiblio.org/a.html", ["ibiblio.org"])


def test_blacklist_dropped():
    enq, verdict = LR.should_enqueue_link(
        "https://ebay.com/x", "t", blacklist=["ebay.com"], whitelist=["ibiblio.org"]
    )
    assert not enq and verdict == "blacklisted"


def test_grok_relevant_and_irrelevant():
    assert LR.should_enqueue_link(
        "https://x.example/p", "t", grok_client=_G("YES")
    ) == (True, "relevant")
    assert LR.should_enqueue_link("https://x.example/p", "t", grok_client=_G("NO")) == (
        False,
        "irrelevant",
    )


@pytest.mark.parametrize(
    "client",
    [None, _G("UNKNOWN"), _G("I am not sure"), _G(boom=True)],
    ids=["no client", "UNKNOWN", "unusable reply", "error"],
)
def test_when_grok_cannot_say_the_link_is_not_queued(client):
    """The old default queued a link nobody had judged 'when in doubt'."""
    assert LR.should_enqueue_link("https://x.example/p", "t", grok_client=client) == (
        False,
        "undecided",
    )


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
    s = enqueue_doc_links(content, BASE, "XChannel", grok_client=_G("YES"))
    assert s == {
        "links": 3,
        "enqueued": 2,
        "already_queued": 0,
        "dropped": 0,
        "undecided": 0,
        "failed": 0,
        "anchors": 1,
        "in_corpus": 0,
    }
    pend, events = _pending(fp, d)
    assert len(pend) == 2
    assert all(e["provider"] == "link-asset" for e in events)
    p = pend[0]["request_params"]
    assert p["relevancy"] == "relevant" and p["source_doc"] == "XChannel"
    assert p["media_type"] in ("html", "image")
    # traceability: the event carries the source document URL + the resolved link endpoint
    assert p["source_url"] == BASE
    assert pend[0]["endpoint"].startswith("https://www.ibiblio.org/")
    # CRITICAL backfill: the review event carries the originating SourceSectionID ULID key
    assert "origin_SourceSectionID" in p


def test_enqueue_drops_blacklisted_and_irrelevant_and_skips_undecided(local_fp):
    fp, d = local_fp

    class ByUrl:
        def chat_completion(self, p):
            return "YES" if "ibiblio.org/a.pdf" in p else "NO"

    content = "[spam](https://ebay.com/x) [ext](https://unknown.example/p) [ok](https://ibiblio.org/a.pdf)"
    s = enqueue_doc_links(
        content, BASE, "X", grok_client=ByUrl(), blacklist=["ebay.com"]
    )
    assert s["enqueued"] == 1 and s["dropped"] == 2
    assert len(_pending(fp, d)[0]) == 1  # only the ibiblio one
    # nobody judged these: not queued, nothing recorded, asked again next run
    s2 = enqueue_doc_links(
        "[a](https://a.example/p) [b](https://b.example/p)", BASE, "Y", grok_client=None
    )
    assert (s2["undecided"], s2["enqueued"]) == (2, 0) and len(_pending(fp, d)[0]) == 1


def test_enqueue_failsafe_on_bad_input(local_fp):
    fp, d = local_fp
    s = enqueue_doc_links(None, None, "X", grok_client=None)
    assert s["enqueued"] == 0


def test_enqueue_carries_source_section_ulid_and_the_trusted_flag(local_fp):
    """CRITICAL backfill: a provided source_section_id lands as origin_SourceSectionID on the event."""
    fp, d = local_fp
    sid = "01HX7SEC0000000000000000AB"
    enqueue_doc_links(
        "[chart](charts/x.jpg)",
        BASE,
        "XChannel",
        grok_client=_G("YES"),
        whitelist=["ibiblio.org"],
        source_section_id=sid,
    )
    pend, _ = _pending(fp, d)
    p = pend[0]["request_params"]
    assert p["origin_SourceSectionID"] == sid and p["origin_entity_id"] == sid
    assert p["origin_entity_type"] == "source_section"


def test_a_rerun_does_not_queue_the_same_link_again(local_fp):
    """Task 1.2f: every Phase 3 re-run used to add a second PENDING review item per link."""
    fp, d = local_fp
    content = "[fn63](../../MoH_P-T.html#roosevelt) [chart](charts/x.jpg)"
    kw = dict(grok_client=_G("YES"))
    first = enqueue_doc_links(content, BASE, "XChannel", **kw)
    again = enqueue_doc_links(content, BASE, "XChannel", **kw)
    assert first["enqueued"] == 2 and again["enqueued"] == 0
    assert again["already_queued"] == 2
    assert len(_pending(fp, d)[0]) == 2
    # the same link from another book is its own review item
    assert enqueue_doc_links(content, BASE, "OtherBook", **kw)["enqueued"] == 2


def test_a_link_a_human_rejected_is_not_queued_again(local_fp):
    """The review status lives on the event, and the event is recorded once ever."""
    fp, d = local_fp
    from src.enrichment import fetch_review

    kw = dict(grok_client=_G("YES"))
    enqueue_doc_links("[chart](charts/x.jpg)", BASE, "XChannel", **kw)
    event = _pending(fp, d)[0][0]
    assert fetch_review.reject_fetch_find(event)
    again = enqueue_doc_links("[chart](charts/x.jpg)", BASE, "XChannel", **kw)
    assert again["enqueued"] == 0 and again["already_queued"] == 1
    assert _pending(fp, d)[0] == []  # still rejected, not pending


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


def test_a_link_queued_under_the_old_event_id_is_not_queued_again(local_fp):
    """Production already holds link-asset events (some rejected or submitted) keyed by the old
    once_key (book, section-or-url, url): the shared queue must recognise them, with no Grok request.
    """
    fp, d = local_fp
    url = "https://example.org/old.jpg"
    fp.record_fetch_event(
        provider="link-asset",
        endpoint=url,
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
        once_key=("XChannel", BASE, url),
    )
    g = _G("YES")
    s = enqueue_doc_links(f"[chart]({url})", BASE, "XChannel", grok_client=g)
    assert s["already_queued"] == 1 and s["enqueued"] == 0
    assert g.asked == []  # nothing spent on a link already recorded


def test_an_already_recorded_link_costs_no_grok_request(local_fp):
    fp, d = local_fp
    g = _G("YES")
    content = "[chart](charts/x.jpg)"
    enqueue_doc_links(content, BASE, "XChannel", grok_client=g)
    assert len(g.asked) == 1
    enqueue_doc_links(content, BASE, "XChannel", grok_client=g)
    assert len(g.asked) == 1


def test_a_refused_find_is_counted_as_dropped(local_fp, monkeypatch):
    from src.utils import find_queue

    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.REFUSED)
    s = enqueue_doc_links("[c](charts/x.jpg)", BASE, "XChannel", grok_client=_G("YES"))
    assert s["dropped"] == 1 and s["already_queued"] == 0 and s["enqueued"] == 0


def test_a_malformed_link_target_is_rejected_not_repaired():
    """A line break, control character or inner space makes the link malformed: it is skipped."""
    for bad in (
        "https://example.org/a\nOBSERV fake/x.jpg",
        "https://example.org/a b.jpg",
        "https://example.org/a\x1b[0m.jpg",
    ):
        c = f'<a href="{bad}">t</a>'
        assert not [a for a in extract_link_assets(c, BASE) if "example.org" in a.url]


def test_a_dropped_link_log_stays_on_one_line(local_fp, caplog):
    import logging

    c = '<a href="https://example.org/a\x1b[31m.jpg">t</a>'
    with caplog.at_level(logging.INFO):
        enqueue_doc_links(c, BASE, "XChannel", grok_client=_G("NO"))
    assert all("\x1b" not in r.getMessage() for r in caplog.records)


def test_a_failed_store_is_counted_as_failed_not_already_queued(local_fp, monkeypatch):
    from src.utils import find_queue

    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.FAILED)
    s = enqueue_doc_links("[c](charts/x.jpg)", BASE, "XChannel", grok_client=_G("YES"))
    assert s["failed"] == 1 and s["already_queued"] == 0 and s["enqueued"] == 0


def test_in_corpus_links_are_left_to_intra_corpus_links(local_fp):
    """Links to another HyperWar volume, or to a page of this one, are indexed by
    intra_corpus_links only (owner decision, TODO Group 9): no Grok request, nothing queued.
    """
    fp, d = local_fp

    class Never:
        def chat_completion(self, p):
            raise AssertionError("an in-corpus link was sent to Grok")

    base = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-6.html"
    content = (
        "[Omaha](https://www.ibiblio.org/hyperwar/USA/USA-E-Omaha/index.html) "
        "[next](USA-E-XChannel-7.html)"
    )
    s = enqueue_doc_links(content, base, "XChannel", grok_client=Never())
    assert (s["links"], s["in_corpus"], s["enqueued"]) == (2, 2, 0)
    assert _pending(fp, d)[0] == []


def test_a_look_alike_hyperwar_link_is_still_reviewed(local_fp):
    """A /hyperwar/ path on another host is not in the corpus: it is judged and queued as usual."""
    fp, d = local_fp
    s = enqueue_doc_links(
        "[x](https://evil.com/hyperwar/USA/USA-E-Omaha/index.html)",
        "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-6.html",
        "XChannel",
        grok_client=_G("YES"),
    )
    assert (s["in_corpus"], s["enqueued"]) == (0, 1)
