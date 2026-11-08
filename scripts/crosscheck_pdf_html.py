#!/usr/bin/env python3
"""Cross-check an authoritative scanned PDF against its HyperWar HTML transcription.

Usage:
  python scripts/crosscheck_pdf_html.py --pdf "unprocesseddocs/CROSS-CHANNEL ATTACK.pdf" \\
      --html tests/fixtures/hyperwar/USA-E-XChannel-8.html --first 288 --last 354 \\
      --chandra-md chunk-p0288-0354/input/input.md --out /tmp/xcheck [--crops]

``--first``/``--last`` are PDF page numbers (1-based) of the chapter; ``--chandra-md`` is the
Chandra OCR of exactly those pages (``--paginate_output``, what Phase 0 produces for a scanned
PDF); without it the PDF's embedded text layer is read. Writes ``report.json`` (with input
hashes), ``review.md`` (the differences that need a look, grouped so a systematic one is one
finding, then each with PDF page, printed page and both readings) and, with ``--crops``, a PNG
of each review line from the authoritative page image (the whole page when the line cannot be
located). Deterministic, no model calls.

With ``--corrected-out DIR`` (needs ``--chandra-md``) it also writes the Chandra text with its
OCR artefacts corrected where the book itself settles them (src/ingestion/pdf_text_corrections:
only to readings the PDF - the newer, 1993 printing - itself prints), ``corrected.md``, and the
log of every change, ``corrections.json``; the corrected text is cross-checked again to prove
each correction landed and nothing else changed.

Exit codes: 0 done; 1 a stream (of 50+ HTML words) aligned under ``--min-aligned`` (wrong
pages or a missing stream: the counts are not meaningful); 2 invalid input; 3 the corrections
did not land cleanly (a bug: see corrections.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion import pdf_html_crosscheck as X  # noqa: E402
from src.ingestion import pdf_text_corrections as C  # noqa: E402
from src.ingestion.chunk_pages import split_pages  # noqa: E402
from src.ingestion.hyperwar_html import normalise_hyperwar_html  # noqa: E402


class InputError(ValueError):
    """An argument the run cannot proceed with; the message says how to fix it."""


class CorrectionError(RuntimeError):
    """The corrected text did not cross-check as the corrections promised."""

    def __init__(self, message: str, summary: "dict | None" = None):
        super().__init__(message)
        self.summary = summary or {}


# A stream's aligned share is judged from this many HTML words up (a page's three-word notes
# stream with one changed number aligns 67% and is no sign of wrong pages).
_MIN_GUARDED_WORDS = 50


def _args(argv=None):
    ap = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", maxsplit=1)[0]
    )
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--html", required=True, type=Path)
    ap.add_argument("--first", required=True, type=int, help="first PDF page (1-based)")
    ap.add_argument("--last", required=True, type=int, help="last PDF page (inclusive)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument(
        "--crops", action="store_true", help="save a PNG of each review line"
    )
    ap.add_argument(
        "--chandra-md",
        type=Path,
        help="Chandra OCR markdown of PDF pages --first..--last (what Phase 0 produces for a "
        "scanned PDF); default: the PDF's own embedded text layer",
    )
    ap.add_argument(
        "--min-aligned",
        type=float,
        default=0.9,
        help="fail when a stream aligns fewer than this share of its HTML words",
    )
    ap.add_argument(
        "--corrected-out",
        type=Path,
        help="write corrected.md + corrections.json here (needs --chandra-md)",
    )
    args = ap.parse_args(argv)
    if args.corrected_out and not args.chandra_md:
        ap.error("--corrected-out needs --chandra-md (the text to correct)")
    return args


def _observ(message: str) -> None:
    print(f"OBSERV crosscheck: {message}", file=sys.stderr)


def _check_files(args) -> None:
    for flag, path in (
        ("--pdf", args.pdf),
        ("--html", args.html),
        ("--chandra-md", args.chandra_md),
    ):
        if path is not None and not path.is_file():
            raise InputError(f"{flag} {path}: no such file")


def _open_pdf(path: Path):
    import fitz  # pymupdf (pinned in requirements.txt)

    try:
        return fitz.open(str(path))
    except (RuntimeError, ValueError) as exc:  # fitz.FileDataError is a RuntimeError
        raise InputError(f"--pdf {path}: not a readable PDF ({exc})") from exc


def _validate(args, doc) -> None:
    if not 1 <= args.first <= args.last <= len(doc):
        raise InputError(
            f"--first {args.first} / --last {args.last} must satisfy "
            f"1 <= first <= last <= {len(doc)} (the PDF's page count)"
        )


def _chandra_markdown(args) -> str:
    markdown = args.chandra_md.read_text(encoding="utf-8")
    pages = len(split_pages(markdown, args.first))
    expected = args.last - args.first + 1
    if pages != expected:
        raise InputError(
            f"{args.chandra_md} has {pages} pages but --first..--last spans {expected}: "
            "pass the Chandra chunk of exactly those pages (chunk-pFFFF-LLLL/input/input.md, "
            "run with --paginate_output), not a merged book"
        )
    return markdown


@dataclass
class _Side:
    """The PDF side of a run: its tokens and the evidence they are judged by."""

    tokens: list
    vocab: Counter
    second: "Counter | None" = None  # the chapter's text layer, with --chandra-md
    markdown: "str | None" = None  # the Chandra text, with --chandra-md
    printed: dict = field(default_factory=dict)


def _pdf_side(args, doc, streams, stats: Counter) -> _Side:
    """With ``--chandra-md`` the chapter's words are counted from the Chandra reading (whose small
    caps and accents the 2010 text layer lacks) INSTEAD of the text layer, so no page of the book
    counts twice; the text layer of the chapter's pages is then an independent second OCR of
    the same images."""
    if not args.chandra_md:
        return _Side(X.pdf_tokens(doc, args.first, args.last), X.pdf_vocabulary(doc))
    printed = X.printed_pages(doc, args.first, args.last)
    markdown = _chandra_markdown(args)
    pdf = X.chandra_tokens(markdown, args.first, printed, streams, stats)
    chapter = range(args.first, args.last + 1)
    vocab = X.pdf_vocabulary(doc, skip=chapter)
    vocab.update(X.token_vocabulary(pdf))
    others = [n for n in range(1, len(doc) + 1) if n not in chapter]
    return _Side(pdf, vocab, X.pdf_vocabulary(doc, skip=others), markdown, printed)


def _checks(streams, tokens, side: _Side) -> list:
    """The narrative and notes cross-checks of ``tokens`` against the transcription's streams."""
    narrative, notes = streams
    body = [t for t in tokens if t.kind != "note"]
    note_tokens = [t for t in tokens if t.kind == "note"]
    return [
        X.crosscheck(
            "narrative", narrative, body, vocab=side.vocab, second=side.second
        ),
        X.crosscheck("notes", notes, note_tokens, vocab=side.vocab, second=side.second),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(args) -> dict:
    """Cross-check (and with --corrected-out, correct) one chapter; returns the report."""
    _check_files(args)
    with _open_pdf(args.pdf) as doc:
        _validate(args, doc)
        html, _report = normalise_hyperwar_html(
            args.html.read_text(encoding="utf-8", errors="replace")
        )
        narrative, notes = X.html_streams(html)
        stats: Counter = Counter()
        side = _pdf_side(args, doc, (narrative, notes), stats)
        if not notes and any(t.kind == "note" for t in side.tokens):
            _observ(
                f"the PDF has notes but {args.html} has no Footnotes heading: every note "
                "will read as pdf_only"
            )
        checks = _checks((narrative, notes), side.tokens, side)
        moved = X.reconcile_moved(checks)
        report = _report_dict(args, checks, moved, stats)
        if (
            args.corrected_out
        ):  # replaced by the result; stays if correcting itself crashes
            report["corrections"] = {"status": "error: correcting did not finish"}
        try:
            if args.corrected_out:
                report["corrections"] = _correct(args, side, checks, (narrative, notes))
        except CorrectionError as exc:
            report["corrections"] = exc.summary
            raise
        finally:  # the cross-check itself is valid either way: never leave a stale report
            _write(args, doc, checks, report)
    return report


def _correct(args, side: _Side, checks, streams) -> dict:
    """Write the corrected Chandra text and its log to ``--corrected-out``, after cross-checking
    the corrected text again: exactly the skipped differences may remain correctable, and no
    other difference may change. Otherwise nothing is written but ``corrections.FAILED.json``
    and the run fails - a correction that does not land is a bug, not data."""
    markdown = side.markdown
    if markdown is None:  # _args guarantees --chandra-md with --corrected-out
        raise InputError("--corrected-out needs --chandra-md (the text to correct)")
    newline = _newline(args)  # refuse mixed endings before any work
    plan = C.plan_corrections(checks, markdown, side.vocab, side.second)
    corrected = C.apply_corrections(markdown, plan.corrections)
    tokens = X.chandra_tokens(corrected, args.first, side.printed, streams)
    after = _checks(streams, tokens, side)
    X.reconcile_moved(after)
    remaining = _keys(after, correctable=True)
    landed = remaining == sorted(_key(*k) for k in plan.skipped_differences)
    unchanged = _keys(checks, correctable=False) == _keys(after, correctable=False)
    summary = {
        "source": str(args.chandra_md),
        "source_sha256": _sha256(args.chandra_md),
        "applied": len(plan.corrections),
        "skipped": dict(plan.skipped),
        "correctable_remaining": len(remaining),
        "remaining_are_the_skipped": landed,
        "other_differences_unchanged": unchanged,
    }
    log = {
        **summary,
        "corrections": [asdict(c) for c in plan.corrections],
        "skipped_differences": plan.skipped_differences,
    }
    _observ(f"corrections {summary}")
    _publish(args.corrected_out, landed and unchanged, corrected, log, newline)
    return summary


_OUTPUTS = ("corrected.md", "corrections.json", "corrections.FAILED.json")


def _publish(out: Path, ok: bool, corrected: str, log: dict, newline: str) -> None:
    """corrected.md + corrections.json when the re-check passed, else ONLY
    corrections.FAILED.json - and never an earlier run's files beside them."""
    out.mkdir(parents=True, exist_ok=True)
    for name in _OUTPUTS:
        (out / name).unlink(missing_ok=True)
    if not ok:
        failed = out / "corrections.FAILED.json"
        failed.write_text(json.dumps(log, indent=2), encoding="utf-8")
        raise CorrectionError(
            f"corrections did not land cleanly (remaining are the skipped: "
            f"{log['remaining_are_the_skipped']}, other differences unchanged: "
            f"{log['other_differences_unchanged']}); nothing written but {failed}",
            {
                k: v
                for k, v in log.items()
                if k not in ("corrections", "skipped_differences")
            },
        )
    with (out / "corrected.md").open("w", encoding="utf-8", newline=newline) as f:
        f.write(corrected)
    (out / "corrections.json").write_text(json.dumps(log, indent=2), encoding="utf-8")


def _newline(args) -> str:
    """The source's line endings, so corrected.md differs from it only by the corrections. Mixed
    endings cannot be reproduced through text-mode reading, so they are refused."""
    raw = args.chandra_md.read_bytes()
    crlf = raw.count(b"\r\n")
    lone_cr = raw.count(b"\r") != crlf
    if lone_cr or (crlf and crlf != raw.count(b"\n")):
        raise InputError(
            f"{args.chandra_md} mixes line endings: normalise it (all LF or all CRLF) first"
        )
    return "\r\n" if crlf else "\n"


def _keys(checks, correctable: bool) -> list:
    """The differences as comparable keys (see ``_key``)."""
    return sorted(
        _key(d.cls, d.html, d.pdf)
        for c in checks
        for d in c.differences
        if (d.cls in X.CORRECTABLE) == correctable
    )


def _key(cls: str, html: str, pdf: str) -> tuple:
    """A difference's class and the WORDS on each side, order aside: after a correction the
    alignment may group a one-sided run differently ("Van Fleet Col James A" / "Fleet Col James
    A Van") - the same text, not a change; any changed or lost word still differs."""
    return (cls, tuple(sorted(html.split())), tuple(sorted(pdf.split())))


def _report_dict(args, checks, moved, stats) -> dict:
    inputs = [args.pdf, args.html] + ([args.chandra_md] if args.chandra_md else [])
    return {
        "pdf": str(args.pdf),
        "html": str(args.html),
        "chandra_md": str(args.chandra_md) if args.chandra_md else None,
        "sha256": {str(p): _sha256(p) for p in inputs},
        "pdf_pages": [args.first, args.last],
        "pdf_text": "chandra" if args.chandra_md else "embedded text layer",
        "chandra_paragraphs": dict(stats),
        "moved_pairs": moved,
        "streams": [X.summary(c) for c in checks],
        "differences": {c.stream: [asdict(d) for d in c.differences] for c in checks},
    }


def _write(args, doc, checks, report) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (args.out / "review.md").write_text(_review_md(checks), encoding="utf-8")
    if report["chandra_paragraphs"]:
        _observ(f"Chandra paragraphs {report['chandra_paragraphs']}")
    _observ(f"moved pairs {report['moved_pairs']}")
    if args.crops:
        lines, pages, unplaced = _write_crops(doc, checks, args.out / "crops")
        _observ(
            f"crops: {lines} review lines located, {pages} whole pages for the rest, "
            f"{unplaced} HTML-only items with no PDF page (see review.md for their context)"
        )


def _review_md(checks) -> str:
    lines = ["# PDF vs HyperWar cross-check: differences to look at", ""]
    for check in checks:
        lines += [f"## {check.stream}: grouped (a repeated pair is one systematic finding)", "",
                  "| HTML | PDF | times | printed pages |", "|---|---|---|---|"]  # fmt: skip
        for html, pdf, times, pages in X.grouped_reviews(check):
            shown = ", ".join(str(p) for p in pages if p is not None)
            lines.append(
                f"| {_cell(html)} | {_cell(pdf)} | {times} | {_cell(shown, None)} |"
            )
        lines.append("")
        lines += [f"## {check.stream}: every item", "", "| # | class | PDF page | printed | HTML | PDF | context |",
                  "|---|---|---|---|---|---|---|"]  # fmt: skip
        for i, d in enumerate(_reviews(check)):
            lines.append(
                f"| {i + 1} | {d.cls} | {d.pdf_page} | {d.printed_page} | {_cell(d.html)} | "
                f"{_cell(d.pdf)} | {_cell(d.context)} |"
            )
        lines.append("")
    return "\n".join(lines)


def _reviews(check):
    return [d for d in check.differences if d.cls in X.NEEDS_A_LOOK]


def _cell(text: str, limit: "int | None" = 120) -> str:
    """A markdown table cell; long readings are shortened (report.json keeps them whole)."""
    cell = (text or "").replace("|", "\\|")
    return cell if limit is None or len(cell) <= limit else cell[: limit - 1] + "…"


def _write_crops(doc, checks, folder: Path) -> "tuple[int, int, int]":
    """A PNG of each review line: its bbox (text layer), else the PDF reading's position when
    it occurs once on the page (Chandra tokens carry no position), else the whole page once.
    Returns (lines cropped, whole pages written, items with no PDF page - HTML-only text).
    """
    import fitz

    folder.mkdir(parents=True, exist_ok=True)
    cropped, whole, unplaced = 0, set(), 0
    for check in checks:
        for i, d in enumerate(_reviews(check)):
            if not d.pdf_page:
                unplaced += 1
                continue
            page = doc[d.pdf_page - 1]
            bbox = d.bbox or _locate(page, d.pdf)
            if bbox is None:
                whole.add(d.pdf_page)
                continue
            x0, y0, x1, y1 = bbox
            clip = fitz.Rect(max(0, x0 - 20), max(0, y0 - 14), min(page.rect.width, x1 + 20),
                             min(page.rect.height, y1 + 14))  # fmt: skip
            page.get_pixmap(clip=clip, dpi=200).save(
                str(folder / f"{check.stream}-{i + 1:04d}-p{d.pdf_page}.png")
            )
            cropped += 1
    for number in sorted(whole):
        doc[number - 1].get_pixmap(dpi=110).save(str(folder / f"page-p{number}.png"))
    return cropped, len(whole), unplaced


def _locate(page, reading: str):
    """The reading's bbox on the page when it occurs there exactly once, else None."""
    hits = page.search_for(reading) if reading else []
    return tuple(hits[0]) if len(hits) == 1 else None


def main(argv=None) -> int:
    """Run the cross-check; the exit code is in the module docstring."""
    args = _args(argv)
    try:
        report = run(args)
    except InputError as exc:
        print(f"crosscheck: {exc}", file=sys.stderr)
        return 2
    except CorrectionError as exc:
        print(f"crosscheck: {exc}", file=sys.stderr)
        return 3
    for s in report["streams"]:
        print(json.dumps(s))
    low = [
        s
        for s in report["streams"]
        if s["html_words"] >= _MIN_GUARDED_WORDS
        and s["aligned_share"] < args.min_aligned
    ]
    for s in low:
        print(
            f"crosscheck: {s['stream']} aligned only {s['aligned_share']:.0%} of its HTML "
            f"words (< {args.min_aligned:.0%}): check --first/--last and the page's notes; "
            "the counts above are not meaningful",
            file=sys.stderr,
        )
    return 1 if low else 0


if __name__ == "__main__":
    raise SystemExit(main())
