"""Per-document language detection + Grok translation to English (Phase 0).

Normalizes non-English source documents to English **at ingest**, so the
English-centric Phase 1–3 stack (prompts + `text_utils` name matching) runs
unchanged. Strategy and rationale:
docs/current/dataquality/LANGUAGE_TRANSLATION.md.

Flow per document (operating on the original-language Markdown that the Phase 0
converters produce):

1. :func:`detect_language` — a deterministic check first (:func:`clearly_english`: English
   function words dominate, no other language's do, no non-Latin script), so an English page
   costs nothing; only a page that is not clearly English asks Grok for its language.
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
# ... of which Grok is sent this much when the deterministic check cannot decide
_GROK_DETECT_CHARS = 1500

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


# Function words of English and of the languages a WWII source is most often in: a page is
# CLEARLY English when English's dominate and no other language's are common. Words two
# languages share ("in", "die", "a") are left out.
_FUNCTION_WORDS = {
    "English": {"the", "and", "of", "to", "was", "were", "with", "which", "that", "had",
                "from", "by", "on", "at", "for", "this", "their", "they", "been", "would"},
    "German": {"der", "und", "das", "nicht", "mit", "dem", "den", "ist", "auf", "sich", "wurde",
               "eine", "einer", "auch", "nach", "zu", "von", "bei", "wir", "sie"},
    "French": {"le", "les", "et", "des", "du", "une", "dans", "est", "pour", "qui", "sur",
               "au", "aux", "pas", "par", "ont", "été", "avec", "nous", "sont"},
    "Italian": {"il", "della", "che", "di", "del", "per", "una", "sono", "nel", "dei",
                "alla", "gli", "delle", "anche", "più", "non", "si", "ha", "dalla", "questo"},
    "Spanish": {"el", "los", "las", "del", "que", "por", "una", "con", "para", "se", "su",
                "fue", "como", "más", "pero", "sus", "este", "entre", "cuando", "muy"},
    "Dutch": {"het", "een", "van", "en", "niet", "zijn", "voor", "dat", "met", "wordt", "werd",
              "ook", "bij", "naar", "uit", "worden", "deze", "wij", "hij", "zij"},
    "Polish": {"w", "nie", "na", "się", "jest", "z", "do", "że", "jak", "dla", "przez",
               "był", "była", "oraz", "jego", "ale", "od", "po", "tak", "który"},
    "Czech": {"je", "se", "na", "že", "jako", "byl", "byla", "jsou", "pro", "jeho", "ale",
              "nebo", "který", "která", "také", "podle", "při", "až", "už", "jen"},
    "Portuguese": {"os", "as", "do", "da", "dos", "das", "em", "um", "uma", "não", "foi", "com",
                   "ao", "pelo", "pela", "seu", "sua", "mas", "como", "são"},
    "Scandinavian": {"og", "er", "ikke", "det", "som", "med", "har", "til", "av", "på", "var",
                     "fra", "jeg", "vi", "och", "inte", "att", "är", "den", "kan"},
}  # fmt: skip
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_LATIN_LETTER = re.compile(r"[A-Za-z\u00C0-\u024F]")
# a word with a letter English does not use (ä ö ü ß é è à ł ą ę ě ř æ ø å ç ñ ...): an English
# history prints some (Saint-Lô, la Fière), a foreign passage many
_ACCENTED = re.compile(r"[\u00C0-\u024F]")
_MAX_ACCENTED_SHARE = 0.02
# markup is not language: HTML tags (tables), image / link targets, code fences (charts)
_MARKUP = re.compile(r"<[^>]*>|!?\[[^\]]*\]\([^)]*\)|```.*?```", re.DOTALL)
_MIN_JUDGED_WORDS = 25  # below it, the share of function words says little
_MIN_ENGLISH_SHARE = (
    0.15  # English function words / all words; ordinary English prose ~0.3
)
_MAX_OTHER_SHARE = 0.02  # another language's function words above this: ask Grok
_LIST_ENGLISH_SHARE = 0.03  # a table or list: few function words, but only English ones
_LIST_OTHER_SHARE = 0.005


def clearly_english(text: str) -> bool:
    """True when ``text`` (markup removed) is English beyond doubt, deterministically: Latin
    script; English function words present, NO other language's common and few accented words.
    A short text needs two distinct English function words and nothing foreign. False means "ask Grok" -
    never "not English": a page of a mixed-language book that quotes a German order, or a
    German page, goes to Grok."""
    words = [w.lower() for w in _WORD.findall(_MARKUP.sub(" ", text or ""))]
    if not _latin("".join(words)):
        return False  # Cyrillic, Greek, CJK...: not English (Grok names the language)
    return _english_words(words) if words else True  # no text: nothing to translate


def _english_words(words: List[str]) -> bool:
    """``clearly_english`` for a text's (Latin-script) words."""
    english, other = _function_word_shares(words)
    accented = sum(1 for w in words if _ACCENTED.search(w)) / len(words)
    if len(words) < _MIN_JUDGED_WORDS:
        return _short_english(words, other, accented)
    prose = english >= _MIN_ENGLISH_SHARE and other <= _MAX_OTHER_SHARE
    listing = english >= _LIST_ENGLISH_SHARE and other <= _LIST_OTHER_SHARE
    return accented <= _MAX_ACCENTED_SHARE and (prose or listing)


def _short_english(words: List[str], other: float, accented: float) -> bool:
    """A few words prove little: two distinct English function words, nothing foreign."""
    distinct = {w for w in words if w in _FUNCTION_WORDS["English"]}
    return len(distinct) >= 2 and other == 0 and accented == 0


def _latin(letters: str) -> bool:
    """At least 90% of the letters are Latin script (an empty string is)."""
    return not letters or len(_LATIN_LETTER.findall(letters)) >= 0.9 * len(letters)


def _function_word_shares(words: List[str]) -> "tuple[float, float]":
    """(share of English function words, the highest share of another language's)."""
    share = {
        lang: sum(1 for w in words if w in vocab) / len(words)
        for lang, vocab in _FUNCTION_WORDS.items()
    }
    english = share.pop("English")
    return english, max(share.values())


def detect_language(markdown: str, grok, *, use_cache: bool = True) -> str:
    """The primary language of a Markdown document: "English" at no cost when it is
    :func:`clearly_english`, else Grok's answer.

    Returns the English name of the language (e.g. "German"). Falls back to
    "English" on an empty document or an unusable response, so a detection
    hiccup never diverts an English-looking doc into translation.
    """
    sample = markdown.strip()[:_DETECT_SAMPLE_CHARS]
    if not sample or clearly_english(sample):
        return "English"
    # Grok names a language from a short sample; the whole block is not needed
    prompt = render_prompt("language_detect", sample=sample[:_GROK_DETECT_CHARS])
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


def _language_runs(
    markdown: str, grok, use_cache: bool
) -> "tuple[List[str], List[str]]":
    """(sections, their languages): the document's blocks in detection-sized sections (each at
    most ``_DETECT_SAMPLE_CHARS``, so every character is looked at), neighbouring sections of
    one language merged - a translation request is one run of one language."""
    blocks = split_markdown_chunks(markdown, max_chars=_DETECT_SAMPLE_CHARS) or [
        markdown
    ]
    sections: List[str] = []
    languages: List[str] = []
    for block in blocks:
        lang = detect_language(block, grok, use_cache=use_cache)
        lang = "English" if is_english(lang) else lang
        if languages and languages[-1] == lang:
            sections[-1] += "\n\n" + block
        else:
            sections.append(block)
            languages.append(lang)
    return sections, languages


def normalize_to_english(
    markdown: str,
    grok,
    *,
    model_name: Optional[str] = None,
    use_cache: bool = True,
) -> TranslationResult:
    """Detect the language of each SECTION (``_language_runs``) and translate the
    non-English ones to English - a book may mix languages (an English history quoting a German
    order, a German document with an English cover sheet): one language for the whole document
    from its first page would leave the rest untranslated. English sections pass through
    untouched (no translation call; clearly English ones no detection call either); each
    translated section is marked (:func:`translation_marker`); the original is kept whole.
    """
    sections, languages = _language_runs(markdown, grok, use_cache)
    foreign = [lang for lang in languages if not is_english(lang)]
    if not foreign:
        logger.info("Document detected as English — no translation")
        return TranslationResult(
            source_language="English",
            translated=False,
            english_markdown=markdown,
        )
    tag = f"grok:{model_name}" if model_name else "grok"
    out = []
    for sec, lang in zip(sections, languages):
        if is_english(lang):
            out.append(sec)
            continue
        logger.info("Section detected as %s — translating to English", lang)
        out.append(
            translation_marker(lang, tag)
            + "\n\n"
            + translate_markdown(sec, lang, grok, use_cache=use_cache)
        )
    return TranslationResult(
        source_language=", ".join(dict.fromkeys(foreign)),
        translated=True,
        english_markdown="\n\n".join(out),
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


# --- Shared per-track adapter -------------------------------------------------
#
# Each Phase-0 track (convert/OCR-narrative/video) produces original-language
# markdown and must normalize it to English BEFORE promote-to-parse, so the
# English-centric Phase 1-3 stack never sees a non-English document. This adapter
# is the single reuse point: config-gated, builds its own Grok client, picks
# per-document vs per-page, and is FAIL-SAFE — disabled, English, or any error
# returns the markdown unchanged (never block/garble a doc on a translation hiccup).


# --- Observability (fail-safe; never raises into the phase) ------------------
# Canonical Phase-0 OBSERV: one greppable line per normalization decision.
# Anti-swallow rule — the fail-safe 'unchanged on error' CONTRACT is preserved
# (we still return the original markdown), but the error is LOUD, never silent.
def _observe_translation(lang: str, action: str, *, reason: str = "") -> None:
    """Emit ``OBSERV translation.normalized lang=<> action=<>``.

    ``translated``, ``unchanged`` (legitimately already-English), and
    ``disabled`` (a configured choice) are info; ``error`` and any unexpected
    action are LOUD. Fail-safe: never raises into the phase.
    """
    try:
        base = "OBSERV translation.normalized lang=%s action=%s" % (lang, action)
        if reason:
            base += " reason=%s" % (str(reason)[:200],)
        if action in ("translated", "unchanged", "disabled"):
            logger.info(base)
        else:
            logger.warning(base)
    except (
        Exception
    ):  # noqa: BLE001 - observability never breaks the phase  # nosec B110 - benign fail-safe (observability/cleanup; loud OBSERV elsewhere)
        pass


def translation_enabled(config: Optional[dict] = None) -> bool:
    """True unless Phase-0 translation is switched OFF (``ingestion.translate: false`` or
    ``PHASE0_TRANSLATE=0``): a non-English document is translated to English by default (the
    Phase 1-3 stack is English-centric), an English one costs nothing (:func:`clearly_english`).
    The ONE switch for every track (convert, OCR, video, OOB)."""
    import os

    env = os.environ.get("PHASE0_TRANSLATE")
    if env in ("0", "1"):
        return env == "1"
    if config is None:
        try:
            from src.utils.config import load_config

            config = load_config()
        except Exception:  # noqa: BLE001 - no config: the default
            return True
    return bool((config or {}).get("ingestion", {}).get("translate", True))


def normalize_markdown_to_english(
    markdown: str,
    *,
    per_page: bool = False,
    grok=None,
    config: Optional[dict] = None,
) -> str:
    """Normalize produced markdown to English for ANY track. Returns the English
    markdown (== input when disabled / already English / on any error).

    per_page=True uses physical-page detection (Chandra multi-page OCR); False
    uses whole-document detection (a single converted/transcript document).
    English pages/docs pass through untouched; translated output carries the
    inert provenance marker. Never raises.
    """
    if not markdown or not markdown.strip():
        return markdown
    if not translation_enabled(config):
        _observe_translation("unknown", "disabled", reason="translation not enabled")
        return markdown
    try:
        if grok is None:
            from src.grok_client import GrokClient
            from src.utils.config import load_config, get_paths

            grok = GrokClient(get_paths(load_config())["api_cache"])
        model_name = None
        try:
            model_name = (config or {}).get("api", {}).get("grok", {}).get("model")
        except (
            Exception
        ):  # noqa: BLE001  # nosec B110 - benign fail-safe (observability/cleanup; loud OBSERV elsewhere)
            pass
        if per_page:
            return _normalize_per_page(markdown, grok, model_name)
        return _normalize_whole_doc(markdown, grok, model_name)
    except Exception as e:  # noqa: BLE001 - fail-safe: keep original on any error
        logger.warning("Translation normalization skipped (keeping original): %s", e)
        # CONTRACT preserved (return original) but the error is LOUD, never silent.
        _observe_translation("unknown", "error", reason=str(e))
        return markdown


def _normalize_per_page(markdown: str, grok, model_name) -> str:
    """Per-page (Chandra multi-page OCR) normalization branch. Preserves log + return exactly."""
    page_res = normalize_pages_to_english(markdown, 1, grok, model_name=model_name)
    if page_res.translated:
        langs = ", ".join(page_res.source_languages)
        logger.info(
            "Translated non-English markdown (%s) to English",
            langs,
        )
        _observe_translation(langs, "translated")
    else:
        _observe_translation("English", "unchanged")
    return page_res.english_markdown


def _normalize_whole_doc(markdown: str, grok, model_name) -> str:
    """Whole-document normalization branch. Preserves log + return exactly."""
    doc_res = normalize_to_english(markdown, grok, model_name=model_name)
    if doc_res.translated:
        logger.info("Translated %s markdown to English", doc_res.source_language)
        _observe_translation(doc_res.source_language, "translated")
    else:
        _observe_translation(doc_res.source_language, "unchanged")
    return doc_res.english_markdown
