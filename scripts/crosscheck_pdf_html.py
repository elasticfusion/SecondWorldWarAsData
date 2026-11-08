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

Exit codes: 0 done; 1 a stream (of 50+ HTML words) aligned under ``--min-aligned`` (wrong
pages or a missing stream: the counts are not meaningful); 2 invalid input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion import pdf_html_crosscheck as X  # noqa: E402
from src.ingestion.chunk_pages import split_pages  # noqa: E402
from src.ingestion.hyperwar_html import normalise_hyperwar_html  # noqa: E402


class InputError(ValueError):
    """An argument the run cannot proceed with; the message says how to fix it."""


# A stream's aligned share is judged from this many HTML words up (a page's three-word notes
# stream with one changed number aligns 67% and is no sign of wrong pages).
_MIN_GUARDED_WORDS = 50


def _args(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
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
    return ap.parse_args(argv)


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


def _pdf_side(args, doc, streams, stats: Counter):
    """(PDF tokens, whole-book vocabulary, second reading). With ``--chandra-md`` the chapter's
    words are counted from the Chandra reading (whose small caps and accents the 2010 text layer
    lacks) INSTEAD of the text layer, so no page of the book counts twice; the text layer of the
    chapter's pages is then an independent second OCR of the same images."""
    if not args.chandra_md:
        return X.pdf_tokens(doc, args.first, args.last), X.pdf_vocabulary(doc), None
    printed = X.printed_pages(doc, args.first, args.last)
    pdf = X.chandra_tokens(_chandra_markdown(args), args.first, printed, streams, stats)
    chapter = range(args.first, args.last + 1)
    vocab = X.pdf_vocabulary(doc, skip=chapter)
    vocab.update(X.token_vocabulary(pdf))
    others = [n for n in range(1, len(doc) + 1) if n not in chapter]
    return pdf, vocab, X.pdf_vocabulary(doc, skip=others)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(args) -> dict:
    _check_files(args)
    with _open_pdf(args.pdf) as doc:
        _validate(args, doc)
        html, _report = normalise_hyperwar_html(
            args.html.read_text(encoding="utf-8", errors="replace")
        )
        narrative, notes = X.html_streams(html)
        stats: Counter = Counter()
        pdf, vocab, second = _pdf_side(args, doc, (narrative, notes), stats)
        if not notes and any(t.kind == "note" for t in pdf):
            _observ(
                f"the PDF has notes but {args.html} has no Footnotes heading: every note "
                "will read as pdf_only"
            )
        evidence = {"vocab": vocab, "second": second}
        checks = [
            X.crosscheck(
                "narrative", narrative, [t for t in pdf if t.kind != "note"], **evidence
            ),
            X.crosscheck(
                "notes", notes, [t for t in pdf if t.kind == "note"], **evidence
            ),
        ]
        moved = X.reconcile_moved(checks)
        report = _report_dict(args, checks, moved, stats)
        _write(args, doc, checks, report)
    return report


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
    hits = page.search_for(reading) if reading else []
    return tuple(hits[0]) if len(hits) == 1 else None


def main(argv=None) -> int:
    args = _args(argv)
    try:
        report = run(args)
    except InputError as exc:
        print(f"crosscheck: {exc}", file=sys.stderr)
        return 2
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
