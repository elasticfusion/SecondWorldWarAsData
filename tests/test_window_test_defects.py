"""The four defects the chapter 8 paragraph-window test exposed (2026-11-13):

1. the batch merge kept ONE mention per sub-event SUMMARY text - every sub-event with the same (or
   no) summary lost its mention after the first;
2. answers with prose before the JSON ("**JSON Response:** ```json {...}```") were "poisoned" and
   failed to parse (185 equipment answers lost);
3. valid short fenced answers (```json {"Equipment": []}```) were evicted and re-asked (299 re-asks);
4. people quotes spliced sentences with "..." (~70% verbatim; the prompt asked for "the sentence(s)").
"""

import json
from unittest import mock

import pytest

import src.grok_client as gc
from src.extraction import batch_parallel as bp
from src.grok_client import json_payload
from src.utils.cache_backend import DiskCacheBackend

# --- 1. a mention per statement of a sub-event, not per summary text ------------------------------


def test_two_sub_events_with_the_same_summary_both_keep_their_mention(tmp_path):
    rec_file = tmp_path / "people" / "omar n bradley.json"
    rec_file.parent.mkdir()
    record = {
        "name": "Omar N. Bradley",
        "PersonID": "01HZZZZZZZZZZZZZZZZZZZZZP1",
        "event_mentions": [],
    }
    rec_file.write_text(json.dumps(record))
    for seid in ("01HZZZZZZZZZZZZZZZZZZZZZS1", "01HZZZZZZZZZZZZZZZZZZZZZS2"):
        bp._add_event_mention_batch(
            record,
            rec_file,
            seid,
            "",  # no summary (paragraph windows) - used to collapse to one mention
            "01HZZZZZZZZZZZZZZZZZZZZZE0",
            "The Sixth of June",
            {"book": "B"},
            source_obj={"original_text": f"Bradley in {seid[-2:]}"},
        )
    mentions = json.loads(rec_file.read_text())["event_mentions"]
    assert sorted(m["Sub_eventID"][-2:] for m in mentions) == ["S1", "S2"]


def test_the_same_statement_of_a_sub_event_is_still_one_mention(tmp_path):
    rec_file = tmp_path / "people" / "x.json"
    rec_file.parent.mkdir()
    record = {
        "name": "X",
        "PersonID": "01HZZZZZZZZZZZZZZZZZZZZZP1",
        "event_mentions": [],
    }
    rec_file.write_text(json.dumps(record))
    for _ in range(2):
        bp._add_event_mention_batch(
            record,
            rec_file,
            "01HZZZZZZZZZZZZZZZZZZZZZS1",
            "s",
            "01HZZZZZZZZZZZZZZZZZZZZZE0",
            "E",
            {"book": "B"},
            source_obj={"original_text": "X"},
        )
    assert len(json.loads(rec_file.read_text())["event_mentions"]) == 1


# --- 2 + 3. the JSON in an answer ----------------------------------------------------------------


@pytest.mark.parametrize(
    "answer, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"Equipment": []}\n```', {"Equipment": []}),
        (
            '**JSON Response:**\n\n```json\n{"people": [{"name": "X"}]}\n```',
            {"people": [{"name": "X"}]},
        ),
        ('**Answer:** {"places": []} - none found.', {"places": []}),
        ('See note [1].\n\n```json\n[{"name": "LCT"}]\n```', [{"name": "LCT"}]),
        ('Per the text [1]: {"dates": [{"d": 1}]}', {"dates": [{"d": 1}]}),
        ('Found these: [{"name": "LCT"}] in the text.', [{"name": "LCT"}]),
        ('**Answer:**\n\n```\n{"weather": []}\n```', {"weather": []}),
    ],
)
def test_the_json_is_found_however_it_is_wrapped(answer, expected):
    assert json.loads(json_payload(answer)) == expected


def test_a_truncated_answer_stays_truncated_not_an_inner_object():
    cut = '{"people": [{"name": "A"}, {"name": "B"}, {"name": "C'
    assert json_payload(cut) == cut  # the truncation handling needs it whole
    assert json_payload("```json\n" + cut) == cut


@pytest.mark.parametrize(
    "answer",
    [
        'Answer: {"people": [{"n": 1}], "x": "cut',  # inner list of an outer value cut short
        'See [1]. {"people": [{"n": 1}, {"n":',  # a citation before the cut-short answer
    ],
)
def test_a_truncated_answer_behind_prose_stays_truncated(answer):
    got = json_payload(answer)
    assert got == answer[answer.find("{") :]
    with pytest.raises(json.JSONDecodeError):
        json.loads(got)  # reaches the truncation path, not read as a complete answer


@pytest.fixture
def grok(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    token = gc.current_book.set(None)
    yield gc.GrokClient(DiskCacheBackend(tmp_path / "c"))
    gc.current_book.reset(token)


def _reply(content):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"Equipment": []}\n```',  # short + fenced: was evicted and re-asked
        '**JSON Response:**\n\n```json\n{"Equipment": [{"name": "LCT"}]}\n```',  # was "poisoned"
    ],
)
def test_a_wrapped_answer_is_parsed_once_not_re_asked(grok, content):
    with mock.patch.object(grok, "_call_api", return_value=_reply(content)) as api:
        got = grok.extract_json("P", cache_type="equipment_broad")
        again = grok.extract_json("P", cache_type="equipment_broad")  # from the cache
    assert got == again == json.loads(json_payload(content))
    assert api.call_count == 1


def test_an_answer_with_no_json_is_still_re_asked_once(grok):
    replies = [_reply("I cannot find any equipment."), _reply('{"Equipment": []}')]
    with mock.patch.object(grok, "_call_api", side_effect=replies) as api:
        assert grok.extract_json("P", cache_type="equipment_broad") == {"Equipment": []}
    assert api.call_count == 2


# --- 4. one exact quote --------------------------------------------------------------------------


def test_the_people_prompt_asks_for_one_exact_quote(monkeypatch):
    # extract_people_batch_async points the module's exclusions file at its output dir: keep it
    # from leaking into later tests
    monkeypatch.setattr(bp, "_people_exclusions_file", bp._people_exclusions_file)
    monkeypatch.setattr(bp, "_people_exclusion_patterns", bp._people_exclusion_patterns)
    seen = []

    class Client:
        def extract_json(self, prompt, system_prompt=None, **k):
            seen.append(prompt)
            raise gc.BatchModeCollecting("q")

    import asyncio
    import tempfile
    from pathlib import Path

    ev = {
        "Event": {
            "EventID": "E1",
            "Sub-events": [
                {
                    "Sub-eventID": "S1",
                    "Sub-event_fulltext": {"Paragraph_001": "Gerow landed."},
                }
            ],
        }
    }
    asyncio.run(
        bp.extract_people_batch_async(ev, Client(), Path(tempfile.mkdtemp()), {})
    )
    (prompt,) = seen
    assert bp.QUOTE_RULE in prompt
    assert "sentence(s)" not in prompt and 'ordered..."' not in prompt


# --- zero-padded numbers echoed from "Paragraph_052" (chapter 8 rerun, 2026-11-14) -------------

# the real equipment answer (batch_49651e55): asked and evicted as "truncated" every round
PADDED = (
    '{\n  "BookID": "57SJMAQBZYEWDEHACZVT8EZ7AP",\n  "RequestID": "1250008cf72c5c52",\n'
    '  "Equipment": [\n'
    '    {"name": "machine gun", "original_text": "American machine gun and mortar fire", '
    '"paragraph_numbers": [052]},\n'
    '    {"name": "mortar", "original_text": "American machine gun and mortar fire", '
    '"paragraph_numbers": [052, 007]}\n  ]\n}'
)


def test_zero_padded_numbers_parse():
    data = json.loads(json_payload(PADDED))
    assert [e["paragraph_numbers"] for e in data["Equipment"]] == [[52], [52, 7]]


def test_padding_inside_strings_and_plain_zeros_are_kept():
    text = '{"original_text": "at 0600, [052] hours", "n": 0, "x": [0, 0.5, -07], "t": "0700"}'
    data = json.loads(json_payload(text))
    assert data == {
        "original_text": "at 0600, [052] hours",
        "n": 0,
        "x": [0, 0.5, -7],
        "t": "0700",
    }


def test_a_zero_padded_answer_is_not_a_truncation():
    client = gc.GrokClient.__new__(gc.GrokClient)
    for attr in ("_json_markdown_stripped", "_json_clean"):
        setattr(client, attr, 0)
    with (
        mock.patch.object(client, "chat_completion", return_value=PADDED),
        mock.patch.object(
            client,
            "_handle_truncation_error",
            side_effect=AssertionError("read as truncated"),
        ),
    ):
        result = client._extract_json("p", use_cache=False)
    assert result["Equipment"][0]["paragraph_numbers"] == [52]


def _decode_error(text):
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        return e
    raise AssertionError("parsed")


def test_only_an_answer_that_runs_out_is_a_truncation():
    cut = '{"Equipment": [{"name": "M4", "original_text": "the tanks'
    ended = '{"Equipment": [{"name": "M4"}, '
    malformed = '{"Equipment": [1 2], "more": "text after the error"}'
    assert gc._cut_short(cut, _decode_error(cut))  # unterminated string
    assert gc._cut_short(ended, _decode_error(ended))  # ran out
    assert not gc._cut_short(malformed, _decode_error(malformed))  # mid-document


def test_a_malformed_answer_is_not_evicted_as_a_truncation():
    client = gc.GrokClient.__new__(gc.GrokClient)
    for attr in (
        "_json_markdown_stripped",
        "_json_clean",
        "_json_repaired",
        "_json_failed",
    ):
        setattr(client, attr, 0)
    malformed = '{"Equipment": [1 2], "more": "' + "x" * 600 + '"}'
    with (
        mock.patch.object(client, "chat_completion", return_value=malformed),
        mock.patch.object(
            client,
            "_handle_truncation_error",
            side_effect=AssertionError("read as truncated"),
        ),
        mock.patch.object(client, "_try_repair_json", return_value={"repaired": True}),
        mock.patch.object(client, "_get_model", return_value="m"),
    ):
        assert client._extract_json("p", use_cache=False) == {"repaired": True}
