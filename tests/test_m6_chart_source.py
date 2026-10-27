"""M6: chart-as-source adapter — vision command chart -> parsed source -> event spine, with ULID
tracking to the original event."""

import os
import pathlib

import pytest

from src.extraction import chart_source_adapter as C

_DEP = {
    "organization": "Allied Naval Command, Operation NEPTUNE",
    "description": "Naval command structure for the assault.",
    "units": ["Western Naval Task Force", "Eastern Naval Task Force"],
    "commanders": ["Admiral Ramsay", "Rear Admiral Kirk"],
    "command_edges": [
        {"superior": "Admiral Ramsay", "subordinate": "Rear Admiral Kirk"},
        {"superior": "Rear Admiral Kirk", "subordinate": "Western Naval Task Force"},
    ],
}


# --- rendering ---
def test_render_command_edges():
    txt = C._render_command_edges(_DEP["command_edges"])
    assert "Admiral Ramsay commands Rear Admiral Kirk." in txt
    assert "Rear Admiral Kirk commands Western Naval Task Force." in txt


def test_render_chart_text_grounded():
    t = C._render_chart_text(_DEP)
    assert "Command relationships:" in t and "Commanders named:" in t
    assert C._render_chart_text({}) == ""  # nothing fabricated


# --- parsed source + ULID tracking ---
def test_build_parsed_source_carries_origin_ulids():
    p = C.build_chart_parsed_source(
        chart_id="USA-E-XChannel-4",
        chart_title="Naval Command Chart",
        approved_image_url="https://ibiblio.org/x/charts/USA-E-XChannel-4.jpg",
        depicted=_DEP,
        source_url="https://ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html",
        origin_event_id="01HX7EVENT000000000000000A",
        origin_source_section_id="01HX7SEC0000000000000000B",
    )
    assert p["source_type"] == "chart"
    assert p["_chart_source"]["origin_EventID"] == "01HX7EVENT000000000000000A"
    assert p["_chart_source"]["origin_SourceSectionID"] == "01HX7SEC0000000000000000B"
    assert p["_chart_source"]["acquisition_method"] == "chart_vision"


def test_build_parsed_source_none_on_empty():
    assert (
        C.build_chart_parsed_source(
            chart_id="x", chart_title="x", approved_image_url="", depicted={}
        )
        is None
    )


# --- recycle (filesystem) ---
@pytest.fixture
def chdir_tmp(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old)


def test_recycle_writes_pair_with_origin_ulids(chdir_tmp):
    ok = C.recycle_chart_as_source(
        chart_id="USA-E-XChannel-4",
        chart_title="Naval Command Chart",
        source_url="https://ibiblio.org/x/USA-E-XChannel-8.html",
        depicted=_DEP,
        origin_event_id="01HX7EVENT000000000000000A",
        origin_source_section_id="01HX7SEC0000000000000000B",
    )
    assert ok
    base = chdir_tmp / "contentrepository" / "chart-USA-E-XChannel-4"
    meta = (base / "chart-USA-E-XChannel-4-meta.yaml").read_text()
    content = (base / "chart-USA-E-XChannel-4a-content.md").read_text()
    assert "source_type: chart" in meta
    assert "origin_EventID: 01HX7EVENT000000000000000A" in meta
    assert "origin_SourceSectionID: 01HX7SEC0000000000000000B" in meta
    assert "commands" in content


def test_recycle_skips_empty_depicted(chdir_tmp):
    assert (
        C.recycle_chart_as_source(
            chart_id="x", chart_title="x", source_url="", depicted={}
        )
        is False
    )


# --- ingest from an affirmed chart link (vision -> recycle, origin ULIDs threaded) ---
class _GrokChart:
    def extract_json_with_image_base64(
        self, prompt, image_base64, cache_type="default"
    ):
        return {"is_relevant": True, "depicted": _DEP}


class _GrokNotChart:
    def extract_json_with_image_base64(
        self, prompt, image_base64, cache_type="default"
    ):
        return {"is_relevant": False, "depicted": None}


def _event(**params):
    return {
        "fetch_event_id": "01HXFE00000000000000000000",
        "endpoint": "https://ibiblio.org/x/charts/USA-E-XChannel-4.jpg",
        "request_params": params,
    }


def test_ingest_affirmed_chart_threads_origin(chdir_tmp):
    ev = _event(
        link_text="naval command chart",
        source_url="https://ibiblio.org/x/USA-E-XChannel-8.html",
        source_doc="XChannel",
        origin_EventID="01HX7EVENT000000000000000A",
        origin_SourceSectionID="01HX7SEC0000000000000000B",
    )
    assert C.ingest_affirmed_chart(ev, b"bytes", grok_client=_GrokChart())
    meta = (
        chdir_tmp
        / "contentrepository"
        / "chart-USA-E-XChannel-4"
        / "chart-USA-E-XChannel-4-meta.yaml"
    ).read_text()
    assert "origin_EventID: 01HX7EVENT000000000000000A" in meta


def test_ingest_rejects_non_chart(chdir_tmp):
    ev = _event(link_text="a photo", source_doc="X")
    assert C.ingest_affirmed_chart(ev, b"bytes", grok_client=_GrokNotChart()) is False
    assert not (chdir_tmp / "contentrepository").exists()


def test_ingest_failsafe_on_bad_client(chdir_tmp):
    class Boom:
        def extract_json_with_image_base64(self, **k):
            raise RuntimeError("x")

    ev = _event(link_text="chart", source_doc="X")
    assert C.ingest_affirmed_chart(ev, b"bytes", grok_client=Boom()) is False
