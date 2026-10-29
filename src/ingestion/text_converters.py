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
        # --sandbox: run the reader in pandoc's sandbox so a crafted/attacker-
        # controlled input document cannot read local files or make network
        # requests during conversion (mitigates the pandoc SSRF/local-file-read
        # CVE class). Does not affect markdown output for normal docs.
        "--sandbox",
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


# Non-content HTML elements whose text is page chrome (navigation, scripts, styling,
# sidebars) — stripped before conversion so web-page boilerplate never pollutes the corpus.
_HTML_BOILERPLATE_TAGS = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
)


def _strip_html_boilerplate(html: str) -> str:
    """Remove non-content chrome (scripts/styles/nav/header/footer/aside/...) from an HTML
    document, returning the cleaned HTML. Fail-safe: if BeautifulSoup is unavailable or parsing
    fails, return the original HTML unchanged (pandoc still converts it, just with more chrome).
    """
    try:
        from bs4 import BeautifulSoup  # pinned dependency (beautifulsoup4)

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(list(_HTML_BOILERPLATE_TAGS)):
            tag.decompose()
        # Prefer the main content region when the page marks one; else the whole (cleaned) doc.
        main = soup.find("main") or soup.find("article") or soup
        return str(main)
    except Exception as e:  # noqa: BLE001 - never let cleaning block conversion
        # DEGRADED FALLBACK: we convert the raw (chrome-laden) HTML instead of a
        # cleaned document. Session anti-swallow rule -> LOUD, never debug.
        logger.warning(
            "OBSERV convert.text kind=html status=degraded "
            "action=raw_html_fallback reason=%s",
            str(e)[:200],
        )
        return html


def html_to_markdown(path: Path) -> str:
    """Convert an ``.html`` document to markdown (GFM).

    Robustness: web pages carry chrome (nav/scripts/sidebars) that would otherwise leak into the
    corpus as noise. We first strip boilerplate with BeautifulSoup, then convert the cleaned HTML
    to GFM via pandoc (``--sandbox`` hardened, tables/headings/lists preserved — far better than a
    raw markdown pass-through, which the old ingest path did by routing .html straight to the
    parser). Fail-safe: a cleaning error falls back to converting the raw HTML.

    Raises ConverterUnavailable/ConverterError consistent with the other converters.
    """
    path = Path(path)
    if not path.is_file():
        raise ConverterError(f"input file does not exist: {path}")
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = _strip_html_boilerplate(raw)
    return _run_pandoc_text(cleaned, "html")


def _run_pandoc_text(text: str, from_format: str) -> str:
    """Convert an in-memory string from ``from_format`` to markdown via pandoc (stdin).

    Mirrors _run_pandoc (same --sandbox hardening, GFM, --wrap=none, timeout) but feeds content on
    stdin so a cleaned/transformed document need not be re-written to disk first."""
    if not pandoc_available():
        raise ConverterUnavailable(
            f"pandoc is required to convert {from_format} but was not found on PATH. "
            "Install pandoc (https://pandoc.org/installing.html)."
        )
    cmd = [
        "pandoc",
        "--sandbox",
        "--from",
        from_format,
        "--to",
        _PANDOC_TO,
        _PANDOC_WRAP,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=text,
            capture_output=True,
            text=True,
            timeout=_PANDOC_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - env dependent
        raise ConverterError(
            f"pandoc timed out after {_PANDOC_TIMEOUT}s converting {from_format}"
        ) from exc
    if proc.returncode != 0:
        raise ConverterError(
            f"pandoc failed ({from_format} -> markdown): {proc.stderr.strip()[:500]}"
        )
    return proc.stdout.rstrip() + "\n"


# --- Observability (fail-safe; never raises into the conversion path) --------
# Canonical Phase-0 OBSERV: one greppable line per conversion decision. The
# session anti-swallow rule applies — an EMPTY output when input was present, or
# any converter failure, is a LOUD warning, never a silent/debug/count=0-as-ok.
_OBSERV_EMPTY_INPUT_CHARS = 1  # input had at least this many chars but output empty


def _observe_convert_text(
    path: Path, kind: str, chars: int, status: str, *, reason: str = ""
) -> None:
    """Emit ``OBSERV convert.text kind=<> chars=<> status=ok|empty|error``.

    LOUD (warning) when status is ``empty`` (input present, no output) or
    ``error`` (converter unavailable/failed); info only on a genuine ``ok``.
    Fail-safe: observability never raises into the convert path.
    """
    try:
        base = "OBSERV convert.text kind=%s chars=%d status=%s" % (kind, chars, status)
        if reason:
            base += " reason=%s" % (str(reason)[:200],)
        if status == "ok":
            logger.info(base)
        else:
            logger.warning(base)
    except Exception:  # noqa: BLE001 - observability never breaks conversion
        pass


def _input_char_len(path: Path) -> int:
    """Best-effort source size (chars) to decide if empty output is an anomaly."""
    try:
        return len(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - size probe must not break conversion
        try:
            return int(Path(path).stat().st_size)
        except Exception:  # noqa: BLE001
            return 0


def _dispatch_convert(path: Path, media_type: str) -> str:
    """Pure dispatch by media type (no observability). See convert_to_markdown."""
    if media_type == "text":
        return txt_to_markdown(path)
    if media_type == "docx":
        return docx_to_markdown(path)
    if media_type == "epub":
        return epub_to_markdown(path)
    if media_type == "html":
        return html_to_markdown(path)
    raise ValueError(
        f"convert_to_markdown does not handle media_type={media_type!r}; "
        "expected one of: text, docx, epub, html"
    )


# Dispatch by media type, mirroring the source_format handler table. Callers
# resolve the media type first (media_detection), then convert.
def convert_to_markdown(path: Path, media_type: str) -> str:
    """Convert a text-bearing document to markdown by its media type.

    Args:
        path: Input document path.
        media_type: One of "text", "docx", "epub", "html".

    Raises:
        ValueError: media_type is not a text-producing format handled here.
        ConverterUnavailable / ConverterError: as documented on the converters.
    """
    try:
        markdown = _dispatch_convert(path, media_type)
    except (ConverterUnavailable, ConverterError) as exc:
        # DEGRADED/FAILURE path — LOUD with the reason, then re-raise so the
        # caller can off-ramp (md_correction / review). Never swallowed.
        _observe_convert_text(path, media_type, 0, "error", reason=str(exc))
        raise
    chars = len(markdown.strip())
    if chars == 0 and _input_char_len(path) >= _OBSERV_EMPTY_INPUT_CHARS:
        # Recognized input produced nothing — anomaly, not a neutral count=0.
        _observe_convert_text(path, media_type, 0, "empty")
    else:
        _observe_convert_text(path, media_type, chars, "ok")
    return markdown
