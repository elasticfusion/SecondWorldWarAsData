"""Vision requests in batch mode (Group 2): a cache miss is queued and the answer, which the batch
caches as TEXT under the request's key, is parsed when it is read."""

import base64
import hashlib
import json

import pytest

from src.grok_client import BatchModeCollecting, GrokAPIError, GrokClient
from src.utils.batch_api import BatchCollector, BatchRequest

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    c = GrokClient(cache_dir=tmp_path, batch_mode=True)
    c._batch_collector = BatchCollector()
    return c


def _live(content):
    calls = []

    def call(messages, temperature=0.1, model=""):
        calls.append(messages)
        return {"choices": [{"message": {"content": content}}]}

    return calls, call


def test_a_miss_in_batch_mode_is_queued_with_the_image_and_never_called_live(
    client, monkeypatch
):
    calls, call = _live("{}")
    monkeypatch.setattr(client, "_call_api", call)
    with pytest.raises(BatchModeCollecting):
        client.extract_json_with_image_base64(
            "is it a map?", PNG, system_prompt="sys", cache_type="in_doc_map_vision"
        )
    assert calls == []
    (req,) = client._batch_collector.requests
    assert req.cache_type == "in_doc_map_vision"
    assert [m["role"] for m in req.messages] == ["system", "user"]
    parts = req.messages[1]["content"]
    assert parts[0] == {"type": "text", "text": "is it a map?"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    line = json.loads(req.to_jsonl())  # it serialises as one xAI batch line
    assert line["custom_id"] == req.request_id and line["body"]["messages"]


def test_the_url_variant_fetches_safely_then_queues(client, monkeypatch):
    monkeypatch.setattr(
        client, "_fetch_image_as_data_url", lambda url: "data:image/png;base64,AAAA"
    )
    with pytest.raises(BatchModeCollecting):
        client.extract_json_with_image("p", "https://example.org/m.png")
    assert len(client._batch_collector) == 1


def test_a_blocked_image_is_an_error_not_a_queued_request(client, monkeypatch):
    def blocked(url):
        raise GrokAPIError("Image fetch blocked (reason=ssrf_blocked)")

    monkeypatch.setattr(client, "_fetch_image_as_data_url", blocked)
    with pytest.raises(GrokAPIError):
        client.extract_json_with_image("p", "http://169.254.169.254/x.png")
    assert len(client._batch_collector) == 0


def test_the_batch_answer_is_read_back_as_parsed_json_under_the_same_key(client):
    with pytest.raises(BatchModeCollecting):
        client.extract_json_with_image_base64("p", PNG, cache_type="in_doc_map_vision")
    key = client._batch_collector.requests[0].request_id
    # what the batch retrieval stores: the answer's text, under the request id
    client._get_cache("in_doc_map_vision")[key] = '```json\n{"is_map": true}\n```'
    client.batch_mode = False  # the re-run
    assert client.extract_json_with_image_base64(
        "p", PNG, cache_type="in_doc_map_vision"
    ) == {"is_map": True}


def test_a_live_answer_is_cached_parsed_and_read_again_without_a_call(
    client, monkeypatch
):
    client.batch_mode = False
    calls, call = _live('{"is_map": false}')
    monkeypatch.setattr(client, "_call_api", call)
    for _ in range(2):
        assert client.extract_json_with_image_base64("p", PNG) == {"is_map": False}
    assert len(calls) == 1


def test_an_unparseable_batch_answer_is_evicted_and_raises(client):
    with pytest.raises(BatchModeCollecting):
        client.extract_json_with_image_base64("p", PNG)
    key = client._batch_collector.requests[0].request_id
    cache = client._get_cache("default")
    cache[key] = "not json at all"
    client.batch_mode = False
    with pytest.raises(GrokAPIError):
        client.extract_json_with_image_base64("p", PNG)
    assert key not in cache  # the next pass asks again


def test_a_cached_answer_is_used_in_batch_mode_too(client):
    client.batch_mode = False
    client._get_cache("default")  # live path below
    client._call_api = lambda *a, **k: {"choices": [{"message": {"content": "{}"}}]}
    client.extract_json_with_image_base64("p", PNG)
    client.batch_mode = True
    assert client.extract_json_with_image_base64("p", PNG) == {}
    assert len(client._batch_collector) == 0


def test_the_batch_key_is_the_live_key(client):
    """A batch submit and the re-run's lookup must meet at one key (the image hash is in it)."""
    with pytest.raises(BatchModeCollecting):
        client.extract_json_with_image_base64("p", PNG)
    other = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"1" * 32).decode()
    with pytest.raises(BatchModeCollecting):
        client.extract_json_with_image_base64("p", other)
    keys = [r.request_id for r in client._batch_collector.requests]
    assert len(set(keys)) == 2


def test_the_same_image_asked_twice_is_one_request(client):
    for _ in range(2):
        with pytest.raises(BatchModeCollecting):
            client.extract_json_with_image_base64("p", PNG)
    assert len(client._batch_collector) == 1
    assert client._batch_collector.duplicates == 1


class _Collecting:
    """A batch-mode client: every vision call is queued, so it raises BatchModeCollecting."""

    def _boom(self, *a, **k):
        raise BatchModeCollecting("k")

    extract_json_with_image = _boom
    extract_json_with_image_base64 = _boom


def test_direct_vision_callers_pass_collecting_through_instead_of_recording_a_verdict():
    """A queued request is not an answer: no caller may turn it into a rejection, an 'error'
    caption or a stamped 'checked'."""
    from src.enrichment.unit_patch import _vision_is_patch
    from src.extraction.grok_search_maps import verify_map_with_vision
    from src.extraction.image_captioner import ImageCaptioner
    from src.extraction.map_link_source import _vision_depicted

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    png = buf.getvalue()
    c = _Collecting()
    with pytest.raises(BatchModeCollecting):
        _vision_depicted(c, "https://example.org/m.png", "Map", {}, 5)
    with pytest.raises(BatchModeCollecting):
        _vision_is_patch("https://example.org/p.png", "1st Infantry Division", "US", c)
    with pytest.raises(BatchModeCollecting):
        ImageCaptioner(c).caption(png, {})
    with pytest.raises(BatchModeCollecting):
        verify_map_with_vision(png, "Omaha", None, "ctx", "Map", c)


def test_search_external_maps_and_video_frames_pass_collecting_through(monkeypatch):
    from src.extraction import search_external_maps as sem
    from src.ingestion.video_vision import GrokVisionAnalyzer

    monkeypatch.setattr(sem, "_download_page", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(sem, "_extract_map_images", lambda *a, **k: ["img"])
    monkeypatch.setattr(sem, "_verify_single_image", lambda *a, **k: _queued())
    # the page-level verifier must not turn a queued image into "rejected"
    with pytest.raises(BatchModeCollecting):
        sem._verify_map_relevance(
            "http://x/", "t", "d", "Omaha", None, "ctx", _Collecting()
        )
    with pytest.raises(BatchModeCollecting):
        GrokVisionAnalyzer(_Collecting()).analyze_frame(b"frame", [], "")


def _queued():
    raise BatchModeCollecting("k")


def test_the_credit_estimate_and_preview_ignore_the_image_bytes(client):
    from src.grok_client import _message_images, _message_text

    big = "A" * 3_000_000
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "is it a map?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{big}"}},
        ],
    }
    assert _message_text(msg) == "is it a map?" and _message_images(msg) == 1
    assert _message_text({"role": "user", "content": "plain"}) == "plain"
    assert _message_images({"role": "user", "content": "plain"}) == 0
    client._batch_collector.add(BatchRequest("k", [msg], "grok-x", 0.1, "default"))
    # ~1500 tokens for the image, not ~750k for its base64
    assert client._estimate_batch_cost() < 0.05
    assert (
        client._batch_prompt_preview("k", {"k": client._batch_collector.requests[0]})
        == "is it a map?"
    )


def test_a_live_answer_that_is_a_bare_string_is_not_cached_as_batch_text(
    client, monkeypatch
):
    client.batch_mode = False
    _, call = _live('"abc"')
    monkeypatch.setattr(client, "_call_api", call)
    assert client.extract_json_with_image_base64("p", PNG) == "abc"
    key = client._make_cache_key(
        f"p:img_{hashlib.sha256(PNG.encode()).hexdigest()[:16]}",
        0.1,
        client._get_model("default"),
    )
    assert key not in client._get_cache("default")


def test_the_live_vision_call_uses_the_model_that_is_in_the_cache_key(
    client, monkeypatch
):
    client.batch_mode = False
    monkeypatch.setattr(
        client, "_get_model", lambda cache_type: "model-for-" + cache_type
    )
    seen = []

    def call(messages, temperature=0.1, model=""):
        seen.append(model)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(client, "_call_api", call)
    client.extract_json_with_image_base64("p", PNG, cache_type="vision_verification")
    monkeypatch.setattr(
        client, "_fetch_image_as_data_url", lambda u: "data:image/png;base64,AA"
    )
    client.extract_json_with_image(
        "p", "https://example.org/a.png", cache_type="unit_patch_vision"
    )
    assert seen == ["model-for-vision_verification", "model-for-unit_patch_vision"]
