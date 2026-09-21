"""Convert non-PDF text document formats to the pipeline's markdown contract.

Requirement #6 (see docs/current/dataquality/STRUCTURED_DATA_ROUTING.md): every
input format normalizes to markdown as the common substrate. Chandra covers
scanned PDFs; this module covers the remaining text-bearing formats so the
downstream stages (``src/parser.py`` and the markdown-structure repairs) are
format-agnostic:

* ``.txt`` / plain text  -> wrapped as markdown (trivial, no dependency).
* ``.docx`` (Word)       -> markdown via ``pandoc`` (tables/images preserved).
* ``.epub``              -> markdown via ``pandoc``.

Design choices:

* **pandoc, not new Python deps.** The ``pandoc`` binary is available and
  converts both docx and epub to GitHub-Flavored Markdown well, so we avoid
  adding ``mammoth`` / ``ebooklib`` / ``python-docx``. If ``pandoc`` is absent,
  the converters raise :class:`ConverterUnavailable` with a clear message rather
  than silently producing nothing.
* **Same output contract as Chandra.** Output is markdown text (GFM), so
  ``src/parser.py`` and ``markdown_structure`` consume it unchanged.
* **Provenance stays with SourceMetadata.** These functions only transform
  bytes->markdown; the source-of-truth record (author, capture_date, checksum,
  ...) is the caller's :class:`~src.ingestion.source_metadata.SourceMetadata`.

The handler table in STRUCTURED_DATA_ROUTING.md marks docx/epub/txt as
"to build"; this module builds them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# pandoc output format: GitHub-Flavored Markdown, no source line wrapping (keep
# paragraphs on one line so the markdown-structure paragraph splitter works).
_PANDOC_TO = "gfm"
_PANDOC_WRAP = "--wrap=none"
_PANDOC_TIMEOUT = 120  # seconds; generous for a large docx/epub


class ConverterUnavailable(RuntimeError):
    """Raised when a required external converter (pandoc) is not installed."""


class ConverterError(RuntimeError):
    """Raised when conversion runs but fails (bad/corrupt input, pandoc error)."""


def pandoc_available() -> bool:
    """Return True if the ``pandoc`` binary is on PATH."""
    return shutil.which("pandoc") is not None


def txt_to_markdown(path: Path) -> str:
    """Wrap a plain-text file as markdown.

    Plain text is already valid markdown; this normalizes newlines and strips a
    trailing whitespace run so the output matches the contract other converters
    produce. No external dependency.
    """
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    # Normalize line endings; collapse a trailing run of blank lines to one.
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n"


def _run_pandoc(path: Path, from_format: str) -> str:
    """Convert ``path`` from ``from_format`` to markdown via pandoc.

    Args:
        path: Input document path.
        from_format: pandoc source format token (e.g. "docx", "epub").

    Returns:
        Markdown (GFM) text.

    Raises:
        ConverterUnavailable: pandoc is not installed.
        ConverterError: pandoc ran but failed.
    """
    if not pandoc_available():
        raise ConverterUnavailable(
            "pandoc is required to convert "
            f"{from_format} files but was not found on PATH. "
            "Install pandoc (https://pandoc.org/installing.html)."
        )
    path = Path(path)
    if not path.is_file():
        raise ConverterError(f"input file does not exist: {path}")

    cmd = [
        "pandoc",
        "--from",
        from_format,
        "--to",
        _PANDOC_TO,
        _PANDOC_WRAP,
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_PANDOC_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - env dependent
        raise ConverterError(
            f"pandoc timed out after {_PANDOC_TIMEOUT}s converting {path.name}"
        ) from exc

    if proc.returncode != 0:
        raise ConverterError(
            f"pandoc failed ({from_format} -> markdown) on {path.name}: "
            f"{proc.stderr.strip()[:500]}"
        )
    return proc.stdout.rstrip() + "\n"


def docx_to_markdown(path: Path) -> str:
    """Convert a Word ``.docx`` document to markdown (GFM) via pandoc.

    Tables and embedded structure are preserved by pandoc's GFM writer, so the
    downstream table handling and markdown-structure repairs apply uniformly.
    """
    return _run_pandoc(Path(path), "docx")


def epub_to_markdown(path: Path) -> str:
    """Convert an ``.epub`` book to markdown (GFM) via pandoc."""
    return _run_pandoc(Path(path), "epub")


# Dispatch by media type, mirroring the source_format handler table. Callers
# resolve the media type first (media_detection), then convert.
def convert_to_markdown(path: Path, media_type: str) -> str:
    """Convert a text-bearing document to markdown by its media type.

    Args:
        path: Input document path.
        media_type: One of "text", "docx", "epub".

    Raises:
        ValueError: media_type is not a text-producing format handled here.
        ConverterUnavailable / ConverterError: as documented on the converters.
    """
    if media_type == "text":
        return txt_to_markdown(path)
    if media_type == "docx":
        return docx_to_markdown(path)
    if media_type == "epub":
        return epub_to_markdown(path)
    raise ValueError(
        f"convert_to_markdown does not handle media_type={media_type!r}; "
        "expected one of: text, docx, epub"
    )
