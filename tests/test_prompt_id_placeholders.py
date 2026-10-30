"""Provenance (#1 goal) regression: entity prompt EXAMPLES must use braced {event_id}/{sub_event_id}
placeholders (which render to the real ULIDs), NOT bare literal 'event_id'/'sub_event_id' tokens.
A literal example made the model echo the placeholder text -> invalid ULID -> null Sub_eventID ->
broken provenance chain (XChannel-8 2026-10-30)."""

import glob
import re

from src.utils.prompt_loader import render_prompt

_EID = "0YQFZD6P4XYJ5BTC72QFMAQXYB"
_SID = "7JAY0GR7CD260B3PKCY60934E9"


def _render(name):
    return render_prompt(
        name, book="B", author="A", series="S", event_name="E", event_id=_EID,
        sub_event_summary="SS", sub_event_id=_SID, text="T",
    )


def test_people_example_uses_real_ids_not_literal():
    out = _render("people")
    assert _EID in out and _SID in out  # real IDs rendered into the example
    assert '"event_id"' not in out and '"sub_event_id"' not in out  # no literal placeholder


def test_places_example_uses_real_ids_not_literal():
    out = _render("places")
    assert _EID in out and _SID in out
    assert '"event_id"' not in out and '"sub_event_id"' not in out


def test_no_prompt_has_bare_literal_id_example():
    """Guard ALL prompts: an id field's example value must not be a bare lowercase placeholder token
    (event_id/sub_event_id/...) — it must be a braced {placeholder} or a realistic ULID."""
    offenders = []
    pat = re.compile(r'"(?:EventID|Sub_eventID|Sub-eventID)":\s*"(event_id|sub_event_id)"')
    for f in glob.glob("prompts/*.yaml"):
        txt = open(f, encoding="utf-8").read()
        if pat.search(txt):
            offenders.append(f)
    assert not offenders, f"prompts with bare-literal id-placeholder examples: {offenders}"
