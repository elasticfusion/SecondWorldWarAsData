"""Active content removed from a web page before it is kept (owner 2026-11-23: strip JavaScript).

A kept page (``find_pages``) is untrusted HTML that a reviewer may open and Phase 0 converts. It is
rebuilt from an ALLOWLIST by ``nh3`` (the Rust ``ammonia`` sanitizer on ``html5ever``, a browser-
conformant HTML5 parser): only the text-and-layout tags and attributes in ``TAGS`` / ``ATTRIBUTES``
survive, URLs only with ``http``/``https``/``mailto``, comments are dropped, and script and style
content is removed. Everything else - scripts, embeds, forms, event handlers, ``javascript:``
URLs, SVG/MathML, CSS - simply never makes it into the kept copy. The page's ``<title>`` is kept,
escaped.

Two earlier blocklist versions were bypassed in review (2026-11-23: an ``html.parser`` tree reads
``<!--><script>`` and duplicate attributes differently from a browser; an ``html5lib`` one could be
stalled for minutes by nesting). So the parse runs in a CHILD PROCESS with a hard wall-clock budget
(``BUDGET_S``) and is killed if it overruns, on input of at most ``MAX_PAGE_BYTES``: no page can
hold up a finder or worker. A page that cannot be stripped in time raises ``PageTooComplex``.
"""

from __future__ import annotations

import html as _html
import json
import re
import subprocess  # nosec B404 - the sanitizer runs in a bounded child process (see _clean)
import sys

MAX_PAGE_BYTES = 2 * 1024 * 1024
BUDGET_S = 5.0
# the kept page is parsed again downstream (Phase 0 conversion, html5lib): a page nesting deeper
# than this is refused, so no later parser can be stalled by it either
MAX_DEPTH = 256
_VOID = frozenset(
    "area br col hr img wbr".split()
)  # the allowlisted elements with no end tag
_OUT_TAG = re.compile(r"<(/?)([a-z][a-z0-9]*)")
TAGS = frozenset(
    """a abbr article aside b blockquote br caption cite code col colgroup dd del details dfn div
    dl dt em figcaption figure footer h1 h2 h3 h4 h5 h6 header hr i img ins kbd li main mark nav
    ol p pre q s samp section small span strong sub summary sup table tbody td tfoot th thead time
    tr u ul var wbr""".split()
)
ATTRIBUTES = {
    "*": ["title", "lang", "dir"],
    "a": ["href"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "time": ["datetime"],
}
URL_SCHEMES = ("http", "https", "mailto")
# removed WITH their text (other disallowed tags are unwrapped: their text stays, escaped); the
# title is kept separately, in the head
DROP_WITH_CONTENT = frozenset({"script", "style", "title", "template"})
_HEAD_CHARS = 64 * 1024  # a title is in the page's head
# the child: reads the page and the allowlist as JSON on stdin, writes the cleaned body to stdout
_WORKER = (
    "import json, sys, nh3\n"
    "a = json.load(sys.stdin)\n"
    "sys.stdout.write(nh3.clean(a['html'], tags=set(a['tags']),"
    " attributes={k: set(v) for k, v in a['attributes'].items()},"
    " url_schemes=set(a['url_schemes']), strip_comments=True,"
    " clean_content_tags=set(a['drop_with_content'])))\n"
)


class PageTooComplex(ValueError):
    """A page too large, or too slow to strip within the budget: it is not kept."""


def _clean(html: str) -> str:
    """``nh3.clean`` with the allowlist, in an isolated child process (``-I``) that is killed
    after ``BUDGET_S`` seconds. Argv is fixed; the page travels on stdin, never on a command line.
    """
    payload = json.dumps(
        {
            "html": html,
            "tags": sorted(TAGS),
            "attributes": ATTRIBUTES,
            "url_schemes": list(URL_SCHEMES),
            "drop_with_content": sorted(DROP_WITH_CONTENT),
        }
    )
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv (this interpreter, -I, a constant program)
            [sys.executable, "-I", "-X", "utf8", "-c", _WORKER],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=BUDGET_S,
            check=False,
            env={},  # the child needs nothing from ours (no AWS or Grok keys)
        )
    except subprocess.TimeoutExpired as e:  # the child is killed by subprocess.run
        raise PageTooComplex(f"not stripped within {BUDGET_S:.0f} s") from e
    if proc.returncode != 0:
        raise PageTooComplex(
            f"the sanitizer exited {proc.returncode}: {proc.stderr[-200:]}"
        )
    return proc.stdout


def _depth(body: str) -> int:
    """The deepest nesting of the CLEANED body. nh3's output is well-formed - every element
    closed, text and attribute values escaped - so counting its tags is exact."""
    depth = deepest = 0
    for m in _OUT_TAG.finditer(body):
        name = m.group(2)
        if name in _VOID:
            continue
        depth += -1 if m.group(1) else 1
        deepest = max(deepest, depth)
    return deepest


def _title(html: str) -> str:
    """The first ``<title>``'s text, escaped (a linear search of the head: no regex backtracking
    on a hostile page)."""
    head = html[:_HEAD_CHARS]
    low = head.lower()
    start = low.find("<title")
    opened = low.find(">", start) if start >= 0 else -1
    end = low.find("</title", opened) if opened >= 0 else -1
    if end < 0:
        return ""
    text = " ".join(_html.unescape(head[opened + 1 : end]).split())
    return _html.escape(text[:300], quote=True)


def strip_active_content(html: str) -> str:
    """The page rebuilt from the allowlist (a complete document: its escaped ``<title>`` and the
    cleaned body). Raises ``PageTooComplex`` for a page over ``MAX_PAGE_BYTES`` or one that cannot
    be stripped within ``BUDGET_S``."""
    size = len((html or "").encode("utf-8", errors="replace"))
    if size > MAX_PAGE_BYTES:
        raise PageTooComplex(f"{size} bytes (limit {MAX_PAGE_BYTES})")
    body = _clean(html or "")
    depth = _depth(body)
    if depth > MAX_DEPTH:
        raise PageTooComplex(f"nests {depth} deep (limit {MAX_DEPTH})")
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{_title(html or '')}</title></head><body>{body}</body></html>"
    )
