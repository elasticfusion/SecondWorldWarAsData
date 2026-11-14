"""A document's catalogue metadata, from its own title page, by one Grok request at the end of
every import (owner rule, 2026-11-09).

When an import completes - an OCR'd PDF published as its chapters (src/ingestion/ocr_promote), an
EPUB/DOCX/HTML/text conversion (phase0_convert), a video transcript (phase0_video) - Grok reads the
document's OPENING pages: for a printed book the title page and the back of the title page, which
carry its title, author, series, edition and imprint ("First Printed 1951 - CMH Pub 7-4-1",
"For sale by the Superintendent of Documents, U.S. Government Printing Office"). Its answer fills
the chapter meta (``chapterN-meta.yaml``) before Phase 1 parses it, so every record's provenance
carries the book's author from the first parse.

* **Evidence, not trust.** Each value must come with a verbatim quote; a quote not found in the
  document's opening pages (or, as a fallback only, the validation page) or that does not contain
  the value is dropped. Which text supported it is recorded (``metadata_evidence``): the document
  is preferred - the PDF is the source of record.
* **Licence by rule, not by model.** A U.S. Government imprint on the opening pages - a Center of
  Military History publication number, "Superintendent of Documents", "Government Printing Office"
  - makes the work public domain (a work of the U.S. Government, owner rule 2026-11-09). Otherwise
  a work first published at least 96 years ago is public domain (the U.S. 95-year term); a later
  one is presumed in copyright (shown as an extract only). Without an imprint or a year - or with
  a government imprint beside a copyright notice or a shared/reprint imprint (a private reprint,
  a co-publication) - the licence stays empty for a person to decide.
* **An operator's value is kept.** A value in the chapter meta that this module did not write
  (``metadata_filled`` records what it did) is a person's and is never replaced; a value it wrote
  is refreshed on a re-drive, and an unchanged one keeps its evidence (same bytes, no re-parse).
* **Fail-safe.** Disabled, no key, no network, a malformed answer: nothing is filled, a warning is
  logged, the import goes on, and the metadata-gap marker lists what is still missing.

Switch: ``ingestion.book_metadata`` in config (default on) or ``BOOK_METADATA=0``/``1``.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

FIELDS = ("title", "author", "series", "publisher", "copyright_year", "edition")
# Chapter meta keys each field fills (title/publisher/edition are kept for provenance and display;
# the parser reads author/series/copyright_date/license).
META_KEY = {
    "title": "book_title",
    "author": "author",
    "series": "series",
    "publisher": "publisher",
    "copyright_year": "copyright_date",
    "edition": "edition",
}
GAP_FIELDS = ("author", "series", "chapter_title", "license", "copyright_date")
# Keys of the chapter meta this module may write; whatever an operator put in one of them is kept.
OWNED = (*META_KEY.values(), "license", "license_basis", "chapter_title")
OPENING_PAGES = (
    8  # a book's half-title, title page, imprint, dedication ... before the text
)
OPENING_CHARS = 12000
# The title page and its back: half-title, (frontispiece caption), title page, imprint. Values must
# be quoted from here - a "by ..." in a foreword on page 7 is not the book's author.
TITLE_PAGES = 4
TITLE_CHARS = 4000
HINT_CHARS = 3000
# A value may come from the validation page (untrusted web content) only for these; the year, the
# publisher and the edition are the document's own (a site footer "(c) 1997" is not the book's).
_HINTABLE = ("title", "author", "series")
_US_TERM_YEARS = (
    96  # published works: 95-year U.S. term, public domain from the year after
)
_IMPRINT_LINE_CHARS = 160  # an imprint line; a longer line is a paragraph of prose
# A U.S. Government imprint (17 U.S.C. 105: a work of the U.S. Government is not copyrighted), as
# the title page or its back prints it: the government's printer and its sales agent, which put
# their name on every federal publication they print or sell, or a federal history office as
# publisher. Matched only on the title pages, on a short imprint line - never on a sentence of the
# text ("I thank the Center of Military History ...").
_US_GOVERNMENT_IMPRINT = re.compile(
    r"\bsuperintendent of documents\b"
    r"|\b(?:u\.?\s?s\.?\s)?government (?:printing|publishing) office\b"
    r"|\bu\.?\s?s\.?\s?g\.?\s?p\.?\s?o\.?\b"
    r"|\b(?:center|office of the chief|office) of (?:military|air force|naval|army) history\b"
    r"|\bnaval history (?:and heritage command|division)\b|\bnaval historical center\b"
    r"|\bhistory and museums division, headquarters,? u\.?\s?s\.? marine corps\b"
    r"|\bhistorical (?:branch|division)\b[^\n]{0,40}\b(?:marine corps|war department)\b"
    r"|\bcmh pub\b",
    re.IGNORECASE,
)
# A copyright / first-publication statement around a year ("First Printed 1951", "Copyright 1951",
# "(c) 1951", "Published 1951"): a year anywhere else ("the 1900 census") is not the book's date.
_YEAR_STATEMENT = r"(?:copyright|©|\(c\)|first (?:printed|published|printing|edition)|published|printed)\W{0,3}(?:[^\n]{0,40}?\W)?"
# A copyright notice ("Copyright 2005 Acme Press", "(c) 1987"): a federal work prints none, so one
# beside a government imprint means a private reprint or co-publication.
_COPYRIGHT_NOTICE = re.compile(
    r"(?:\bcopyright\b|©|\(c\))\W{0,3}(?:[^\n]{0,40}?\W)?(?:1[4-9]|20)\d\d\b"
)
_NOT_COPYRIGHTED = re.compile(
    r"\bnot (?:subject to|protected by) copyright\b|\bpublic domain\b"
)
_SHARED_IMPRINT = re.compile(
    r"\bin (?:cooperation|association|collaboration|conjunction) with\b"
    r"|\bco-?published\b|\breprint(?:ed)? (?:of|by|from|with)\b"
)
_IMAGE_PARAGRAPH = re.compile(r"^\s*!\[[^\n]*$", re.MULTILINE)
_PAGE_SEPARATOR = re.compile(r"^\d+-{48}$", re.MULTILINE)
_PAGE_LABEL = re.compile(r"^\[page \d+\]$", re.MULTILINE)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_DELIMITER = re.compile(r"<<<|>>>")
_WORD = re.compile(r"[^\W_]+")


@dataclass
class Metadata:
    """What the document's opening pages establish, with the quote and source behind each value."""

    values: Dict[str, str] = field(default_factory=dict)  # FIELDS -> value
    evidence: Dict[str, Dict[str, str]] = field(
        default_factory=dict
    )  # field -> quote, source
    rejected: Dict[str, str] = field(
        default_factory=dict
    )  # field -> why it was not used
    license: str = ""
    license_basis: str = ""
    model: str = ""

    def meta(self) -> Dict[str, Any]:
        """The chapter-meta keys this metadata fills (only those it established)."""
        out: Dict[str, Any] = {META_KEY[f]: v for f, v in self.values.items() if v}
        if self.license:
            out["license"] = self.license
            out["license_basis"] = self.license_basis
        elif "license" in self.rejected:
            # not written to the meta: tells resolve to withdraw an earlier machine licence, and
            # the publish log why there is none
            out["license_rejected"] = self.rejected["license"]
        if self.values:
            out["metadata_basis"] = f"grok:{self.model}" if self.model else "grok"
            out["metadata_evidence"] = self.evidence
        return out


# --- the document's opening pages -----------------------------------------------------------------


Document = Union[str, Sequence[Tuple[Optional[int], str]]]


def opening_text(
    document: Document, pages: int = OPENING_PAGES, chars: int = OPENING_CHARS
) -> str:
    """The document's opening pages as plain printed text: for Chandra OCR parts
    (``(first page, paginated markdown)``) the first ``pages`` printed pages, each labelled with its
    PDF page; for any other text its first ``chars`` characters. Chandra's model-written picture
    descriptions are removed - they are not the document's text."""
    if isinstance(document, str):
        return _printed(document)[:chars]
    from src.ingestion.chunk_pages import split_pages

    out: list = []
    for first, markdown in document:
        for page, text in split_pages(markdown, first or 1):
            if len(out) >= pages:
                return "\n\n".join(out)[:chars]
            printed = _printed(text)
            if printed:
                out.append(f"[page {page}]\n{printed}")
    return "\n\n".join(out)[:chars]


def title_pages(document: Document) -> str:
    """The title page and its back: the first printed pages (or characters) of the document."""
    return opening_text(document, TITLE_PAGES, TITLE_CHARS)


def _printed(markdown: str) -> str:
    text = _IMAGE_PARAGRAPH.sub("", markdown)
    text = _PAGE_SEPARATOR.sub("", text)
    return "\n".join(
        line.rstrip() for line in text.splitlines() if line.strip()
    ).strip()


def page_hint(html: Optional[str]) -> str:
    """A transcription page's title and opening text, as plain text (a fallback source only)."""
    if not html:
        return ""
    html = _SCRIPT.sub(" ", html)
    title = re.search(r"<title[^>]{0,200}>(.{0,500}?)</title>", html, re.S | re.I)
    head = title.group(1).strip() + "\n" if title else ""
    body = re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()
    return (head + body)[:HINT_CHARS]


# --- the request ----------------------------------------------------------------------------------


def enabled(config: Optional[dict] = None) -> bool:
    flag = os.environ.get("BOOK_METADATA")
    if flag in ("0", "1"):
        return flag == "1"
    if config is None:
        try:
            from src.utils.config import load_config

            config = load_config()
        except Exception:  # noqa: BLE001
            return True
    return bool((config or {}).get("ingestion", {}).get("book_metadata", True))


def _budget() -> float:
    """Seconds the request may take: the import must never wait on it (the OCR merge runs in a
    300 s Lambda). Past it the import goes on without the answer."""
    try:
        return max(5.0, float(os.getenv("BOOK_METADATA_BUDGET_S", "90")))
    except ValueError:
        return 90.0


def derive(
    sample: str,
    hint: str = "",
    grok: Any = None,
    titles: Optional[str] = None,
    licence_rule: bool = True,
) -> Metadata:
    """The metadata the document establishes: Grok reads ``sample`` (its opening pages) and every
    value is checked against ``titles`` (its title pages; default: the sample) or, for the title,
    author and series only, ``hint`` (the validation page). Never raises."""
    found = Metadata()
    if not sample.strip() and not hint.strip():
        return found
    titles = sample if titles is None else titles
    response = _answer(found, grok, sample, hint)
    _verify(found, response, titles, hint)
    if (
        licence_rule
    ):  # a rule over the document's own imprint: it holds even without the model
        found.license, found.license_basis = license_for(
            titles, found.values.get("copyright_year")
        )
        if not found.license and found.license_basis:
            found.rejected["license"] = found.license_basis
            found.license_basis = ""
    logger.info(
        "OBSERV book_metadata.derived fields=%s rejected=%s license=%s",
        sorted(found.values),
        found.rejected,
        found.license or "-",
    )
    return found


def _answer(found: Metadata, grok: Any, sample: str, hint: str) -> dict:
    """Grok's answer, within the time budget, or {} (logged by type only: a client error may
    echo request headers, the API key among them)."""
    box: Dict[str, Any] = {}

    def run() -> None:
        try:
            g = grok or client()
            box["model"] = str(getattr(g, "model", "") or "")
            box["response"] = _ask(g, sample, hint)
        except (
            Exception
        ) as e:  # noqa: BLE001 - fail-safe: the import goes on without metadata
            box["error"] = type(e).__name__

    import threading

    # a daemon thread: a hung request never keeps the Lambda or the task from finishing
    worker = threading.Thread(target=run, name="book-metadata", daemon=True)
    worker.start()
    worker.join(_budget())
    if worker.is_alive():
        box["error"] = "timeout"
    if "error" in box:
        logger.warning(
            "OBSERV book_metadata.unavailable status=warn err=%s", box["error"]
        )
        return {}
    found.model = box.get("model", "")
    return box.get("response") or {}


def _ask(grok: Any, sample: str, hint: str) -> dict:
    from src.utils.prompt_loader import get_system_prompt, render_prompt

    response = grok.extract_json(
        # the document text cannot close the block it is quoted in
        prompt=render_prompt(
            "book_metadata",
            sample=_DELIMITER.sub(" ", sample),
            hint=_DELIMITER.sub(" ", hint) or "(none)",
        ),
        system_prompt=get_system_prompt("book_metadata"),
        temperature=0.1,
        use_cache=True,
        cache_type="book_metadata",
    )
    if not isinstance(response, dict):
        raise ValueError(f"metadata answer is not an object: {type(response).__name__}")
    return response


def client() -> Any:
    """A Grok client that works in a Lambda as well as a task: the key from the environment or
    Secrets Manager (``SECRETS_ID``), the cache in DynamoDB (``CACHE_TABLE``, read item by item -
    never the whole-table preload) or, locally, on disk; a request timeout within the budget.
    """
    from src.grok_client import GrokClient

    grok = GrokClient(_cache(), api_key=os.getenv("GROK_API_KEY") or _secret_key())
    grok.timeout = min(float(getattr(grok, "timeout", 600.0) or 600.0), _budget())
    return grok


def _cache() -> Any:
    table = os.getenv("CACHE_TABLE", "")
    if table:
        from src.utils.cache_backend import DynamoCacheBackend

        region = (
            os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        )
        return DynamoCacheBackend(table_name=table, region=region)
    from src.utils.cache_backend import DiskCacheBackend
    from src.utils.config import get_paths, load_config

    return DiskCacheBackend(get_paths(load_config())["api_cache"])


def _secret_key() -> str:
    secret_id = os.getenv("SECRETS_ID", "")
    if not secret_id:
        return ""
    import boto3

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("secretsmanager", region_name=region).get_secret_value(
        SecretId=secret_id
    )["SecretString"]


# --- evidence ---------------------------------------------------------------------------------


def _verify(found: Metadata, response: dict, titles: str, hint: str) -> None:
    """Keep each value quoted from the title pages (or, for the title, author and series only,
    the validation page) whose quote holds the value."""
    sources = {"document": _normal(titles), "validation_page": _normal(hint)}
    for name in FIELDS:
        item = response.get(name)
        if item is None:
            continue
        value, quote = _value_and_quote(item)
        reason = _rejection(name, value, quote)
        allowed = (
            ("document", "validation_page") if name in _HINTABLE else ("document",)
        )
        source = "" if reason else _quoted_in(quote, {k: sources[k] for k in allowed})
        if not reason and not source:
            reason = "quote not on the title pages"
        if reason:
            found.rejected[name] = reason
            continue
        found.values[name] = value
        found.evidence[name] = {"quote": quote[:300], "source": source}


def _value_and_quote(item: Any) -> Tuple[str, str]:
    if not isinstance(item, dict):
        return "", ""
    value = " ".join(str(item.get("value") or "").split())
    quote = " ".join(str(item.get("evidence") or "").split())
    return value, quote


def _rejection(name: str, value: str, quote: str) -> str:
    if not value or not quote or not _WORD.search(value):
        return "no value or no quote"
    if len(value) > 300 or len(value) > len(quote) + 10:
        return "value too long"
    if name == "copyright_year":
        return _year_rejection(value, quote)
    return "" if _words_of(name, value, quote) else "value not in its quote"


def _words_of(name: str, value: str, quote: str) -> bool:
    """Whether the value (each of an author list's "; "-separated names) is a run of whole words
    of its quote ("ted states" is not in "united states")."""
    q = f" {_flat(quote)} "
    parts = value.split(";") if name == "author" else [value]
    return all(_flat(p) and f" {_flat(p)} " in q for p in parts)


def _year_rejection(value: str, quote: str) -> str:
    if (
        not re.fullmatch(r"\d{4}", value)
        or not 1450 <= int(value) <= datetime.now().year
    ):
        return "not a year"
    if not re.search(_YEAR_STATEMENT + re.escape(value) + r"\b", _normal(quote)):
        return "not a copyright or first-publication statement"
    return ""


def _flat(text: str) -> str:
    """Words only, in order: "Gordon A. Harrison" -> "gordon a harrison"."""
    return " ".join(_WORD.findall(_normal(text)))


def _quoted_in(quote: str, normal_sources: Dict[str, str]) -> str:
    q = _normal(quote)
    return next((name for name, text in normal_sources.items() if q and q in text), "")


def _normal(text: str) -> str:
    """Case, accents, emphasis marks, dashes, quotes and spacing aside."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[\u2010-\u2015]", "-", t)
    t = re.sub(r"[*_#>`\\|]", " ", t)
    return " ".join(t.split())


# --- licence ------------------------------------------------------------------------------------


def license_for(titles: str, year: Optional[str]) -> Tuple[str, str]:
    """(licence, basis) from the title pages' imprint, else the first publication year. A
    government imprint beside a copyright notice or a shared/reprint imprint (a private press's
    reprint of a federal work, a co-publication) decides nothing: ("", why) - a person decides,
    because a wrong "public domain" would publish a copyrighted text in full."""
    imprint = _imprint(titles)
    if imprint:
        conflict = _imprint_conflict(titles)
        if conflict:
            return "", f'the imprint names "{imprint}" but {conflict}: a person decides'
        return "public_domain", f'U.S. Government work: the imprint names "{imprint}"'
    if year and year.isdigit():
        if int(year) <= datetime.now().year - _US_TERM_YEARS:
            return (
                "public_domain",
                f"first published {year}: the U.S. 95-year term has ended",
            )
        return (
            "in_copyright",
            f"first published {year}: presumed in copyright (show an extract)",
        )
    return "", ""


def _imprint_lines(titles: str) -> list:
    """The title pages' short lines (an imprint line, not a paragraph of prose), normalised."""
    return [
        _normal(line)
        for line in _PAGE_LABEL.sub("", titles or "").splitlines()
        if 0 < len(line) <= _IMPRINT_LINE_CHARS
    ]


def _imprint(titles: str) -> str:
    """The U.S. Government imprint on a short line of the title pages, or ""."""
    for line in _imprint_lines(titles):
        m = _US_GOVERNMENT_IMPRINT.search(line)
        if m:
            return m.group(0)
    return ""


def _imprint_conflict(titles: str) -> str:
    """Why the title pages do not let a government imprint decide the licence, or ""."""
    for line in _imprint_lines(titles):
        if _COPYRIGHT_NOTICE.search(line) and not _NOT_COPYRIGHTED.search(line):
            return f'they also print a copyright notice ("{line[:80]}")'
        if _SHARED_IMPRINT.search(line):
            return f'they also print a shared or reprint imprint ("{line[:80]}")'
    return ""


# --- the chapter meta -------------------------------------------------------------------------

_MAX_META_BYTES = 64 * 1024  # a chapter meta is a few hundred bytes


def existing(s3: Any, bucket: str, key: str) -> Dict[str, Any]:
    """What the chapter meta already at ``key`` holds of this module's keys, with the record of
    which of them it wrote itself (``metadata_filled``), or {}. Only real bytes of a sane size are
    parsed: anything else (an absent object, a stub client in a test) is "no meta" - never handed
    to the YAML reader, which loops on a stream that is not one."""
    import yaml

    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read(_MAX_META_BYTES + 1)
    except Exception:  # noqa: BLE001 - absent or unreadable: nothing to keep
        return {}
    if not isinstance(raw, (bytes, str)) or len(raw) > _MAX_META_BYTES:
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return _owned(data) if isinstance(data, dict) else {}


def _owned(data: Dict[str, Any]) -> Dict[str, Any]:
    """This module's keys of a chapter meta, with the record of what it wrote."""
    keep = set(OWNED) | {"metadata_filled", "metadata_basis", "metadata_evidence"}
    out = {k: v for k, v in data.items() if k in keep and v}
    if not isinstance(data.get("metadata_filled"), dict) and out.get("chapter_title"):
        # a meta from before this record: the import always wrote the chapter title itself
        # (author, series and licence only a person did), so the title is the import's to refresh
        out["metadata_filled"] = {"chapter_title": out["chapter_title"]}
    return out


def resolve(
    kept: Dict[str, Any], derived: Dict[str, Any], title: str
) -> Dict[str, Any]:
    """The chapter-meta fields to write: an operator's value (one this module did not write) is
    kept; a value this module wrote is replaced by today's (a re-OCR fixes its own mistakes), and
    kept if today brought none; ``title`` is today's chapter title. Unchanged values keep their
    recorded evidence, so a re-drive that establishes the same facts writes the same bytes and
    starts no new parse. ``metadata_filled`` records what this module wrote."""
    filled = _as_dict(kept.get("metadata_filled"))
    today = dict(derived, chapter_title=title) if title else dict(derived)
    # today's title pages contradict the licence rule (a copyright notice beside the imprint): a
    # licence this module decided earlier is withdrawn, not carried (a failed request withdraws
    # nothing - it establishes nothing)
    withdrawn = {"license"} if derived.get("license_rejected") else set()
    out: Dict[str, Any] = {}
    mine: Dict[str, Any] = {}
    for key in OWNED:
        if key == "license_basis":  # goes with the licence it explains (below)
            continue
        value, ours = _choose(key, today.get(key), kept.get(key), filled, withdrawn)
        if value:
            out[key] = value
            if ours:
                mine[key] = value
    _licence_basis(out, kept, today, filled, mine)
    _carry_record(out, kept, derived, mine)
    return out


def _choose(
    key: str, now: Any, before: Any, filled: dict, withdrawn: set
) -> Tuple[Any, bool]:
    """(value, whether this module wrote it) of one owned key."""
    if before and filled.get(key) != before:  # an operator's
        return before, False
    if key in withdrawn:
        return None, False
    return now or before, True


def _licence_basis(
    out: dict, kept: dict, today: dict, filled: dict, mine: dict
) -> None:
    """The licence's basis: the rule's for a licence this module decided; never the rule's text
    beside a licence a person chose (it would claim a rule decided it)."""
    if "license" in mine:
        basis = (
            today.get("license_basis")
            if today.get("license") == out["license"]
            else kept.get("license_basis")
        )
        if basis:
            out["license_basis"] = mine["license_basis"] = basis
    elif kept.get("license_basis") and filled.get("license_basis") != kept.get(
        "license_basis"
    ):
        out["license_basis"] = kept["license_basis"]  # a person's own note


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _carry_record(out: dict, kept: dict, derived: dict, mine: dict) -> None:
    """The basis and per-field evidence of the values this module wrote: kept for a value that
    did not change, today's for one that did."""
    old = _as_dict(kept.get("metadata_evidence"))
    new = _as_dict(derived.get("metadata_evidence"))
    evidence: Dict[str, Any] = {}
    changed = False
    for name, key in META_KEY.items():
        if key not in mine:
            continue
        if kept.get(key) == mine[key] and name in old:
            evidence[name] = old[name]
        elif name in new:
            evidence[name], changed = new[name], True
    if evidence:
        # the model whose answer stands: today's if it changed a value, else the recorded one
        first, second = (derived, kept) if changed else (kept, derived)
        out["metadata_basis"] = first.get("metadata_basis") or second.get(
            "metadata_basis", ""
        )
        out["metadata_evidence"] = evidence
    # always recorded (even empty): a meta without the record is read as one from before it
    out["metadata_filled"] = mine


def missing(meta: Dict[str, Any]) -> Tuple[str, ...]:
    """The gap-marker fields ``meta`` still leaves empty (no copyright date is no gap once the
    licence is known: the date only served to decide it)."""
    gaps = [f for f in GAP_FIELDS if not meta.get(f)]
    if meta.get("license") and "copyright_date" in gaps:
        gaps.remove("copyright_date")
    return tuple(gaps)


def for_import(
    document: Document,
    hint_html: Optional[str] = None,
    grok: Any = None,
    transcript: bool = False,
) -> Dict[str, Any]:
    """The chapter-meta keys a completed import's document establishes ({} when disabled or on any
    failure): one Grok request over its opening pages (see the module docstring). A transcript
    (a video) has no imprint, so no licence is decided from what is said in it."""
    if not enabled():
        return {}
    try:
        found = derive(
            opening_text(document),
            page_hint(hint_html),
            grok,
            titles=title_pages(document),
            licence_rule=not transcript,
        )
        return found.meta()
    except (
        Exception
    ) as e:  # noqa: BLE001 - fail-safe: the import goes on without metadata
        logger.warning(
            "OBSERV book_metadata.unavailable status=warn err=%s", type(e).__name__
        )
        return {}
