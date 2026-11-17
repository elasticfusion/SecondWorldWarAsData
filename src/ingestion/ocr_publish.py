"""From a book's OCR parts to the chapters Phase 1 parses (pure; src/ingestion/ocr_promote does
the I/O).

Steps, in order, each logged:

1. **sanitise** every part (src/ingestion/ocr_sanitise: look-alike letters, superscript
   ordinals) - always, whether or not the document has a validation URL;
2. **split** the book at its own chapter headings (src/ingestion/ocr_chapters);
3. **correct** - only for a document with a validation URL: each chapter cross-checked against
   its transcription and corrected (src/ingestion/ocr_crosscheck, done by the caller on the
   sections); a document without one is never checked against nothing;
4. **normalise** each published chapter into parser form (src/ingestion/chandra_markdown).

The raw OCR (before step 1) is kept by the caller, so every step is reproducible and reversible.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from src.ingestion.chandra_markdown import normalise_chandra_chapter
from src.ingestion.ocr_chapters import Section, paginated_book, split_book
from src.ingestion.ocr_sanitise import SanitiseLog, sanitise_parts, vocabulary

Parts = Sequence[
    Tuple[Optional[int], str]
]  # (first PDF page, Chandra markdown), page order


@dataclass
class Chapter:
    number: int
    title: str
    label: str
    first_page: Optional[int]
    last_page: Optional[int]
    markdown: str
    log: dict


@dataclass
class Publication:
    chapters: List[Chapter] = field(default_factory=list)
    held_back: List[dict] = field(default_factory=list)  # sections not published

    def summary(self) -> dict:
        return {
            "chapters": [c.number for c in self.chapters],
            "held_back": [h["label"] for h in self.held_back],
        }

    def to_dict(self) -> dict:
        keys = ("number", "title", "label", "first_page", "last_page", "log")
        return {
            "summary": self.summary(),
            "chapters": [{k: getattr(c, k) for k in keys} for c in self.chapters],
            "held_back": self.held_back,
        }


def sanitised(parts: Parts) -> Tuple[List[Tuple[Optional[int], str]], SanitiseLog]:
    """Step 1: the parts sanitised against the whole book's vocabulary."""
    texts, log = sanitise_parts(parts)
    return [(first, text) for (first, _), text in zip(parts, texts)], log


def sections_of(parts: Parts) -> List[Section]:
    """Step 2."""
    return split_book(paginated_book(parts))


def book_vocabulary(parts: Parts) -> Counter:
    return vocabulary([text for _, text in parts])


def publication(sections: Sequence[Section], vocab: Counter) -> Publication:
    """Step 4 for the (corrected, where checked) sections."""
    pub = Publication()
    for section in sections:
        if not section.published:
            pub.held_back.append(_held(section))
            continue
        markdown, log = normalise_chandra_chapter(section.text, vocab)
        pub.chapters.append(
            Chapter(
                section.number,
                section.chapter_title if section.label else "",
                section.label,
                section.first_page,
                section.last_page,
                markdown,
                log.to_dict(),
            )
        )
    return pub


def _held(section: Section) -> dict:
    return {
        "label": section.label,
        "kind": section.kind,
        "first_page": section.first_page,
        "last_page": section.last_page,
        "chars": len(section.text),
    }
