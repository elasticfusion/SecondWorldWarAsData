"""M4: follow ALL links in parsed HTML -> recognize -> relevancy-gate -> enqueue for human review."""

import json
import pathlib
import tempfile

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


def test_whitelist_loader_failsafe_default(tmp_path):
    missing = tmp_path / "nope.yaml"
    assert LR.load_link_relevancy_whitelist(missing) == ["ibiblio.org"]


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
    assert s == {"links": 3, "enqueued": 2, "dropped": 0, "anchors": 1}
    pend, events = _pending(fp, d)
    assert len(pend) == 2
    assert all(e["provider"] == "link-asset" for e in events)
    p = pend[0]["request_params"]
    assert p["relevancy"] == "whitelisted" and p["source_doc"] == "XChannel"
    assert p["media_type"] in ("html", "image")
    # traceability: the event carries the source document URL + the resolved link endpoint
    assert p["source_url"] == BASE
    assert pend[0]["endpoint"].startswith("https://www.ibiblio.org/")


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
