"""§1.0c NIT: prompt_loader/search_query single-pass substitution — an already-substituted value
containing a literal {otherkey} must NOT be re-substituted."""

from unittest import mock


def test_render_prompt_single_pass(monkeypatch):
    import src.utils.prompt_loader as PL

    monkeypatch.setattr(
        PL, "load_prompt", lambda name: {"prompt_template": "A={a} B={b}"}
    )
    # a's value contains a literal {b}; single-pass must NOT expand it with b's value
    out = PL.render_prompt("x", a="has-{b}-token", b="BVAL")
    assert out == "A=has-{b}-token B=BVAL"


def test_render_prompt_unknown_placeholder_intact(monkeypatch):
    import src.utils.prompt_loader as PL

    monkeypatch.setattr(
        PL, "load_prompt", lambda name: {"prompt_template": '{"json": {nested}} {a}'}
    )
    out = PL.render_prompt("x", a="X")
    assert "{nested}" in out and out.endswith("X")  # unknown braces preserved


def test_render_search_queries_single_pass(monkeypatch):
    import src.utils.search_query_loader as SQ

    monkeypatch.setattr(SQ, "load_search_queries", lambda qf: {"cat": ["{a} then {b}"]})
    out = SQ.render_search_queries("x", "cat", a="val-{b}", b="BVAL")
    assert out == ["val-{b} then BVAL"]  # a's literal {b} not re-substituted
