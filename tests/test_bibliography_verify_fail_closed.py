"""`_verify_match` fails closed like every other bibliography verifier (TODO gap 20).

An accepted unverified match stamps a possibly wrong URL as the citation's copy; a rejection is only
a miss, which the resolver searches again on the next run.
"""

import logging

from src.enrichment import bibliography_resolver as br


class _Answers:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def chat_completion(self, **_):
        if self.error:
            raise self.error
        return self.reply


def test_no_grok_client_rejects_and_says_why(caplog):
    with caplog.at_level(logging.WARNING):
        assert br._verify_match("A title", "a citation", None) is False
    assert "bib.verify_unavailable reason=no_grok_client" in caplog.text


def test_a_grok_error_on_the_title_check_rejects(caplog):
    with caplog.at_level(logging.WARNING):
        ok = br._verify_match(
            "A title", "a citation", _Answers(error=RuntimeError("x"))
        )
    assert ok is False
    assert "bib.verify_title_error err=RuntimeError" in caplog.text


def test_a_yes_still_accepts_and_a_no_rejects():
    assert br._verify_match("A title", "a citation", _Answers(reply="YES")) is True
    assert br._verify_match("A title", "a citation", _Answers(reply="NO")) is False
