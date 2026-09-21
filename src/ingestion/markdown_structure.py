"""Repair Chandra's markdown structural blind spots (post-OCR, block-level).

Chandra converts scanned pages to markdown well, but a validated probe (St. Vith
/ Boyer PDF; see docs/current/dataquality/CHANDRA_OCR_DESIGN.md "Operational
findings") established two structural-fidelity gaps where Chandra **captures the
content but drops the block-type markup**, and neither closed after a model
update:

* **Block quotes are not marked** — an indented source quotation is emitted as
  ordinary paragraphs with quote characters, no ``>`` / ``<blockquote>``. For a
  citable corpus this is an *attribution-integrity* issue: a quotation can be
  mistaken for the author's own assertion. (This module.)
* **Complex horizontal / 2-D tables are flattened** — a task-organization table
  (columns = time snapshots, rows = command groups) comes through as sequential
  vertical lists with no ``<table>`` markup. Recognized and flagged for review
  with a structured hint tree; never fabricated into a grid we cannot verify.
  (``detect_flattened_tables``.)

This module owns both halves. The block-quote half detects quotation blocks in
prose markdown and re-marks them as markdown blockquotes (``> ``) carrying the
attributed source, so the quote's *own* provenance (the quoted work) is
preserved distinctly from the containing document's provenance. It follows the
project's verification-flagging discipline: it never rewrites the words of a
quotation, only adds the ``>`` marker and records what it did; low-confidence
detections are flagged for review rather than silently applied. The table half
never rewrites at all — it flags.

Downstream, ``src/parser.py`` already recognizes ``> `` blockquotes
(``_BLOCKQUOTE_PATTERN``) — Chandra simply never emitted them. Re-marking here
makes the existing parser (and the RAG layer) treat the passage as a quotation.

See docs/current/dataquality/INGESTION_FRONT_END.md
("Chandra markdown structural blind spots").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# --- Detection signals ---------------------------------------------------

# A paragraph that introduces a quotation. Chandra keeps these as prose; they
# are the strongest, most reliable cue that the *following* paragraph(s) are a
# quotation. Kept deliberately conservative (explicit attribution verbs / a
# trailing colon + page cite) to avoid re-marking ordinary colon-ended prose.
_LEADIN_RE = re.compile(
    r"""
    (?:
        \bin\s+the\s+words\s+of\b        # "In the words of ..."
      | \b(?:wrote|writes|said|says|stated|states|
             quoted|recounts|observed|noted)\b
      | \baccording\s+to\b
    )
    .*:\s*                                # ... up to a colon
    (?:\s*\(?(?:pp?|pages?)\.?\s*[\divxlcIVXLC]+ # optional trailing page cite
        (?:\s*[-\u2013]\s*[\divxlcIVXLC]+)?\)?)?  #   e.g. (pp. 270-271)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A page-citation fragment on its own (used to strengthen a weaker lead-in).
_PAGE_CITE_RE = re.compile(
    r"\(?\b(?:pp?|pages?)\.?\s*[\divxlcIVXLC]+"
    r"(?:\s*[-\u2013]\s*[\divxlcIVXLC]+)?\)?",
    re.IGNORECASE,
)

# An opening double quote at the very start of a paragraph. Straight or curly.
_OPEN_QUOTE_RE = re.compile(r'^\s*["\u201c]')

# A closing double quote at the very end of a paragraph. Straight or curly.
_CLOSE_QUOTE_RE = re.compile(r'["\u201d]\s*$')

# Confidence levels, mirroring the disposition classifier's vocabulary.
_CONF_CLEAR = 0.9  # explicit lead-in + quoted paragraph(s)
_CONF_WEAK = 0.55  # quote-shaped block without an explicit lead-in
_REVIEW_BELOW = 0.6


@dataclass
class BlockQuoteSpan:  # pylint: disable=too-many-instance-attributes
    """A detected quotation within a markdown document.

    Attributes:
        start_para: Index (into the paragraph list) of the first quoted
            paragraph.
        end_para: Index of the last quoted paragraph (inclusive).
        leadin_para: Index of the introducing paragraph (e.g. "In the words of
            ... Top Secret: (pp. 270-271)"), or ``None`` if none was found.
        attribution: The lead-in text, when present — the quote's own source.
        page_cite: A page citation extracted from the lead-in (e.g.
            "pp. 270-271"), when present.
        confidence: Detector confidence in [0.0, 1.0].
        needs_review: True when the detection is weak and a human should confirm.
        notes: Why it was detected / flagged.
    """

    start_para: int
    end_para: int
    leadin_para: Optional[int] = None
    attribution: Optional[str] = None
    page_cite: Optional[str] = None
    confidence: float = _CONF_WEAK
    needs_review: bool = True
    notes: str = ""


@dataclass
class BlockQuoteResult:
    """Result of running the block-quote detector on one markdown document.

    Attributes:
        spans: Detected quotation spans, in document order.
        markdown: The document with detected quotations re-marked as ``> ``
            blockquotes (only clear detections are applied; weak ones are left
            as-is but reported in ``spans`` for review).
        changed: True when at least one blockquote was applied to ``markdown``.
    """

    spans: List[BlockQuoteSpan] = field(default_factory=list)
    markdown: str = ""
    changed: bool = False


def _split_paragraphs(markdown: str) -> List[str]:
    """Split markdown into blank-line-separated paragraph blocks.

    Blocks are returned verbatim (no stripping) so the re-marked output can be
    reassembled without disturbing untouched content.
    """
    # Normalize newlines, then split on one-or-more blank lines.
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return re.split(r"\n\s*\n", text)


def _is_heading_or_table(block: str) -> bool:
    """True for blocks that are structurally not prose (headings, tables, hr)."""
    stripped = block.lstrip()
    return (
        stripped.startswith("#")
        or stripped.startswith("<table")
        or stripped.startswith("|")
        or stripped.startswith("---")
        or stripped.startswith("![")
    )


def _looks_quoted(block: str) -> bool:
    """True when a paragraph looks like quoted material (opens with a quote)."""
    return bool(_OPEN_QUOTE_RE.search(block.strip()))


def _quote_run_end(blocks: List[str], start: int) -> int:
    """Return the index just past a contiguous run of quote-shaped paragraphs."""
    j = start
    n = len(blocks)
    while j < n and blocks[j].strip() and _looks_quoted(blocks[j]):
        j += 1
    return j


def _detect_leadin_quote(blocks: List[str], i: int) -> Optional[BlockQuoteSpan]:
    """Case 1: an explicit lead-in followed by quoted paragraph(s) (high conf)."""
    if not _LEADIN_RE.search(blocks[i].strip()):
        return None
    start = i + 1
    end = _quote_run_end(blocks, start)
    if end <= start:
        return None
    page_cite_match = _PAGE_CITE_RE.search(blocks[i])
    return BlockQuoteSpan(
        start_para=start,
        end_para=end - 1,
        leadin_para=i,
        attribution=blocks[i].strip(),
        page_cite=(page_cite_match.group(0).strip("() ") if page_cite_match else None),
        confidence=_CONF_CLEAR,
        needs_review=False,
        notes="explicit lead-in followed by quoted paragraph(s)",
    )


def _detect_weak_quote(blocks: List[str], i: int) -> Optional[BlockQuoteSpan]:
    """Case 2: a run of quote-shaped paragraphs with no lead-in (weak, flagged)."""
    if not _looks_quoted(blocks[i]):
        return None
    end = _quote_run_end(blocks, i)
    run = blocks[i:end]
    # Require a closing quote somewhere in the run, to avoid catching
    # dialogue-heavy prose.
    if not any(_CLOSE_QUOTE_RE.search(b.strip()) for b in run):
        return None
    return BlockQuoteSpan(
        start_para=i,
        end_para=end - 1,
        leadin_para=None,
        attribution=None,
        page_cite=None,
        confidence=_CONF_WEAK,
        needs_review=True,
        notes="quote-shaped paragraph(s) without explicit lead-in",
    )


def detect_block_quotes(markdown: str) -> BlockQuoteResult:
    """Detect quotation blocks in Chandra prose markdown.

    Strategy (conservative, provenance-preserving):

    1. Split into paragraph blocks.
    2. A ``lead-in`` paragraph (explicit attribution verb / "in the words of"
       ending in a colon, optionally with a page cite) marks that the *next*
       run of quote-shaped paragraphs is a quotation — high confidence.
    3. A run of paragraphs that each open with a double quote, with no lead-in,
       is a weaker signal — detected but flagged for review, not auto-applied.

    The detector never alters the quotation's words; re-marking adds ``> `` only.
    """
    blocks = _split_paragraphs(markdown)
    spans: List[BlockQuoteSpan] = []

    i = 0
    n = len(blocks)
    while i < n:
        block = blocks[i]
        if not block.strip() or _is_heading_or_table(block):
            i += 1
            continue

        span = _detect_leadin_quote(blocks, i) or _detect_weak_quote(blocks, i)
        if span is not None:
            spans.append(span)
            i = span.end_para + 1
            continue

        i += 1

    markdown_out, changed = _apply_block_quotes(blocks, spans)
    return BlockQuoteResult(spans=spans, markdown=markdown_out, changed=changed)


def _apply_block_quotes(
    blocks: List[str], spans: List[BlockQuoteSpan]
) -> Tuple[str, bool]:
    """Re-mark clear-confidence quotation spans as ``> `` blockquotes.

    Only spans with ``needs_review=False`` are applied. Weak spans are reported
    (in the result) but the text is left untouched, per the project's
    "flag, never silently rewrite" discipline. The quotation's words are
    unchanged — each line of the quoted paragraph is prefixed with ``> ``.
    """
    apply_indices = {
        idx
        for span in spans
        if not span.needs_review
        for idx in range(span.start_para, span.end_para + 1)
    }
    if not apply_indices:
        return "\n\n".join(blocks), False

    out: List[str] = []
    for idx, block in enumerate(blocks):
        if idx in apply_indices:
            quoted = "\n".join(
                ("> " + line) if line.strip() else ">" for line in block.split("\n")
            )
            out.append(quoted)
        else:
            out.append(block)
    return "\n\n".join(out), True


# =========================================================================
# Flattened task-organization / 2-D table detection (Chandra blind spot #2)
# =========================================================================
#
# Chandra flattens complex horizontal / 2-D task-organization tables into
# sequential vertical lists with no ``<table>`` markup (the P155 failure). The
# original 2-D structure is: COLUMNS are time snapshots (e.g. 170300, 172400)
# and ROWS are command groups (CC-A, CC-B, DIV ARTY, ...) each listing assigned
# units. Chandra emits: a snapshot header, then repeated ``<group-header>`` +
# unit-token lines.
#
# Per the design (INGESTION_FRONT_END.md "Chandra markdown structural blind
# spots"): re-structure OR flag ``needs_review`` — never fabricate a grid we
# cannot verify. Reconstructing the exact column alignment across snapshots is a
# guess (a group may be absent in one snapshot), so this detector does the
# safe thing: it recognizes the flattened block, extracts a
# ``snapshot -> group -> units`` tree as *hints*, and flags it for review. The
# original text is left untouched; the hints ride alongside for a human/reviewer
# (and a later, cost-gated re-read of the source image if exact reconstruction
# is ever needed).

# Command-group row headers seen in US WWII division task-org tables. Matched at
# the start of a line. Kept explicit (not a broad heuristic) to avoid catching
# ordinary prose. Extendable as new group forms appear.
_GROUP_HEADER_RE = re.compile(
    r"^(CC-?[A-RZ]|CCA|CCB|CCR"  # combat commands (A/B/R and reserve)
    r"|DIV\s+ARTY|DIV\s+TRRS|DIV\s+TMS"  # division artillery / troops / trains
    r"|DIVARTY|TF\s+\w+"  # task forces
    r"|RESERVE|DIV\s+HQ)\b",
    re.IGNORECASE,
)

# A time-snapshot column header: 4-6 digits (HHMM or DDHHMM military time),
# optionally with a trailing descriptor ("170300 - March South").
_SNAPSHOT_RE = re.compile(r"^\s*(\d{4,6})\b\s*(?:-\s*(.*))?$")

# A unit-designation token line: short, unit-code shaped (numbers, slashes,
# parenthetical detachments, letter prefixes). Deliberately narrow so prose
# never matches. e.g. "A/33", "203(-A,B,C)", "6 M Tks/31", "H&HqBtry/203".
_UNIT_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Za-z0-9&/()\-,. ]{0,28}$")

# A block must have at least this many group headers to be a task-org table
# (avoids firing on an incidental single header line).
_MIN_GROUPS = 2
# Confidence for a recognized-but-not-reconstructed flattened table.
_CONF_TABLE = 0.55


@dataclass
class TaskOrgUnit:
    """One command group and the unit tokens listed under it in a snapshot."""

    group: str
    units: List[str] = field(default_factory=list)


@dataclass
class TaskOrgSnapshot:
    """One time-snapshot column of a flattened task-org table."""

    label: str  # e.g. "170300" (the snapshot / column key)
    descriptor: Optional[str] = None  # e.g. "March South"
    groups: List[TaskOrgUnit] = field(default_factory=list)


@dataclass
class FlattenedTableSpan:
    """A detected flattened task-organization table (Chandra blind spot #2).

    Attributes:
        start_para: Paragraph index of the first block of the flattened table.
        end_para: Paragraph index of the last block (inclusive).
        snapshots: Extracted ``snapshot -> group -> units`` hint tree. NOT a
            reconstructed grid — a structured view of what was flattened, for
            review.
        confidence: Detector confidence in [0.0, 1.0].
        needs_review: Always True — the 2-D structure is recognized but not
            authoritatively reconstructed, so a human confirms.
        notes: Why it was detected.
    """

    start_para: int
    end_para: int
    snapshots: List[TaskOrgSnapshot] = field(default_factory=list)
    confidence: float = _CONF_TABLE
    needs_review: bool = True
    notes: str = ""


@dataclass
class TableRepairResult:
    """Result of scanning a markdown document for flattened task-org tables.

    Attributes:
        spans: Detected flattened-table spans, in document order.
        changed: False — this detector never rewrites the markdown (flag-only).
    """

    spans: List[FlattenedTableSpan] = field(default_factory=list)
    changed: bool = False

    def to_hint_dicts(self) -> List[dict]:
        """Serialize detected flattened tables as plain dicts for persistence.

        Each dict is a review-flagged hint tree (snapshot -> group -> units)
        with its line span and note — not an authoritative grid. Suitable for
        attaching to a parsed document's ``table_hints`` and writing to JSON.
        """
        return [
            {
                "start_line": span.start_para,
                "end_line": span.end_para,
                "confidence": span.confidence,
                "needs_review": span.needs_review,
                "notes": span.notes,
                "snapshots": [
                    {
                        "label": snap.label,
                        "descriptor": snap.descriptor,
                        "groups": [
                            {"group": g.group, "units": list(g.units)}
                            for g in snap.groups
                        ],
                    }
                    for snap in span.snapshots
                ],
            }
            for span in self.spans
        ]


def _is_group_header(line: str) -> bool:
    return bool(_GROUP_HEADER_RE.match(line.strip()))


def _is_unit_token(line: str) -> bool:
    s = line.strip()
    if not s or _is_group_header(s):
        return False
    # Note: do NOT reject snapshot-shaped (bare 4-6 digit) lines here — a numeric
    # unit designation (e.g. "3967" under DIV TMS) is a valid unit. Snapshot vs.
    # numeric-unit disambiguation is handled at the call site via lookahead.
    # Unit tokens are short and code-shaped; reject anything sentence-like.
    if " " in s and len(s.split()) > 4:
        return False
    return bool(_UNIT_TOKEN_RE.match(s))


class _TaskOrgScanner:  # pylint: disable=too-many-instance-attributes
    """Line-by-line state machine for detecting flattened task-org tables.

    Encapsulates the scan state (current snapshot/group/region) so the driver
    loop is a simple dispatch. Recognizes the
    ``snapshot -> (group header, unit tokens)+`` pattern and emits a span per
    contiguous task-org region with enough structure.
    """

    def __init__(self, lines: List[str]) -> None:
        self.lines = lines
        self.spans: List[FlattenedTableSpan] = []
        self.snapshots: List[TaskOrgSnapshot] = []
        self.cur_snapshot: Optional[TaskOrgSnapshot] = None
        self.cur_group: Optional[TaskOrgUnit] = None
        self.region_start: Optional[int] = None
        self.region_end = 0

    def _next_nonempty(self, idx: int) -> Optional[str]:
        for k in range(idx + 1, len(self.lines)):
            s = self.lines[k].strip()
            if s:
                return s
        return None

    def flush(self) -> None:
        """Emit a span for the collected region if it has enough structure."""
        group_total = sum(len(s.groups) for s in self.snapshots)
        if (
            self.snapshots
            and group_total >= _MIN_GROUPS
            and self.region_start is not None
        ):
            self.spans.append(
                FlattenedTableSpan(
                    start_para=self.region_start,
                    end_para=self.region_end,
                    snapshots=self.snapshots,
                    notes=(
                        f"flattened task-org table: {len(self.snapshots)} snapshot(s), "
                        f"{group_total} group row(s); 2-D structure recognized "
                        "but not reconstructed (flagged for review)"
                    ),
                )
            )
        self.snapshots = []
        self.cur_snapshot = None
        self.cur_group = None
        self.region_start = None

    def _open_region(self, lineno: int) -> None:
        if self.region_start is None:
            self.region_start = lineno
        self.region_end = lineno

    def _is_snapshot_line(self, line: str, lineno: int) -> "Optional[re.Match[str]]":
        """A bare 4-6 digit line is a snapshot column (not a numeric unit) when it
        has a descriptor or the next non-empty line is a group header."""
        snap = _SNAPSHOT_RE.match(line)
        if snap is None:
            return None
        nxt = self._next_nonempty(lineno)
        if snap.group(2) or (nxt is not None and _is_group_header(nxt)):
            return snap
        return None

    def feed(self, lineno: int, line: str) -> None:
        """Process one non-empty stripped line."""
        snap = self._is_snapshot_line(line, lineno)
        if snap is not None:
            descriptor = snap.group(2)
            self.cur_snapshot = TaskOrgSnapshot(
                label=snap.group(1),
                descriptor=descriptor.strip() if descriptor else None,
            )
            self.snapshots.append(self.cur_snapshot)
            self.cur_group = None
            self._open_region(lineno)
            return

        if _is_group_header(line):
            # A group header outside any snapshot still implies a task-org block
            # (some tables have no explicit leading time); open an implicit one.
            if self.cur_snapshot is None:
                self.cur_snapshot = TaskOrgSnapshot(label="(unlabeled)")
                self.snapshots.append(self.cur_snapshot)
                self._open_region(lineno)
            self.cur_group = TaskOrgUnit(group=line)
            self.cur_snapshot.groups.append(self.cur_group)
            self.region_end = lineno
            return

        if self.cur_group is not None and _is_unit_token(line):
            self.cur_group.units.append(line)
            self.region_end = lineno
            return

        # A non-matching, non-empty line ends the current task-org region.
        if self.region_start is not None:
            self.flush()


def detect_flattened_tables(markdown: str) -> TableRepairResult:
    """Detect flattened task-organization / 2-D tables in Chandra markdown.

    Recognizes the ``snapshot header -> (group header, unit tokens)+`` pattern
    Chandra produces when it flattens a 2-D task-org table, and extracts a
    structured hint tree. Always flags ``needs_review`` and never rewrites the
    source (the exact grid cannot be reconstructed without guessing).
    """
    # Work line-by-line across the whole document (the flattened table spans many
    # short blocks; paragraph splitting fragments it).
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    scanner = _TaskOrgScanner(text.split("\n"))

    for lineno, raw in enumerate(scanner.lines):
        line = raw.strip()
        if line:
            scanner.feed(lineno, line)

    scanner.flush()
    return TableRepairResult(spans=scanner.spans, changed=False)
