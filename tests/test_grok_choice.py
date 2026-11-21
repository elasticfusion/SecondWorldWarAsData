"""Task 1.2f: a one-word Grok verdict that is none of the choices is never read as one of them,
and never left in the cache as THE answer: it is evicted and asked once more, then the caller's
safe default applies."""

from unittest import mock

import pytest

from src.utils import grok_choice as gc


@pytest.mark.parametrize(
    "reply,word",
    [("YES", "YES"), ("  no.", "NO"), ('"SITE"', "SITE"), ("Terrain view", "TERRAIN"),
     ("", ""), (None, ""), ("42", "")],
)  # fmt: skip
def test_the_first_word_is_read(reply, word):
    assert gc.first_word(reply) == word


def test_a_valid_choice_is_returned_without_eviction():
    evict = mock.Mock()
    assert gc.ask_choice(lambda: "YES", ("YES", "NO"), evict) == "YES"
    evict.assert_not_called()


def test_an_unusable_reply_is_evicted_and_asked_again():
    replies = iter(["NOT SURE", "NO"])
    evict = mock.Mock()
    # "NOT SURE" used to read as NO via startswith("NO")
    assert gc.ask_choice(lambda: next(replies), ("YES", "NO"), evict) == "NO"
    evict.assert_called_once()


def test_unusable_twice_is_none_and_both_are_evicted(caplog):
    evict = mock.Mock()
    assert gc.ask_choice(lambda: "I cannot say", ("YES", "NO"), evict, what="t") is None
    assert (
        evict.call_count == 2 and "OBSERV grok.unusable_verdict what=t" in caplog.text
    )


def test_without_an_evictor_it_does_not_reask():
    ask = mock.Mock(return_value="maybe")
    assert gc.ask_choice(ask, ("YES", "NO"), None) is None and ask.call_count == 1


def test_the_evictor_targets_the_exact_request():
    client = mock.Mock()
    gc.evictor(client, "p", "s", "exclude_scope", 0.0)()
    client.evict_completion.assert_called_once_with("p", "s", "exclude_scope", 0.0)
    assert gc.evictor(object(), "p") is None  # a test double without it


def test_title_membership_reads_not_sure_as_unknown():
    from src.extraction import title_memberships as tm

    class Fake:
        def chat_completion(self, p):
            return "NOT SURE"

    assert tm._ask_membership(Fake(), "A", "B", "1944") is None


def test_an_unusable_terrain_verdict_is_not_linked():
    from src.extraction import image_place_link as ipl

    class Fake:
        def chat_completion(self, p):
            return "Sorry, I can't view images"

    assert ipl._grok_is_terrain("caption", Fake()) == (False, "grok-unparsed")


def test_an_unusable_scope_verdict_stays_narrow():
    from src.enrichment import fetch_review as fr

    client = mock.Mock()
    client.chat_completion.return_value = "It depends"
    assert (
        fr.decide_exclude_scope({"endpoint": "https://x.org/a"}, client) == fr.SCOPE_URL
    )
    assert client.evict_completion.call_count == 2


def test_a_bibliography_verify_that_is_neither_yes_nor_no_is_evicted_and_rejected():
    from src.enrichment import bibliography_resolver as br

    client = mock.Mock()
    client.chat_completion.side_effect = ["Possibly, the title is close", "YES"]
    assert br._ask_yes(client, "p", "s") is True  # asked again after the eviction
    client.evict_completion.assert_called_once_with(
        "p", "s", "bibliography_verify", 0.0
    )
    client.chat_completion.side_effect = ["unclear", "unclear"]
    assert br._ask_yes(client, "p", "s") is False


def test_in_batch_mode_an_unusable_answer_is_evicted_not_reasked():
    """A re-ask would only queue the request again mid-cycle: evict (the next run asks) and use
    the safe default now."""
    ask, evict = mock.Mock(return_value="hmm"), mock.Mock()
    assert gc.ask_choice(ask, ("YES", "NO"), evict, reask=False) is None
    assert ask.call_count == 1 and evict.call_count == 1
    assert gc.can_reask(mock.Mock(batch_mode=True)) is False
    assert gc.can_reask(mock.Mock()) is True  # only a real True means batch mode


def test_a_batch_collect_reaches_the_collector():
    from src.grok_client import BatchModeCollecting

    def collecting():
        raise BatchModeCollecting("k")

    with pytest.raises(BatchModeCollecting):
        gc.ask_choice(collecting, ("YES", "NO"), mock.Mock())
