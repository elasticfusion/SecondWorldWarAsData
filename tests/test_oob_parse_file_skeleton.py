"""DRY P1.7 guard: the 7 near-identical parse_<section>_file(path) wrappers (read md -> call
parse_<section> -> log canonical summary -> return) now share _common.parse_markdown_file. The
per-section parse_<section>(markdown) cores stay distinct. These tests pin the shared wrapper's
contract + that every wrapper routes through it.
"""

import logging
from pathlib import Path

from src.ingestion.oob_markdown._common import parse_markdown_file


class _Result:
    def __init__(self):
        self.rows = [1, 2, 3]
        self.review_count = 1


def test_parse_markdown_file_contract(tmp_path, caplog):
    p = tmp_path / "ch3-content.md"
    p.write_text("SOME MARKDOWN", encoding="utf-8")
    seen = {}

    def fake_parse(markdown, source_file=""):
        seen["markdown"] = markdown
        seen["source_file"] = source_file
        return _Result()

    with caplog.at_level(logging.INFO):
        result = parse_markdown_file(p, fake_parse, "widget")

    assert seen["markdown"] == "SOME MARKDOWN"  # file was read
    assert seen["source_file"] == "ch3-content.md"  # source_file = path.name
    assert result.rows == [1, 2, 3]  # result passed through
    # canonical log line: "Parsed <name>: <n> <noun> row(s), <r> flagged for review"
    assert "Parsed ch3-content.md: 3 widget row(s), 1 flagged for review" in caplog.text


def test_all_seven_wrappers_route_through_helper(tmp_path, monkeypatch):
    # Spy parse_markdown_file; call each wrapper; assert it was invoked with (path, parse_fn, noun).
    calls = []

    def spy(path, parse_fn, noun):
        calls.append((Path(path).name, parse_fn.__name__, noun))

        class _R:
            rows = []
            review_count = 0

        return _R()

    import src.ingestion.oob_markdown.attachments as attachments
    import src.ingestion.oob_markdown.campaigns as campaigns
    import src.ingestion.oob_markdown.command_posts as command_posts
    import src.ingestion.oob_markdown.command_staff as command_staff
    import src.ingestion.oob_markdown.higher_units as higher_units
    import src.ingestion.oob_markdown.organic_units as organic_units
    import src.ingestion.oob_markdown.statistics as statistics

    # Each wrapper did `from ..._common import parse_markdown_file`, so patch the name BOUND in
    # each module (not the _common source).
    for mod in (
        attachments,
        campaigns,
        command_posts,
        command_staff,
        higher_units,
        organic_units,
        statistics,
    ):
        monkeypatch.setattr(mod, "parse_markdown_file", spy)

    p = tmp_path / "x-content.md"
    p.write_text("", encoding="utf-8")

    attachments.parse_attachments_file(p)
    campaigns.parse_campaigns_file(p)
    command_posts.parse_command_posts_file(p)
    command_staff.parse_command_staff_file(p)
    higher_units.parse_higher_units_file(p)
    organic_units.parse_organic_units_file(p)
    statistics.parse_statistics_file(p)

    nouns = {noun for (_n, _fn, noun) in calls}
    assert nouns == {
        "attachment",
        "campaign",
        "command-post",
        "command-staff",
        "higher-unit",
        "organic-unit",
        "statistic",
    }
    assert len(calls) == 7  # all 7 wrappers delegated
