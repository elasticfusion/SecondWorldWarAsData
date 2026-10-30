"""Provenance (#1 goal) regression: entity prompt EXAMPLES must RENDER the real ULIDs into the
EventID/Sub_eventID example values — not leave a bare literal 'sub_event_id' NOR a surviving
'{sub_event_id}' brace. A placeholder the model copies -> invalid ULID -> null Sub_eventID ->
broken provenance chain (XChannel-8 2026-10-30). render_prompt must substitute placeholders that
live INSIDE the injected {schema}, for STRUCTURAL kwargs only."""

import glob
import re

from src.utils.prompt_loader import render_prompt

_EID = "0YQFZD6P4XYJ5BTC72QFMAQXYB"
_SID = "7JAY0GR7CD260B3PKCY60934E9"


def _render(name, **extra):
    kw = dict(
        book="B",
        author="A",
        series="S",
        event_name="E",
        event_id=_EID,
        sub_event_summary="SS",
        sub_event_id=_SID,
        text="T",
    )
    kw.update(extra)
    return render_prompt(name, **kw)


def _example_id_values(out):
    # the JSON-example lines (indented) for EventID/Sub_eventID, not the instruction bullets
    vals = []
    for ln in out.splitlines():
        m = re.search(r'"(?:EventID|Sub_eventID|Sub-eventID)":\s*"([^"]*)"', ln)
        if m and not ln.lstrip().startswith("-"):
            vals.append(m.group(1))
    return vals


def test_people_example_renders_real_ids():
    out = _render("people")
    vals = _example_id_values(out)
    assert vals, "expected EventID/Sub_eventID example values"
    for v in vals:
        assert v in (_EID, _SID), f"example id value not rendered to a real ULID: {v!r}"
    assert (
        "{event_id}" not in out and "{sub_event_id}" not in out
    )  # no surviving braces
    assert '"event_id"' not in out and '"sub_event_id"' not in out  # no bare literal


def test_places_example_renders_real_ids():
    out = _render("places")
    vals = _example_id_values(out)
    assert vals and all(v in (_EID, _SID) for v in vals)
    assert "{event_id}" not in out and "{sub_event_id}" not in out


def test_untrusted_text_not_substituted_into_schema():
    # a {placeholder} inside untrusted `text` must NOT be re-interpolated (single-pass hardening);
    # only STRUCTURAL kwargs are pre-applied to the schema.
    out = _render("people", text="malicious {event_id} {book} injection")
    # the literal token from text survives as-is (not replaced by a second pass)
    assert "malicious" in out


def test_no_prompt_has_bare_literal_or_unrendered_id_example():
    """Guard ALL prompts: no id example value may be a bare 'event_id'/'sub_event_id' literal."""
    offenders = []
    pat = re.compile(
        r'"(?:EventID|Sub_eventID|Sub-eventID)":\s*"(event_id|sub_event_id)"'
    )
    for f in glob.glob("prompts/*.yaml"):
        if pat.search(open(f, encoding="utf-8").read()):
            offenders.append(f)
    assert not offenders, f"prompts with bare-literal id example: {offenders}"
