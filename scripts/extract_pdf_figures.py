#!/usr/bin/env python3
"""Extract the figures (maps, charts, photographs) of a scanned book PDF - from the PDF itself,
no OCR - with the book's own numbers, titles, captions and credits, the best-resolution copy of
each, and the passages of the text that cite them (src/ingestion/pdf_figures).

Usage (from the repo root, venv active):
  python scripts/extract_pdf_figures.py --pdf "unprocesseddocs/CROSS-CHANNEL ATTACK.pdf" \\
      --chunks /tmp/xch/ocr --out /tmp/xch/figures \\
      --hyperwar-index https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html \\
      --text ch1=/tmp/xcheck-book/ch1/corrected/corrected.md:20 [--text ...]

``--chunks``: the folder of Chandra OCR chunks (``chunk-pFFFF-LLLL/input/`` with input.md,
input_metadata.json and the crop files) - the front-matter chunk holds the book's lists.
``--hyperwar-index``: an optional other copy of the book; its images are matched BY TITLE and win
only when they have more pixels. ``--text UNIT=PATH:FIRST_PDF_PAGE``: corrected text of a unit,
for the references ("See Map VII"). Writes ``figures.json`` (provenance: input hashes, every
candidate and why the chosen one won) and ``files/``. A re-run keeps the figure IDs.

Exit codes: 0 done; 1 a list entry has no figure, or a citation names a number the book's lists
do not have (both printed, to look at); 2 invalid input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion import pdf_figures as F  # noqa: E402
from src.ingestion import pdf_html_crosscheck as X  # noqa: E402

_CHUNK = re.compile(r"^chunk-p(\d{4})-(\d{4})$")
_USER_AGENT = "Mozilla/5.0 (research; SecondWorldWarAsData)"


class InputError(ValueError):
    """An argument the run cannot proceed with; the message says how to fix it."""


def _args(argv=None):
    ap = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", maxsplit=1)[0]
    )
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--chunks", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--hyperwar-index", help="URL or file of a HyperWar book index")
    ap.add_argument(
        "--text",
        action="append",
        default=[],
        help="UNIT=PATH:FIRST_PDF_PAGE of a corrected text unit (repeatable)",
    )
    return ap.parse_args(argv)


def _observ(message: str) -> None:
    print(f"OBSERV figures: {message}", file=sys.stderr)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunks(folder: Path):
    """The OCR chunks, in page order."""
    out = []
    for d in sorted(folder.iterdir()) if folder.is_dir() else []:
        m = _CHUNK.match(d.name)
        if m and (d / "input" / "input.md").is_file():
            out.append(F.Chunk(int(m.group(1)), d / "input"))
    if not out:
        raise InputError(
            f"--chunks {folder}: no chunk-pFFFF-LLLL/input/input.md folders"
        )
    return out


def _front(chunks) -> F.FrontMatter:
    """The book's lists, from the first chunk that has them."""
    for chunk in chunks:
        front = F.front_matter((chunk.folder / "input.md").read_text(encoding="utf-8"))
        if front.entries:
            return front
    raise InputError("no chunk holds the book's lists of maps / charts / illustrations")


def _texts(specs):
    """[(unit, path, first PDF page)] from UNIT=PATH:PAGE."""
    out = []
    for spec in specs:
        unit, _, rest = spec.partition("=")
        path, _, page = rest.rpartition(":")
        if not (unit and path and page.isdigit() and Path(path).is_file()):
            raise InputError(
                f"--text {spec}: expected UNIT=PATH:FIRST_PDF_PAGE of a file"
            )
        out.append((unit, Path(path), int(page)))
    return out


def _known_ids(out: Path):
    """{(PDF page, position): figure_id} of an earlier run in --out."""
    old = out / "figures.json"
    if not old.is_file():
        return {}
    data = json.loads(old.read_text(encoding="utf-8"))
    return {
        (f["pdf_page"], f["position"]): f["figure_id"] for f in data.get("figures", [])
    }


_MAX_DOWNLOAD = 50 * 1024 * 1024  # bytes: a map scan is a few MB


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https URL (never down to http or another scheme)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        if not newurl.startswith("https://"):
            raise urllib.error.URLError(
                f"refusing a redirect to a non-https URL: {newurl}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetcher(cache: Path):
    """``fetch(url) -> bytes``: https only (redirects too), images only, size-capped, cached on
    disk (written atomically) with a polite pause between real requests."""
    cache.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)

    def fetch(url: str) -> bytes:
        if not url.startswith("https://"):
            raise InputError(f"refusing a non-https URL: {url}")
        path = cache / hashlib.sha256(url.encode()).hexdigest()
        if path.is_file():
            return path.read_bytes()
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        # https only (checked above and in the redirect handler)
        with opener.open(request, timeout=60) as response:  # nosec B310
            kind = response.headers.get("Content-Type", "")
            data = response.read(_MAX_DOWNLOAD + 1)
        if len(data) > _MAX_DOWNLOAD:
            raise ValueError(f"{url}: larger than {_MAX_DOWNLOAD} bytes")
        if not (kind.startswith("image/") or url.endswith(".html")):
            raise ValueError(f"{url}: not an image ({kind or 'no Content-Type'})")
        partial = path.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(path)  # never a half-written cache hit
        time.sleep(1.0)
        return data

    return fetch


def _other_copies(args, fetch):
    if not args.hyperwar_index:
        return [], None
    source = args.hyperwar_index
    if source.startswith("https://"):
        html = fetch(source).decode("latin-1")
    elif Path(source).is_file():
        html = Path(source).read_text(encoding="latin-1")
        source = str(Path(source).resolve())
    else:
        raise InputError(f"--hyperwar-index {source}: neither an https URL nor a file")
    base = args.hyperwar_index if args.hyperwar_index.startswith("https://") else ""
    return F.hyperwar_copies(html, base), source


def run(args) -> dict:
    """Extract, compare, cite; write figures.json. Returns the report."""
    import fitz  # pymupdf (pinned)

    if not args.pdf.is_file():
        raise InputError(f"--pdf {args.pdf}: no such file")
    chunks = _chunks(args.chunks)
    front = _front(chunks)
    texts = _texts(args.text)
    with fitz.open(str(args.pdf)) as doc:
        printed = X.printed_pages(doc, 1, len(doc))
        figures = F.build_figures(
            doc, chunks, front, printed, args.out, _known_ids(args.out)
        )
    fetch = _fetcher(args.out / ".cache")
    copies, copies_source = _other_copies(args, fetch)
    copy_log = (
        F.add_other_copies(figures, copies, fetch, args.out) if copies else F.CopyLog()
    )
    refs = [
        ref
        for unit, path, first in texts
        for ref in F.references(path.read_text(encoding="utf-8"), first, unit)
    ]
    unmatched_refs = F.attach_references(figures, refs)
    return _report(
        args, figures, front, chunks, texts, copies_source, unmatched_refs, copy_log
    )


def _report(
    args, figures, front, chunks, texts, copies_source, unmatched_refs, copy_log
) -> dict:
    placed = {(f.kind, f.number, f.title) for f in figures if f.match}
    missing = [
        asdict(e) for e in front.entries if (e.kind, e.number, e.title) not in placed
    ]
    report = {
        "pdf": str(args.pdf),
        "pdf_sha256": _sha256(args.pdf),
        "ocr_chunks": {str(c.folder): _sha256(c.folder / "input.md") for c in chunks},
        "other_copies": copies_source,
        "texts": {unit: {"path": str(p), "sha256": _sha256(p), "first_pdf_page": first}
                  for unit, p, first in texts},  # fmt: skip
        "counts": _counts(figures),
        "list_entries_without_figure": missing,
        "citations_of_unlisted_numbers": unmatched_refs,
        "other_copies_log": asdict(copy_log),
        "figures": [asdict(f) for f in figures],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "figures.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def _counts(figures) -> dict:
    from collections import Counter

    return {
        "figures": len(figures),
        "by_kind": dict(Counter(f.kind for f in figures)),
        "by_source": dict(Counter(f.source for f in figures)),
        "by_match": dict(Counter(f.match or "unlisted" for f in figures)),
        "fallbacks": sum(1 for f in figures if f.chosen_because.startswith("FALLBACK")),
        "references": sum(len(f.references) for f in figures),
    }


def _observ_copies(log: dict) -> None:
    """One line per kind of disagreement with the other source, each saying what to look at."""
    from collections import Counter

    _observ(f"other copies: {len(log['matched'])} matched by title, "
            f"{len(log['unused_copies'])} of its copies matched no figure")  # fmt: skip
    for miss in log["near_misses"]:
        _observ(f"near miss {miss['title_score']}: {miss['title']!r} vs the other copy's "
                f"{miss['copy_title']!r} - an OCR-garbled title, or another figure")  # fmt: skip
    shifts = Counter(_shift(d["pdf"], d["copy"]) for d in log["number_differences"])
    for shift, times in shifts.items():
        _observ(f"numbering: {times} figure(s) numbered {shift} in the other copy - a printing's "
                "renumbering when systematic; the PDF's number is kept")  # fmt: skip
    for diff in log["shape_differences"]:
        _observ(f"shape differs: {diff['title']!r} is {diff['pdf']} in the PDF (page "
                f"{diff['pdf_page']}) but {diff['copy']} at {diff['url']} - likely a different "
                "picture linked under that title; the PDF's is kept, look at the copy")  # fmt: skip
    for failure in log["failed"]:
        _observ(f"download failed, skipped: {failure['url']}: {failure['error']}")


def _shift(pdf: str, copy: str) -> str:
    """'+1' when both are numbers (Roman or Arabic) one apart, else 'X -> Y'."""
    a, b = _as_int(pdf), _as_int(copy)
    if a is None or b is None:
        return f"{pdf} -> {copy}"
    return f"{b - a:+d}"


def _as_int(number: str):
    if number.isdigit():
        return int(number)
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    if not number or any(ch not in values for ch in number):
        return None
    total = 0
    for k, ch in enumerate(number):
        v = values[ch]
        total += -v if k + 1 < len(number) and values[number[k + 1]] > v else v
    return total


def main(argv=None) -> int:
    """Run; the exit code is in the module docstring."""
    args = _args(argv)
    try:
        report = run(args)
    except InputError as exc:
        print(f"figures: {exc}", file=sys.stderr)
        return 2
    _observ(f"counts {report['counts']}")
    _observ_copies(report["other_copies_log"])
    problems = 0
    for entry in report["list_entries_without_figure"]:
        print(f"figures: no figure for {entry['kind']} {entry['number'] or ''} "
              f"{entry['title']!r} (printed page {entry['printed_page']})", file=sys.stderr)  # fmt: skip
        problems += 1
    for number, times in report["citations_of_unlisted_numbers"].items():
        print(f"figures: the text cites {number} {times}x but the book's lists have no such "
              "number - an OCR misreading or a map this printing lacks", file=sys.stderr)  # fmt: skip
        problems += 1
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
