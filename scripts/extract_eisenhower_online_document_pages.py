#!/usr/bin/env python3
"""Extract entries from Eisenhower Library WWII online-document landing pages."""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://www.eisenhowerlibrary.gov"
OUTPUT_CSV = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_online_document_pages_wwii.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/eisenhower_online_document_pages_wwii_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TOPIC_PAGES: list[dict[str, str]] = [
    {
        "topic_slug": "ardennes-campaign-battle-bulge",
        "landing_page_url": (
            f"{BASE_URL}/research/online-documents/ardennes-campaign-battle-bulge"
        ),
        "wwii_topic": "Ardennes Campaign (Battle of the Bulge)",
    },
    {
        "topic_slug": "world-war-ii-d-day-invasion-normandy",
        "landing_page_url": (
            f"{BASE_URL}/research/online-documents/world-war-ii-d-day-invasion-normandy"
        ),
        "wwii_topic": "D-Day, The Invasion of Normandy",
    },
    {
        "topic_slug": "world-war-ii-holocaust-extermination-european-jews",
        "landing_page_url": (
            f"{BASE_URL}/research/online-documents/"
            "world-war-ii-holocaust-extermination-european-jews"
        ),
        "wwii_topic": "Holocaust, The Extermination of European Jews",
    },
]

CSV_FIELDS = [
    "source_landing_page_title",
    "source_landing_page_url",
    "topic_slug",
    "wwii_topic",
    "record_category",
    "section",
    "content_group",
    "entry_type",
    "title",
    "description",
    "digital_resource_url",
    "digital_resource_format",
    "thumbnail_url",
    "related_resource_url",
    "collection_name",
    "box",
    "folder_or_item_title",
    "naid",
    "catalog_id",
    "archival_citation",
    "content_warning",
    "is_external_resource",
    "raw_entry_text",
]

BRACKET_RE = re.compile(r"\[([^\]]+)\]")
NAID_RE = re.compile(r"NAID\s*#?\s*([\d,\s]+(?:and\s+[\d,\s]+)?)", re.I)
BOX_RE = re.compile(r"Box(?:es)?\s+(\d+(?:\s*[-–—]\s*\d+)?)", re.I)
BOX_FOLDER_TAIL_RE = re.compile(r"Box(?:es)?\s+\d+(?:\s*[-–—]\s*\d+)?,\s*(.+)$", re.I)
FOLDER_RE = re.compile(r"File\s+Folder[s]?:\s*([^;\]]+)", re.I)
WARNING_RE = re.compile(r"\(WARNING:[^)]+\)", re.I)
CATALOG_RE = re.compile(r"\b(EL-[A-Z]+\d+-\d+)\b")
YOUTUBE_RE = re.compile(r"youtube\.com/embed/([^?&]+)")


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", "replace")


def absolute_url(href: str) -> str:
    if not href or href.startswith("#"):
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return f"{BASE_URL}/{href}"


def resource_format(url: str) -> str:
    if not url:
        return ""
    lower = url.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube_embed"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return "image"
    if lower.startswith("http"):
        return "external_web"
    return "other"


def is_external(url: str) -> bool:
    return bool(url) and "eisenhowerlibrary.gov" not in url


def classify_url(url: str) -> str:
    lower = url.lower()
    if is_external(url):
        if lower.endswith(".pdf"):
            return "external_resource"
        return "external_resource"
    if "/audiovisual/images/" in lower:
        return "photograph"
    if "/oral-histories/" in lower:
        return "oral_history"
    if "/subject-guides/" in lower:
        return "subject_guide"
    if "bibliography" in lower and lower.endswith(".pdf"):
        return "bibliography"
    if "transcript" in lower and lower.endswith(".pdf"):
        return "transcript"
    if lower.endswith(".pdf"):
        return "document_pdf"
    if "youtube.com" in lower:
        return "audiovisual_embed"
    return "digital_resource"


def link_title(text: str, link_text: str, entry_type: str) -> str:
    if link_text and not link_text.lower().startswith("http"):
        return link_text
    if entry_type in {"external_resource", "document_pdf"}:
        without_bracket = BRACKET_RE.sub("", text).strip()
        without_url = re.split(r"https?://", without_bracket)[0].strip().rstrip(".")
        if without_url:
            return without_url
    return link_text or text.split("[")[0].strip()


def record_category(entry_type: str) -> str:
    if entry_type == "narrative":
        return "page_narrative"
    if entry_type in {"subject_guide", "bibliography", "external_resource"}:
        return "recommended_reading"
    return "presidential_library_holding"


def parse_archival_citation(text: str) -> dict[str, str]:
    warning = ""
    warning_match = WARNING_RE.search(text)
    if warning_match:
        warning = warning_match.group(0)

    bracket_match = BRACKET_RE.search(text)
    citation = bracket_match.group(1).strip() if bracket_match else ""
    naid = ""
    box = ""
    folder = ""
    collection = citation

    if citation:
        naid_match = NAID_RE.search(citation)
        if naid_match:
            naid = re.sub(r"\s+and\s+", "; ", naid_match.group(1).strip())
            collection = citation[: naid_match.start()].strip().rstrip(";").strip()
        box_match = BOX_RE.search(citation)
        if box_match:
            box = box_match.group(1).strip()
        folder_match = FOLDER_RE.search(citation)
        if folder_match:
            folder = folder_match.group(1).strip()
        elif box_match:
            tail_match = BOX_FOLDER_TAIL_RE.search(citation)
            if tail_match:
                folder = tail_match.group(1).strip()

    catalog_match = CATALOG_RE.search(text)
    catalog_id = catalog_match.group(1) if catalog_match else ""

    return {
        "archival_citation": citation,
        "collection_name": collection,
        "box": box,
        "folder_or_item_title": folder,
        "naid": naid,
        "catalog_id": catalog_id,
        "content_warning": warning,
    }


def make_row(
    *,
    page_title: str,
    page_meta: dict[str, str],
    section: str,
    content_group: str,
    entry_type: str,
    title: str,
    description: str = "",
    digital_resource_url: str = "",
    thumbnail_url: str = "",
    related_resource_url: str = "",
    collection_name: str = "",
    box: str = "",
    folder_or_item_title: str = "",
    naid: str = "",
    catalog_id: str = "",
    archival_citation: str = "",
    content_warning: str = "",
    raw_entry_text: str = "",
) -> dict[str, str]:
    url = digital_resource_url or related_resource_url
    return {
        "source_landing_page_title": page_title,
        "source_landing_page_url": page_meta["landing_page_url"],
        "topic_slug": page_meta["topic_slug"],
        "wwii_topic": page_meta["wwii_topic"],
        "record_category": record_category(entry_type),
        "section": section,
        "content_group": content_group,
        "entry_type": entry_type,
        "title": title,
        "description": description,
        "digital_resource_url": digital_resource_url,
        "digital_resource_format": resource_format(digital_resource_url),
        "thumbnail_url": thumbnail_url,
        "related_resource_url": related_resource_url,
        "collection_name": collection_name,
        "box": box,
        "folder_or_item_title": folder_or_item_title,
        "naid": naid,
        "catalog_id": catalog_id,
        "archival_citation": archival_citation,
        "content_warning": content_warning,
        "is_external_resource": "yes" if is_external(url) else "no",
        "raw_entry_text": raw_entry_text,
    }


def should_skip_element(element: Tag, body: Tag) -> bool:
    if element.name != "p":
        return False
    parent = element.parent
    if parent and parent.name == "figcaption":
        return True
    ancestor = element.parent
    while ancestor and ancestor is not body:
        if is_photo_gallery_section(ancestor):
            return True
        ancestor = ancestor.parent
    return False


def is_photo_gallery_section(element: Tag) -> bool:
    if element.name != "section":
        return False
    return bool(element.find("a", href=True) and element.find("img"))


def parse_landing_page(page_meta: dict[str, str]) -> list[dict[str, str]]:
    html = fetch_html(page_meta["landing_page_url"])
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.select_one("h1")
    page_title_text = page_title.get_text(" ", strip=True) if page_title else ""
    body = soup.select_one(".field--name-body")
    if body is None:
        raise RuntimeError(f"No body content found for {page_meta['landing_page_url']}")

    entries: list[dict[str, str]] = []
    section = "introduction"
    content_group = ""
    seen_photo_urls: set[str] = set()

    def add_entry(**kwargs: str) -> None:
        entries.append(make_row(page_title=page_title_text, page_meta=page_meta, **kwargs))

    for element in body.find_all(["h3", "p", "figure", "section"]):
        if should_skip_element(element, body):
            continue

        if element.name == "h3":
            section = element.get_text(" ", strip=True).rstrip(":")
            continue

        if element.name == "section":
            if not is_photo_gallery_section(element):
                continue
            for link in element.find_all("a", href=True):
                if not link.find("img"):
                    continue
                href = absolute_url(link["href"])
                if href in seen_photo_urls:
                    continue
                seen_photo_urls.add(href)
                img = link.find("img")
                thumb = absolute_url(img.get("src", "")) if img else ""
                title = link.get("title") or link.get_text(" ", strip=True)
                add_entry(
                    section=section,
                    content_group=content_group,
                    entry_type="photograph",
                    title=title,
                    digital_resource_url=href,
                    thumbnail_url=thumb,
                    raw_entry_text=title,
                )
            continue

        if element.name == "figure":
            iframe = element.find("iframe", src=True)
            if not iframe:
                continue
            embed_url = absolute_url(iframe["src"])
            caption = element.find("figcaption")
            caption_text = caption.get_text(" ", strip=True) if caption else ""
            title = iframe.get("title") or caption_text[:120]
            meta = parse_archival_citation(caption_text)
            add_entry(
                section=section,
                content_group=content_group,
                entry_type="audiovisual_embed",
                title=title,
                description=caption_text,
                digital_resource_url=embed_url,
                catalog_id=meta["catalog_id"],
                raw_entry_text=caption_text or title,
            )
            continue

        text = element.get_text(" ", strip=True)
        if not text:
            continue

        strong = element.find("strong")
        links = element.find_all("a", href=True)

        if strong and not links:
            label = strong.get_text(" ", strip=True).rstrip(":")
            remainder = text[len(strong.get_text(" ", strip=True)) :].strip(" :")
            content_group = label
            if remainder:
                add_entry(
                    section=section,
                    content_group=content_group,
                    entry_type="narrative",
                    title=label,
                    description=remainder,
                    raw_entry_text=text,
                )
            continue

        if not links:
            add_entry(
                section=section,
                content_group=content_group,
                entry_type="narrative",
                title=text[:120],
                description=text,
                raw_entry_text=text,
            )
            continue

        meta = parse_archival_citation(text)
        primary_href = ""
        transcript_href = ""
        for link in links:
            href = absolute_url(link["href"])
            entry_type = classify_url(href)
            if entry_type == "transcript" and not transcript_href:
                transcript_href = href
            elif not primary_href:
                primary_href = href

        for index, link in enumerate(links):
            href = absolute_url(link["href"])
            link_text = link.get_text(" ", strip=True)
            entry_type = classify_url(href)
            title = link_title(text, link_text, entry_type)
            if entry_type == "transcript":
                title = f"Transcript: {title}" if title else "Transcript"

            description = ""
            if entry_type == "external_resource":
                description = text.replace(link_text, "").strip()

            add_entry(
                section=section,
                content_group=content_group,
                entry_type=entry_type,
                title=title,
                description=description,
                digital_resource_url=href if entry_type != "transcript" else primary_href,
                related_resource_url=href if entry_type == "transcript" else transcript_href,
                collection_name=meta["collection_name"],
                box=meta["box"],
                folder_or_item_title=meta["folder_or_item_title"],
                naid=meta["naid"],
                catalog_id=meta["catalog_id"],
                archival_citation=meta["archival_citation"],
                content_warning=meta["content_warning"],
                raw_entry_text=text if index == 0 else f"{link_text} {href}",
            )

    return entries


def write_csv(entries: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def main() -> None:
    all_entries: list[dict[str, str]] = []
    manifest_pages: list[dict[str, object]] = []

    for page_meta in TOPIC_PAGES:
        entries = parse_landing_page(page_meta)
        all_entries.extend(entries)
        by_type: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for entry in entries:
            by_type[entry["entry_type"]] = by_type.get(entry["entry_type"], 0) + 1
            by_category[entry["record_category"]] = (
                by_category.get(entry["record_category"], 0) + 1
            )
        manifest_pages.append(
            {
                "topic_slug": page_meta["topic_slug"],
                "landing_page_url": page_meta["landing_page_url"],
                "wwii_topic": page_meta["wwii_topic"],
                "entry_count": len(entries),
                "entries_by_type": by_type,
                "entries_by_category": by_category,
            }
        )
        print(
            f"{page_meta['topic_slug']}: {len(entries)} entries "
            f"({by_type})"
        )

    write_csv(all_entries)
    manifest = {
        "source_pages": [page["landing_page_url"] for page in TOPIC_PAGES],
        "output_csv": str(OUTPUT_CSV.relative_to(PROJECT_ROOT)),
        "pages": manifest_pages,
        "total_entries": len(all_entries),
        "entries_by_category": {
            category: sum(1 for entry in all_entries if entry["record_category"] == category)
            for category in sorted({entry["record_category"] for entry in all_entries})
        },
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_entries)} entries to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()