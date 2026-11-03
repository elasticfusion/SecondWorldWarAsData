"""DRY P2.5 guard: chart_source_adapter.render_chart_markdown and
map_source_adapter.render_map_markdown now share _entity_common.render_source_markdown. Pin the
shared layout + that each adapter's output is byte-identical to the old hand-rolled render.
"""

from src.extraction._entity_common import render_source_markdown


def test_render_source_markdown_layout():
    out = render_source_markdown(
        "Body text here.",
        "My Title",
        "http://u",
        default_title="WWII map",
        source_label="map",
    )
    assert out == "## My Title\n\n(Source map: http://u)\n\nBody text here.\n"


def test_empty_text_returns_empty():
    assert (
        render_source_markdown("", "T", "u", default_title="D", source_label="x") == ""
    )


def test_default_title_and_no_source_url():
    out = render_source_markdown(
        "B", "", "", default_title="WWII command chart", source_label="chart"
    )
    assert out == "## WWII command chart\n\nB\n"


def test_chart_render_matches_old_shape(monkeypatch):
    import src.extraction.chart_source_adapter as cs

    monkeypatch.setattr(cs, "_render_chart_text", lambda d: "First Army at Avranches.")
    out = cs.render_chart_markdown("Cobra Chart", {"x": 1}, source_url="http://c")
    assert (
        out
        == "## Cobra Chart\n\n(Source chart: http://c)\n\nFirst Army at Avranches.\n"
    )
    # empty depicted -> ""
    monkeypatch.setattr(cs, "_render_chart_text", lambda d: "")
    assert cs.render_chart_markdown("T", {}, source_url="u") == ""


def test_map_render_matches_old_shape(monkeypatch):
    import src.extraction.map_source_adapter as ms

    monkeypatch.setattr(ms, "_render_depicted_text", lambda d: "Omaha Beach sector.")
    out = ms.render_map_markdown("", {"x": 1}, source_url="")
    assert (
        out == "## WWII map\n\nOmaha Beach sector.\n"
    )  # default title, no source line
