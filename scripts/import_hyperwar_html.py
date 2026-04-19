#!/usr/bin/env python3
"""
Import HyperWar HTML content into the pipeline content repository.

Downloads HTML chapters from ibiblio.org/hyperwar index pages, converts to
markdown, splits into sub-chapters at section headings, and generates
metadata YAML with user input.

Usage:
    python3 scripts/import_hyperwar_html.py https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html

The script will:
1. Parse the index page to find chapter links and their subsections
2. Download each chapter HTML page
3. Convert HTML to markdown matching the existing content format
4. Split chapters into sub-chapter files (a, b, c...) at section headings
5. Prompt for metadata fields (series, book, author, etc.)
6. Create the directory structure under contentrepository/
"""

import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from src.utils.logger import setup_logging

logger = logging.getLogger(__name__)

import html2text
import requests
import yaml
from bs4 import BeautifulSoup

# Match existing content format: blockquoted paragraphs
CONTENT_REPO = Path(__file__).resolve().parent.parent / "contentrepository"


def fetch_page(url: str, max_retries: int = 3) -> BeautifulSoup:
    """Download and parse an HTML page with retry."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.content, "html.parser")
        except requests.exceptions.HTTPError as e:
            logger.warning(
                "HTTP error fetching %s (attempt %d/%d): %s",
                url,
                attempt + 1,
                max_retries,
                e,
            )
        except requests.exceptions.ConnectionError as e:
            logger.warning(
                "Connection error fetching %s (attempt %d/%d): %s",
                url,
                attempt + 1,
                max_retries,
                e,
            )
        except requests.exceptions.Timeout:
            logger.warning(
                "Timeout fetching %s (attempt %d/%d)", url, attempt + 1, max_retries
            )
        if attempt < max_retries - 1:
            time.sleep(2**attempt)
    raise requests.exceptions.RequestException(
        f"Failed to fetch {url} after {max_retries} attempts"
    )


def parse_index(index_url: str) -> list[dict]:
    """Parse a HyperWar index page to extract chapter links and subsections.

    Returns list of dicts with keys: number, title, url, subsections
    """
    soup = fetch_page(index_url)
    chapters = []

    # Find all links that look like chapter pages (e.g. USA-E-XChannel-1.html)
    # The index page has chapter links with Roman numeral labels
    all_links = soup.find_all("a", href=True)

    # Build a map of href -> link element for chapter pages
    # HyperWar pattern: links in the table of contents point to chapter HTML files
    chapter_links = []
    for link in all_links:
        href = link.get("href", "")
        text = link.get_text(strip=True)
        if not text or not href:
            continue
        # Skip non-chapter links (maps, images, charts, glossary, anchors)
        if any(
            x in href
            for x in [
                "maps/",
                "img/",
                "charts/",
                "Glossary",
                "MapSym",
                "#",
                "mailto:",
            ]
        ):
            continue
        # Skip if it's just a page number reference
        if re.match(r"^\d+$", text):
            continue
        full_url = urljoin(index_url, href)
        chapter_links.append({"title": text, "url": full_url, "href": href})

    # Deduplicate by URL
    seen_urls = set()
    unique_links = []
    for cl in chapter_links:
        if cl["url"] not in seen_urls:
            seen_urls.add(cl["url"])
            unique_links.append(cl)

    # Assign chapter numbers
    for i, cl in enumerate(unique_links, 1):
        chapters.append(
            {
                "number": i,
                "title": cl["title"],
                "url": cl["url"],
            }
        )

    return chapters


def convert_html_to_markdown(soup: BeautifulSoup, source_url: str) -> str:
    """Convert a chapter's HTML body to markdown matching existing format.

    Preserves footnote references, images, and page markers.
    Returns blockquoted markdown text.
    """
    # Remove navigation elements, headers/footers
    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()

    # Find the main content - typically in the body or a content div
    body = soup.find("body") or soup
    # Remove the HyperWar navigation bar if present
    for hr in body.find_all("hr"):
        # Navigation bars are typically before the first hr
        pass

    # Convert footnote links to superscript markdown format
    for link in body.find_all("a", href=True):
        href = link.get("href", "")
        text = link.get_text(strip=True)
        # Footnote pattern: links to fn*.html with numeric text
        if "fn" in href and re.match(r"^\d+$", text):
            fn_url = urljoin(source_url, href)
            link.replace_with(f"<sup>[{text}]({fn_url})</sup>")

    # Convert images to markdown
    for img in body.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if src:
            full_src = urljoin(source_url, src)
            img.replace_with(f"![{alt}]({full_src})")

    # Ensure paragraph breaks inside blockquotes survive html2text conversion.
    # HyperWar HTML uses <blockquote> with <p>/<center> children that html2text
    # merges into single lines. Insert explicit break markers between them.
    for bq in body.find_all("blockquote"):
        for p in bq.find_all(["p", "center"]):
            p.insert_before(soup.new_tag("br"))
            p.insert_before(soup.new_tag("br"))

    # Use html2text for conversion
    h = html2text.HTML2Text()
    h.body_width = 0  # No line wrapping
    h.unicode_snob = True
    h.protect_links = True
    h.wrap_links = False
    h.skip_internal_links = False

    raw_md = h.handle(str(body))

    # Clean up the markdown
    lines = raw_md.split("\n")
    cleaned = []
    for line in lines:
        # Convert page markers like --123-- to our format
        page_match = re.match(r"^\s*\*?\\?-+(\d+)\\?-+\*?\s*$", line.strip())
        if page_match:
            cleaned.append(f"\n*\\--{page_match.group(1)}--*\n")
            continue
        # Keep page anchor tags
        anchor_match = re.match(r'.*<a\s+id="page(\d+)"', line)
        if anchor_match:
            cleaned.append(f'<a id="page{anchor_match.group(1)}"></a>')
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def split_into_subchapters(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into sub-chapters at section headings.

    Returns list of (heading, content) tuples.
    Handles headings that may be blockquoted (> ### heading).
    """
    lines = markdown.split("\n")
    chunks: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    chapter_title_seen = False

    for line in lines:
        # Strip blockquote prefix for heading detection
        stripped = line.strip()
        bare = stripped.lstrip("> ").strip() if stripped.startswith(">") else stripped

        heading_match = re.match(r"^(#{2,3})\s+(.+)$", bare)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip("*_ ")

            if level == 2 and not chapter_title_seen:
                # First ## is the chapter title
                chapter_title_seen = True
                current_heading = heading_text
                current_lines.append(line)
                continue
            elif level == 3 and heading_text.lower() == "footnotes":
                # Skip footnotes section - don't start a new sub-chapter
                current_lines.append(line)
                continue
            else:
                # New subsection
                if current_lines:
                    chunks.append((current_heading, "\n".join(current_lines)))
                current_heading = heading_text
                current_lines = [line]
                continue

        current_lines.append(line)

    if current_lines:
        chunks.append((current_heading, "\n".join(current_lines)))

    if not chunks:
        chunks = [("", markdown)]

    return chunks


def format_as_blockquote(content: str) -> str:
    """Format content with blockquote markers matching existing style.

    Existing content uses '> ' prefix on content lines and '> ' for blank lines.
    """
    lines = content.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        # Blank line or whitespace-only blockquote line → standard blank blockquote
        if not stripped or stripped in (">", "> "):
            result.append("> ")
        elif stripped.startswith("*\\--") or stripped.startswith("<a id="):
            result.append(stripped)
        elif stripped.startswith("* * *"):
            result.append(stripped)
        elif stripped.startswith("> "):
            result.append(stripped)
        else:
            result.append(f"> {stripped}")
    return "\n".join(result)


def prompt_metadata(chapters: list[dict], source_url: str) -> dict:
    """Prompt user for book metadata."""
    # User-facing prompts stay as print() for clean interactive output
    print("\n" + "=" * 60)
    print("METADATA INPUT")
    print("=" * 60)
    print(f"\nSource: {source_url}")
    print(f"Chapters found: {len(chapters)}")
    for ch in chapters:
        print(f"  {ch['number']:>3}. {ch['title']}")

    print("\nEnter metadata (press Enter for defaults shown in [brackets]):\n")

    series = input("  Series [United States Army in World War II]: ").strip()
    if not series:
        series = "United States Army in World War II"

    book = input("  Book title: ").strip()
    while not book:
        book = input("  Book title (required): ").strip()

    author = input("  Author: ").strip()
    while not author:
        author = input("  Author (required): ").strip()

    license_val = input("  License [Public Domain]: ").strip()
    if not license_val:
        license_val = "Public Domain"

    copyright_date = input("  Copyright year: ").strip()

    # Directory name - derive from book title
    default_dir = re.sub(r"[^a-zA-Z0-9]+", "", book.title().replace(" ", ""))
    dir_name = input(f"  Directory name [{default_dir}]: ").strip()
    if not dir_name:
        dir_name = default_dir

    metadata = {
        "series": series,
        "book": book,
        "author": author,
        "license": license_val,
        "copyright_date": copyright_date,
        "source_url": source_url,
        "dir_name": dir_name,
    }
    logger.info("Metadata: %s by %s (%s)", book, author, dir_name)
    return metadata


def create_chapter_meta(meta: dict, chapter_num: int, chapter_title: str) -> str:
    """Generate chapter metadata YAML."""
    data = {
        "series": meta["series"],
        "book": meta["book"],
        "author": meta["author"],
        "chapter_number": chapter_num,
        "chapter_title": chapter_title,
        "license": meta["license"],
        "copyright_date": meta["copyright_date"],
        "source_url": meta["source_url"],
    }
    # Filter out empty values
    data = {k: v for k, v in data.items() if v}
    return yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False
    )


def process_book(index_url: str) -> None:
    """Main processing pipeline."""
    logger.info("Fetching index: %s", index_url)
    chapters = parse_index(index_url)

    if not chapters:
        logger.error("No chapters found on index page: %s", index_url)
        sys.exit(1)

    logger.info("Found %d chapters/sections", len(chapters))

    # Get metadata from user
    meta = prompt_metadata(chapters, index_url)
    book_dir = CONTENT_REPO / meta["dir_name"]

    if book_dir.exists():
        resp = input(f"\n  Directory {book_dir} already exists. Overwrite? [y/N]: ")
        if resp.lower() != "y":
            logger.info("Aborted by user")
            sys.exit(0)

    logger.info("Output directory: %s", book_dir)
    logger.info("Processing %d chapters...", len(chapters))

    processed = 0
    failed = 0

    for ch in chapters:
        chapter_num = ch["number"]
        chapter_title = ch["title"]
        chapter_url = ch["url"]

        logger.info("Chapter %d: %s", chapter_num, chapter_title)
        logger.debug("Downloading %s", chapter_url)

        try:
            soup = fetch_page(chapter_url)
        except requests.exceptions.RequestException as e:
            logger.error(
                "Failed to download chapter %d (%s): %s", chapter_num, chapter_url, e
            )
            failed += 1
            continue

        # Convert to markdown
        markdown = convert_html_to_markdown(soup, chapter_url)

        # Split into sub-chapters
        subchapters = split_into_subchapters(markdown)
        sub_labels = []
        for i, (heading, _) in enumerate(subchapters):
            suffix = chr(ord("a") + i)
            label = heading if heading else "(intro)"
            sub_labels.append(f"{suffix}: {label}")
        logger.info(
            "  Split into %d sub-chapter(s): %s",
            len(subchapters),
            ", ".join(sub_labels),
        )

        # Create chapter directory and write files
        ch_dir = book_dir / f"chapter{chapter_num}"
        try:
            ch_dir.mkdir(parents=True, exist_ok=True)

            # Write metadata
            meta_content = create_chapter_meta(meta, chapter_num, chapter_title)
            meta_file = ch_dir / f"chapter{chapter_num}-meta.yaml"
            meta_file.write_text(meta_content, encoding="utf-8")

            # Write sub-chapter content files
            for i, (heading, content) in enumerate(subchapters):
                suffix = chr(ord("a") + i)
                formatted = format_as_blockquote(content)
                content_file = ch_dir / f"chapter{chapter_num}{suffix}-content.md"
                content_file.write_text(formatted + "\n", encoding="utf-8")
        except OSError as e:
            logger.error(
                "Failed to write chapter %d files to %s: %s", chapter_num, ch_dir, e
            )
            failed += 1
            continue

        processed += 1
        # Be polite to the server
        time.sleep(0.5)

    logger.info("Import complete: %d processed, %d failed", processed, failed)
    logger.info("Book: %s | Location: %s", meta["book"], book_dir)
    if failed:
        logger.warning(
            "%d chapter(s) failed to download — review logs and retry", failed
        )
    logger.info("Next: python3 phase1_parse.py && python3 phase2_retry.py")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/import_hyperwar_html.py <index_url>")
        print()
        print("Example:")
        print(
            "  python3 scripts/import_hyperwar_html.py "
            "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html"
        )
        sys.exit(1)

    setup_logging(level="INFO", log_file="logs/import_hyperwar.log")
    index_url = sys.argv[1]
    process_book(index_url)


if __name__ == "__main__":
    main()
