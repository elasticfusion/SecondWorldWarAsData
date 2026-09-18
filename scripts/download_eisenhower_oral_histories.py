#!/usr/bin/env python3
"""Download digitized Eisenhower Presidential Library oral history PDFs politely."""

from __future__ import annotations

import csv
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "contentrepository/indexes/eisenhower_oral_histories_wwii_participants.csv"
OUT_DIR = PROJECT_ROOT / "contentrepository/EisenhowerPresidentialLibraryOralHistories"
SOURCE_PAGE = "https://www.eisenhowerlibrary.gov/research/oral-histories"

CHROME_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

MIN_DELAY_SEC = 4.0
MAX_DELAY_SEC = 8.0
INITIAL_PAGE_WAIT_SEC = 3.0


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_digital_entries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("digital_artifact") == "yes" and row.get("transcript_pdf"):
                entries.append(row)
    return entries


def target_filename(entry: dict[str, str]) -> str:
    oh_id = entry.get("oh_id") or "no-oh-id"
    name_slug = slugify(entry.get("name", "unknown"))
    url_name = Path(urlparse(entry["transcript_pdf"]).path).name
    if url_name.endswith(".pdf"):
        return f"{oh_id}_{name_slug}_{url_name}"
    return f"{oh_id}_{name_slug}.pdf"


def polite_wait() -> None:
    delay = random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC)
    time.sleep(delay)


def main() -> int:
    entries = load_digital_entries()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=CHROME_USER_AGENT,
            locale="en-US",
            viewport={"width": 1365, "height": 900},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        page = context.new_page()

        print(f"Opening source page: {SOURCE_PAGE}")
        page.goto(SOURCE_PAGE, wait_until="domcontentloaded", timeout=120_000)
        time.sleep(INITIAL_PAGE_WAIT_SEC)

        for index, entry in enumerate(entries, start=1):
            url = entry["transcript_pdf"]
            filename = target_filename(entry)
            dest = OUT_DIR / filename

            record = {
                "name": entry.get("name"),
                "oh_id": entry.get("oh_id"),
                "source_url": url,
                "local_file": str(dest.relative_to(PROJECT_ROOT)),
                "status": "skipped_existing",
                "bytes": dest.stat().st_size if dest.exists() else 0,
            }

            if dest.exists() and dest.stat().st_size > 1024:
                print(f"[{index}/{len(entries)}] skip existing {filename}")
                manifest.append(record)
                continue

            print(f"[{index}/{len(entries)}] downloading {entry.get('name')} ({entry.get('oh_id')})")
            try:
                response = context.request.get(
                    url,
                    headers={
                        "Referer": SOURCE_PAGE,
                        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                    },
                    timeout=120_000,
                )
                if not response.ok:
                    raise RuntimeError(f"HTTP {response.status}")

                body = response.body()
                if len(body) < 1024:
                    raise RuntimeError(f"suspiciously small payload ({len(body)} bytes)")

                dest.write_bytes(body)
                record["status"] = "downloaded"
                record["bytes"] = len(body)
                manifest.append(record)
                print(f"  saved {filename} ({len(body):,} bytes)")
            except Exception as exc:  # noqa: BLE001 - report and continue
                record["status"] = "failed"
                record["error"] = str(exc)
                failures.append(
                    {
                        "name": entry.get("name", ""),
                        "oh_id": entry.get("oh_id", ""),
                        "url": url,
                        "error": str(exc),
                    }
                )
                manifest.append(record)
                print(f"  failed: {exc}")

            if index < len(entries):
                polite_wait()

        browser.close()

    manifest_path = OUT_DIR / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    downloaded = sum(1 for item in manifest if item["status"] == "downloaded")
    skipped = sum(1 for item in manifest if item["status"] == "skipped_existing")
    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {len(failures)} failed")
    print(f"Output directory: {OUT_DIR}")
    print(f"Manifest: {manifest_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())