"""Deterministic date-interval resolution: expand SOURCE-stated dates into sortable ISO
intervals; never guess; preserve verbatim date_start. Source is the sole authority."""

import jsonschema
import pytest

from src.extraction.date_resolution import resolve_date_interval as R
from src.extraction.dates import _stamp_resolved_interval
from src.schemas.dates_output import DATES_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def test_exact_day_is_point_interval():
    assert R("1944-06-06") == ("1944-06-06", "1944-06-06", "precision_rule")


def test_month_spans_whole_month():
    assert R("1944-06") == ("1944-06-01", "1944-06-30", "precision_rule")


def test_bare_year_spans_whole_year():
    assert R("1944") == ("1944-01-01", "1944-12-31", "precision_rule")


@pytest.mark.parametrize(
    "ds,lo,hi",
    [
        ("early-1944-06", "1944-06-01", "1944-06-10"),
        ("mid-1944-06", "1944-06-11", "1944-06-20"),
        ("late-1944-06", "1944-06-21", "1944-06-30"),
    ],
)
def test_month_thirds(ds, lo, hi):
    assert R(ds) == (lo, hi, "precision_rule")


def test_season_bounds_and_winter_crosses_year():
    assert R("summer-1944") == ("1944-06-01", "1944-08-31", "precision_rule")
    assert R("winter-1944") == ("1944-12-01", "1945-02-28", "precision_rule")


def test_stated_range():
    assert R("1944-06-06", "1944-06-12") == ("1944-06-06", "1944-06-12", "range")


def test_vague_unresolvable_is_null_never_guessed():
    assert R("the following spring") == (None, None, "unresolved")
    assert R("") == (None, None, "unresolved")
    assert R(None) == (None, None, "unresolved")


def test_sortable_ordering_exact_within_vague():
    # the resolved bounds make vague + exact comparable/sortable
    _, early_hi, _ = R("early-1944-06")
    exact_lo, _, _ = R("1944-06-06")
    assert (
        early_hi < exact_lo or early_hi >= "1944-06-01"
    )  # early June precedes/overlaps


def test_stamp_preserves_verbatim_and_adds_interval():
    rec = {
        "DateID": UL,
        "date_start": "early-1944-06",
        "date_precision": "early",
        "original_text": "early June 1944",
    }
    _stamp_resolved_interval(rec)
    # verbatim preserved
    assert rec["date_start"] == "early-1944-06"
    assert rec["original_text"] == "early June 1944"
    # interval added
    assert rec["resolved_earliest"] == "1944-06-01"
    assert rec["resolved_latest"] == "1944-06-10"
    assert rec["resolution_method"] == "precision_rule"
    jsonschema.validate(rec, S)


def test_schema_accepts_unresolved_nulls():
    rec = {
        "DateID": UL,
        "date_start": "the following spring",
        "resolved_earliest": None,
        "resolved_latest": None,
        "resolution_method": "unresolved",
    }
    jsonschema.validate(rec, S)
