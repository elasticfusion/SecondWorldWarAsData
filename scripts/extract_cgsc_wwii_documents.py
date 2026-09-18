#!/usr/bin/env python3
"""Index CGSC World War II Operational Documents collection with full item metadata."""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://cgsc.contentdm.oclc.org"
COLLECTION = "p4013coll8"
SEARCH_URL = (
    f"{BASE}/digital/api/search/collection/{COLLECTION}"
    "/searchterm//field/all/mode/all/conn/and/maxRecords/{max_records}/page/{page}"
)
DETAIL_URL = f"{BASE}/digital/api/singleitem/collection/{COLLECTION}/id/{{item_id}}"
ITEM_URL = f"{BASE}/digital/collection/{COLLECTION}/id/{{item_id}}"
FILE_URL = (
    f"{BASE}/utils/getfile/collection/{COLLECTION}/id/{{item_id}}/filename/{{filename}}"
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_CSV = (
    PROJECT_ROOT
    / "contentrepository/indexes/cgsc_wwii_operational_and_historical_analysis.csv"
)
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/cgsc_wwii_operational_and_historical_analysis_manifest.json"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
PAGE_SIZE = 200
DETAIL_DELAY_SEC = 0.25
PAGE_DELAY_SEC = 1.0
HISTORICAL_ANALYSIS_RE = re.compile(r"historical\s+analysis", re.I)
TEXT_FIELD_KEYS = {
    "title",
    "altern",
    "descri",
    "series",
    "keywor",
    "subjec",
    "subjea",
    "subjeb",
    "type",
    "format",
    "subcol",
    "collec",
}


def fetch_json(url: str, retries: int = 4) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url}")


def is_historical_analysis(fields: dict[str, str]) -> bool:
    for key in TEXT_FIELD_KEYS:
        value = fields.get(key, "")
        if value and HISTORICAL_ANALYSIS_RE.search(value):
            return True
    return False


def flatten_detail(item_id: str, detail: dict) -> dict[str, str]:
    filename = detail.get("filename", "") or ""
    detail_url = ITEM_URL.format(item_id=item_id)
    if filename:
        file_url = FILE_URL.format(item_id=item_id, filename=filename)
    else:
        file_url = f"{BASE}/api/collection/{COLLECTION}/id/{item_id}/download"

    row: dict[str, str] = {
        "item_id": item_id,
        "collection_alias": COLLECTION,
        "collection_name": "World War II Operational Documents",
        "document_link": detail_url,
        "document_detail_url": detail_url,
        "document_url": file_url,
        "document_download_url": file_url,
        "content_type": detail.get("contentType", ""),
        "filename": filename,
        "download_uri": file_url,
        "thumbnail_uri": f"{BASE}{detail['thumbnailUri']}" if detail.get("thumbnailUri") else "",
        "iiif_info_uri": f"{BASE}{detail['iiifInfoUri']}" if detail.get("iiifInfoUri") else "",
    }

    fields: dict[str, str] = {}
    for field in detail.get("fields", []):
        key = field.get("key", "")
        label = field.get("label", "")
        value = field.get("value", "") or ""
        fields[key] = value
        row[f"meta_{key}"] = value
        row[f"label_{key}"] = label

    row["author"] = fields.get("creato", "")
    row["title"] = fields.get("title", "")
    row["subcollection"] = fields.get("subcol", "")
    row["resource_type"] = fields.get("format", "")

    if is_historical_analysis(fields):
        row["document_category"] = "historical_analysis"
    else:
        row["document_category"] = "operational_document"

    return row


def fetch_all_item_ids() -> list[str]:
    first = fetch_json(SEARCH_URL.format(max_records=1, page=1))
    total = int(first["totalResults"])
    print(f"Collection total: {total}")

    item_ids: list[str] = []
    page = 1
    while len(item_ids) < total:
        payload = fetch_json(SEARCH_URL.format(max_records=PAGE_SIZE, page=page))
        batch = payload.get("items", [])
        if not batch:
            break
        for item in batch:
            item_ids.append(str(item["itemId"]))
        print(f"  listed page {page}: {len(batch)} items ({len(item_ids)}/{total})")
        page += 1
        time.sleep(PAGE_DELAY_SEC)
    return item_ids


def main() -> int:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    item_ids = fetch_all_item_ids()

    rows: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    for index, item_id in enumerate(item_ids, start=1):
        try:
            detail = fetch_json(DETAIL_URL.format(item_id=item_id))
            rows.append(flatten_detail(item_id, detail))
        except Exception as exc:  # noqa: BLE001
            failures.append({"item_id": item_id, "error": str(exc)})
        if index % 50 == 0 or index == len(item_ids):
            print(f"  details fetched: {index}/{len(item_ids)}")
        time.sleep(DETAIL_DELAY_SEC)

    all_columns: list[str] = []
    seen: set[str] = set()
    priority = [
        "item_id",
        "collection_alias",
        "collection_name",
        "document_category",
        "title",
        "author",
        "document_link",
        "document_detail_url",
        "document_url",
        "document_download_url",
        "subcollection",
        "resource_type",
        "content_type",
        "filename",
        "download_uri",
        "thumbnail_uri",
        "iiif_info_uri",
    ]
    for col in priority:
        seen.add(col)
        all_columns.append(col)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                all_columns.append(key)

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    manifest = {
        "source_collection": COLLECTION,
        "source_search_api": SEARCH_URL.format(max_records=PAGE_SIZE, page=1),
        "total_items": len(item_ids),
        "indexed_items": len(rows),
        "failed_items": len(failures),
        "operational_documents": sum(
            1 for row in rows if row["document_category"] == "operational_document"
        ),
        "historical_analysis_documents": sum(
            1 for row in rows if row["document_category"] == "historical_analysis"
        ),
        "failures": failures,
        "output_csv": str(OUTPUT_CSV),
    }
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}")
    print(
        "Categories: "
        f"{manifest['operational_documents']} operational, "
        f"{manifest['historical_analysis_documents']} historical analysis"
    )
    if failures:
        print(f"Failures: {len(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())