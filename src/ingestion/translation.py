"""Per-document language detection + Grok translation to English (Phase 0).

Normalizes non-English source documents to English **at ingest**, so the
English-centric Phase 1–3 stack (prompts + `text_utils` name matching) runs
unchanged. Strategy and rationale:
docs/current/dataquality/LANGUAGE_TRANSLATION.md.

Flow per document (operating on the original-language Markdown that the Phase 0
converters produce):

1. :func:`detect_language` — ask Grok for the document's primary language.
2. If English → **no-op** (no Grok translation call, no ``.orig.md``): the
   markdown passes straight through. This is the common, zero-cost case.
3. Else :func:`translate_markdown` — translate to English via Grok, chunked on
   Markdown boundaries so structure (headings, tables, image refs) survives, and
   proper nouns are rendered in conventional English forms.

The heavy work is delegated to an injected :class:`GrokClient`, so the chunking
and orchestration here are unit-testable with a fake client and no network.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from src.utils.prompt_loader import get_system_prompt, render_prompt

logger = logging.getLogger(__name__)

# Characters of leading text used for language detection — enough to be
# decisive, small enough to keep the detect call cheap.
_DETECT_SAMPLE_CHARS = 4000

# Soft cap on characters per translation chunk. Long documents are split on
# Markdown boundaries below this size so each Grok call stays well within
# limits while never cutting mid-structure.
_TRANSLATE_CHUNK_CHARS = 12000

_ENGLISH_NAMES = {"english", "en", "eng"}


@dataclass
class TranslationResult:
    """Outcome of normalizing one document to English.

    Attributes:
        source_language: Detected primary language (English name, e.g. "German").
        translated: True when a Grok translation was applied.
        english_markdown: The English text that feeds Phase 1 (== input when the
            source was already English).
        original_markdown: The original-language markdown (kept for provenance)
            when translated; ``None`` when the source was already English.
        translator: Provenance tag, e.g. "grok:grok-4" (``None`` when not
            translated).
    """

    source_language: str
    translated: bool
    english_markdown: str
    original_markdown: Optional[str] = None
    translator: Optional[str] = None


def is_english(language: str) -> bool:
    """Return True if a detected language name denotes English."""
    return language.strip().lower() in _ENGLISH_NAMES


def detect_language(markdown: str, grok, *, use_cache: bool = True) -> str:
    """Detect the primary language of a Markdown document via Grok.

    Returns the English name of the language (e.g. "German"). Falls back to
    "English" on an empty document or an unusable response, so a detection
    hiccup never diverts an English-looking doc into translation.
    """
    sample = markdown.strip()[:_DETECT_SAMPLE_CHARS]
    if not sample:
        return "English"
    prompt = render_prompt("language_detect", sample=sample)
    system = get_system_prompt("language_detect")
    resp = grok.chat_completion(
        prompt,
        system_prompt=system,
        temperature=0.0,
        use_cache=use_cache,
        cache_type="translation",
    )
    # The prompt asks for one word; take the first alphabetic token defensively.
    match = re.search(r"[A-Za-z]+", resp or "")
    return match.group(0).capitalize() if match else "English"


def split_markdown_chunks(
    markdown: str, max_chars: int = _TRANSLATE_CHUNK_CHARS
) -> List[str]:
    """Split Markdown into translation chunks without breaking structure.

    Splits on blank-line block boundaries and accumulates blocks up to
    ``max_chars``. A single block larger than ``max_chars`` (e.g. a big table)
    is kept whole as its own chunk rather than cut mid-structure — better to
    send one oversized-but-intact block than to sever a table.
    """
    blocks = re.split(r"\n\s*\n", markdown)
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for block in blocks:
        block_len = len(block) + 2  # account for the blank-line separator
        if current and size + block_len > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += block_len
    if current:
        chunks.append("\n\n".join(current))
    return [c for c in chunks if c.strip()]


def translate_markdown(
    markdown: str, source_language: str, grok, *, use_cache: bool = True
) -> str:
    """Translate original-language Markdown to English via Grok, chunked.

    Each chunk is translated independently (structure-preserving prompt) and
    rejoined with blank lines. Proper nouns are rendered in conventional English
    forms per the prompt.
    """
    system = get_system_prompt("translation")
    out: List[str] = []
    chunks = split_markdown_chunks(markdown)
    for i, chunk in enumerate(chunks):
        prompt = render_prompt(
            "translation", source_language=source_language, markdown=chunk
        )
        logger.info(
            "Translating chunk %d/%d (%d chars) from %s",
            i + 1,
            len(chunks),
            len(chunk),
            source_language,
        )
        translated = grok.chat_completion(
            prompt,
            system_prompt=system,
            temperature=0.1,
            use_cache=use_cache,
            cache_type="translation",
        )
        out.append((translated or "").strip())
    return "\n\n".join(out)


def normalize_to_english(
    markdown: str,
    grok,
    *,
    model_name: Optional[str] = None,
    use_cache: bool = True,
) -> TranslationResult:
    """Detect language and, if non-English, translate the document to English.

    English documents pass through untouched (no translation call). Non-English
    documents are translated; the original is returned alongside for provenance.
    """
    language = detect_language(markdown, grok, use_cache=use_cache)
    if is_english(language):
        logger.info("Document detected as English — no translation")
        return TranslationResult(
            source_language="English",
            translated=False,
            english_markdown=markdown,
        )
    logger.info("Document detected as %s — translating to English", language)
    english = translate_markdown(markdown, language, grok, use_cache=use_cache)
    tag = f"grok:{model_name}" if model_name else "grok"
    return TranslationResult(
        source_language=language,
        translated=True,
        english_markdown=english,
        original_markdown=markdown,
        translator=tag,
    )


# --- Per-page normalization (the unit for mixed-language rolls) ---------------
#
# A microfilm roll / multi-page scan is a container of many small documents in
# possibly-different languages (e.g. an English cover sheet + a German
# interrogation transcript). Detecting/translating per PAGE handles that
# correctly, where per-document would mis-detect the minority-language pages.
# Pages are the physical pages Chandra emits with ``--paginate_output`` (see
# src/ingestion/chunk_pages.split_pages).


@dataclass
class PageTranslation:
    """Per-page language outcome within a multi-page markdown document."""

    page: int
    source_language: str
    translated: bool


@dataclass
class PageNormalizationResult:
    """Outcome of per-page normalization of one multi-page markdown document.

    Attributes:
        english_markdown: Reassembled English markdown (feeds Phase 1). Each
            translated page is prefixed with an inert translation-provenance
            HTML comment (see :func:`translation_marker`); English pages are
            unchanged.
        original_markdown: The original (pre-translation) markdown, kept for
            provenance when ANY page was translated; ``None`` if all pages were
            already English.
        pages: Per-page language outcomes.
        translator: Provenance tag (``None`` when nothing was translated).
    """

    english_markdown: str
    original_markdown: Optional[str]
    pages: List[PageTranslation]
    translator: Optional[str] = None

    @property
    def translated(self) -> bool:
        """True when at least one page was translated."""
        return any(p.translated for p in self.pages)

    @property
    def source_languages(self) -> List[str]:
        """Distinct source languages seen across pages, in first-seen order."""
        seen: List[str] = []
        for p in self.pages:
            if p.source_language not in seen:
                seen.append(p.source_language)
        return seen


def translation_marker(language: str, translator: str) -> str:
    """Return the inert per-page translation-provenance marker.

    An HTML comment: visible to a human reading the markdown and parseable by
    tooling, but inert to ``src/parser.py`` and entity extraction (which ignore
    HTML comments — same mechanism as Chandra's ``<!-- Page N -->`` markers), so
    provenance is preserved without contaminating extracted entities.
    """
    return f"<!-- translated from {language} by {translator} -->"


def normalize_pages_to_english(  # pylint: disable=too-many-locals
    markdown: str,
    chunk_start_page: int,
    grok,
    *,
    model_name: Optional[str] = None,
    use_cache: bool = True,
) -> PageNormalizationResult:
    """Detect + translate a multi-page markdown document **per physical page**.

    Splits ``markdown`` into physical pages (``--paginate_output`` separators via
    :func:`chunk_pages.split_pages`), then for each page: detect language;
    English → pass through untouched; non-English → translate and prefix the
    inert :func:`translation_marker`. Pages are reassembled in order. The
    pre-translation markdown is retained as ``original_markdown`` whenever any
    page was translated.
    """
    # Local import to avoid a module-load cycle (chunk_pages imports table
    # detection, which is heavier than this module needs at import time).
    from src.ingestion.chunk_pages import (  # pylint: disable=import-outside-toplevel
        split_pages,
    )

    tag = f"grok:{model_name}" if model_name else "grok"
    pages = split_pages(markdown, chunk_start_page)
    out_pages: List[str] = []
    outcomes: List[PageTranslation] = []
    any_translated = False
    for physical_page, page_md in pages:
        language = detect_language(page_md, grok, use_cache=use_cache)
        if is_english(language):
            out_pages.append(page_md)
            outcomes.append(PageTranslation(physical_page, "English", False))
            continue
        english = translate_markdown(page_md, language, grok, use_cache=use_cache)
        marked = translation_marker(language, tag) + "\n\n" + english
        out_pages.append(marked)
        outcomes.append(PageTranslation(physical_page, language, True))
        any_translated = True
    return PageNormalizationResult(
        english_markdown="\n\n".join(out_pages),
        original_markdown=markdown if any_translated else None,
        pages=outcomes,
        translator=tag if any_translated else None,
    )
