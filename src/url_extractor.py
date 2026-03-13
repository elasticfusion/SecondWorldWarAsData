"""URL content extraction and conversion to markdown structure."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import html2text
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class URLExtractor:
    """Extract content from URLs and convert to markdown structure."""

    def __init__(self, output_dir: Path, timeout: int = 30):
        """Initialize URL extractor."""
        self.output_dir = output_dir
        self.timeout = timeout
        self.html2text = html2text.HTML2Text()
        self.html2text.ignore_links = False
        self.html2text.ignore_images = False
        self.html2text.body_width = 0  # No wrapping

    def fetch_url(self, url: str) -> str:
        """Fetch HTML content from URL."""
        logger.info(f"Fetching: {url}")
        response = requests.get(url, allow_redirects=True, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def html_to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        return self.html2text.handle(html)

    def extract_main_content(
        self, html: str, content_selector: Optional[str] = None
    ) -> str:
        """
        Extract main content from HTML.

        Args:
            html: HTML content
            content_selector: CSS selector for main content (e.g., 'article', '#content')

        Returns:
            Extracted HTML content
        """
        soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        # Extract specific content if selector provided
        if content_selector:
            content = soup.select_one(content_selector)
            if content:
                return str(content)

        # Try common content selectors
        for selector in ["article", "main", '[role="main"]', ".content", "#content"]:
            content = soup.select_one(selector)
            if content:
                return str(content)

        # Fall back to body
        body = soup.find("body")
        return str(body) if body else html

    def split_into_chapters(
        self,
        markdown: str,
        chapter_pattern: str = r"^>?\s*#{1,2}\s+Chapter\s+(\d+|[IVXLCDM]+)",
        subchapter_pattern: str = r"^>?\s*###\s+(.+)$",
    ) -> List[Dict[str, str]]:
        """
        Split markdown into chapters and sub-chapters based on heading patterns.

        Args:
            markdown: Markdown content
            chapter_pattern: Regex pattern to identify chapter headings
            subchapter_pattern: Regex pattern to identify sub-chapter headings

        Returns:
            List of dicts with chapter_number, title, content, subsections
        """
        chapters: List[Dict[str, Any]] = []
        lines = markdown.split("\n")

        current_chapter: Optional[Dict[str, Any]] = None
        current_subchapter: Optional[str] = None
        current_content: List[str] = []

        for line in lines:
            # Check if line is a chapter heading
            chapter_match = re.match(chapter_pattern, line, re.IGNORECASE)
            if chapter_match:
                # Save previous chapter
                if current_chapter:
                    # Save last subchapter
                    if current_subchapter:
                        current_chapter["subsections"].append(
                            {
                                "title": current_subchapter,
                                "content": "\n".join(current_content).strip(),
                            }
                        )
                    chapters.append(current_chapter)

                # Start new chapter
                chapter_num = chapter_match.group(1)
                current_chapter = {
                    "chapter_number": chapter_num,
                    "title": line.strip("#").strip(),
                    "subsections": [],
                }
                current_subchapter = None
                current_content = []
                continue

            # Check if line is a sub-chapter heading
            subchapter_match = re.match(subchapter_pattern, line)
            if subchapter_match and current_chapter:
                # Save previous subchapter
                if current_subchapter:
                    current_chapter["subsections"].append(
                        {
                            "title": current_subchapter,
                            "content": "\n".join(current_content).strip(),
                        }
                    )

                # Start new subchapter
                current_subchapter = subchapter_match.group(1).strip()
                # Clean up markdown formatting (italic, bold)
                current_subchapter = re.sub(r"^[_*]+|[_*]+$", "", current_subchapter)
                current_content = []
                continue

            # Add line to current content
            current_content.append(line)

        # Save last chapter and subchapter
        if current_chapter:
            if current_subchapter:
                current_chapter["subsections"].append(
                    {
                        "title": current_subchapter,
                        "content": "\n".join(current_content).strip(),
                    }
                )
            chapters.append(current_chapter)

        return chapters

    def save_chapters(
        self, chapters: List[Dict[str, str]], book_name: str, source_url: str
    ) -> List[Path]:
        """
        Save chapters as markdown files with sub-chapters.

        Args:
            chapters: List of chapter dicts with subsections
            book_name: Name of the book
            source_url: Original source URL

        Returns:
            List of saved file paths
        """
        book_dir = self.output_dir / book_name
        book_dir.mkdir(parents=True, exist_ok=True)

        saved_files = []

        for chapter in chapters:
            chapter_num = chapter["chapter_number"]
            chapter_dir = book_dir / f"chapter{chapter_num}"
            chapter_dir.mkdir(exist_ok=True)

            # Save meta file
            meta_file = chapter_dir / f"chapter{chapter_num}-meta.md"
            meta_content = f"""Source: {source_url}
Book: {book_name}
Chapter: {chapter['title']}
License: Check source
"""
            meta_file.write_text(meta_content, encoding="utf-8")
            saved_files.append(meta_file)

            # Save subsections as separate files
            subsections_data: Any = chapter.get("subsections", [])
            subsections: List[Dict[str, Any]] = []
            if isinstance(subsections_data, list):
                subsections = subsections_data
            if subsections:
                for i, subsection in enumerate(subsections, start=1):
                    section_letter = chr(ord("a") + i - 1)  # a, b, c, d...
                    content_file = (
                        chapter_dir / f"chapter{chapter_num}{section_letter}-content.md"
                    )

                    # Add subsection title as heading
                    content = f"### {subsection['title']}\n\n{subsection['content']}"
                    content_file.write_text(content, encoding="utf-8")
                    saved_files.append(content_file)

                    logger.info(f"  Saved: {content_file.relative_to(self.output_dir)}")
            else:
                # No subsections, save as single file
                content_file = chapter_dir / f"chapter{chapter_num}-content.md"
                content_file.write_text(chapter.get("content", ""), encoding="utf-8")
                saved_files.append(content_file)
                logger.info(f"  Saved: {content_file.relative_to(self.output_dir)}")

        return saved_files

    def extract_from_url(
        self,
        url: str,
        book_name: str,
        content_selector: Optional[str] = None,
        chapter_pattern: Optional[str] = None,
    ) -> List[Path]:
        """
        Extract content from URL and save as chapter structure.

        Args:
            url: Source URL
            book_name: Name for the book directory
            content_selector: CSS selector for main content
            chapter_pattern: Regex pattern for chapter headings

        Returns:
            List of saved file paths
        """
        # Fetch HTML
        html = self.fetch_url(url)

        # Extract main content
        content_html = self.extract_main_content(html, content_selector)

        # Convert to markdown
        markdown = self.html_to_markdown(content_html)

        # Split into chapters
        if chapter_pattern:
            chapters = self.split_into_chapters(
                markdown, chapter_pattern=chapter_pattern
            )
        else:
            chapters = self.split_into_chapters(markdown)

        if not chapters:
            logger.warning("No chapters found, saving as single chapter")
            chapters = [
                {"chapter_number": "1", "title": "Chapter 1", "content": markdown}
            ]

        logger.info(f"Found {len(chapters)} chapter(s)")

        # Save chapters
        return self.save_chapters(chapters, book_name, url)
