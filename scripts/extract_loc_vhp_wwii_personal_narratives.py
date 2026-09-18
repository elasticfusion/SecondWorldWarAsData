#!/usr/bin/env python3
"""Extract WWII personal narratives from Library of Congress VHP search results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import random
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_SEARCH_URL = (
    "https://www.loc.gov/search/?fa=original-format%3Apersonal+narrative"
    "%7Csubject%3Aworld+war%2C+1939-1945&sb=date&st=list&c=150"
)
SOURCE_ORGANIZATION = "Library of Congress"
SOURCE_COLLECTION = "Veterans History Project"
SOURCE_REPOSITORY = (
    "Veterans History Project, American Folklife Center, Library of Congress"
)

CACHE_DIR = PROJECT_ROOT / "contentrepository/LibraryOfCongress/VHP"
SEGMENT_CACHE_DIR = CACHE_DIR / "segment_searches"
SEARCH_CACHE_DIR = CACHE_DIR / "search_pages"
CHECKPOINT_DIR = CACHE_DIR / "checkpoint"
CHECKPOINT_STATE_JSON = CHECKPOINT_DIR / "state.json"
CHECKPOINT_INDIVIDUALS_JSONL = CHECKPOINT_DIR / "individuals.jsonl"
CHECKPOINT_ARTIFACTS_JSONL = CHECKPOINT_DIR / "artifacts.jsonl"
DEFAULT_RESUME_OVERLAP = 5
OUTPUT_CSV_ARTIFACTS = (
    PROJECT_ROOT
    / "contentrepository/indexes/loc_vhp_wwii_personal_narratives_artifacts.csv"
)
OUTPUT_CSV_INDIVIDUALS = (
    PROJECT_ROOT
    / "contentrepository/indexes/loc_vhp_wwii_personal_narratives_individuals.csv"
)
# Legacy paths retained as aliases for any downstream references.
OUTPUT_CSV = OUTPUT_CSV_ARTIFACTS
OUTPUT_CSV_VETERANS = OUTPUT_CSV_INDIVIDUALS
MANIFEST_JSON = (
    PROJECT_ROOT
    / "contentrepository/indexes/loc_vhp_wwii_personal_narratives_manifest.json"
)

ARTIFACT_DELIMITER = " | "
WITHIN_ARTIFACT_DELIMITER = "; "

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_DELAY_SEC = 2.0
DEFAULT_JITTER_SEC = 0.75
DEFAULT_CSV_CHECKPOINT_EVERY = 1500
MAX_RETRIES = 8
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504, 520, 522, 524, 525}
CLOUDFLARE_HTTP_CODES = {520, 522, 524}

MEDIA_URL_KEYS = (
    "video",
    "audio",
    "pdf",
    "fulltext_file",
    "image",
    "background",
    "video_stream",
    "info",
)

INDIVIDUAL_CSV_FIELDS = [
    "source_organization",
    "source_collection",
    "source_repository",
    "source_search_url",
    "captured_at",
    "metadata_page_url",
    "loc_item_id",
    "collection_number",
    "collection_title",
    "veteran_name",
    "birth_place",
    "gender",
    "race",
    "state_of_residence_at_collection",
    "veteran_status",
    "prisoner_of_war",
    "rank",
    "branch_of_service",
    "unit_of_service",
    "service_location",
    "war_or_conflicts",
    "battles_campaigns",
    "dates_of_service",
    "entrance_into_service",
    "highest_rank",
    "location_of_service",
    "military_status",
    "service_history_json",
    "description",
    "interview_collection_notes",
    "contributor_veteran",
    "contributor_interviewer",
    "contributor_organization",
    "contributor_names",
    "subject_headings",
    "subjects",
    "locations_home",
    "locations_service",
    "original_formats",
    "online_formats",
    "mime_types",
    "digitized",
    "access_restricted",
    "date_indexed",
    "dates_indexed",
    "collection_mets_url",
    "artifact_index_file",
    "artifact_ids",
    "artifact_row_count",
    "artifact_count",
    "transcript_mentioned_in_materials",
    "transcript_artifact_ids",
]

ARTIFACT_CSV_FIELDS = [
    "artifact_id",
    "individual_index_file",
    "individual_metadata_page_url",
    "collection_number",
    "veteran_name",
    "loc_item_id",
    "source_organization",
    "source_collection",
    "source_search_url",
    "captured_at",
    "artifact_sequence_in_collection",
    "artifact_resource_type",
    "artifact_caption",
    "artifact_resource_label",
    "artifact_duration_seconds",
    "artifact_width",
    "artifact_height",
    "artifact_segment_number",
    "artifact_segment_title",
    "artifact_segment_count",
    "artifact_resource_page_url",
    "artifact_file_url_primary",
    "artifact_file_url_mp4",
    "artifact_file_url_mp3",
    "artifact_file_url_pdf",
    "artifact_file_url_image",
    "artifact_file_url_stream",
    "artifact_file_url_other",
    "artifact_all_file_urls",
    "transcript_available",
    "transcript_artifact_id",
    "transcript_file_url",
    "raw_resource_json",
]

TRANSCRIPT_RESOURCE_TYPES = {"transcription", "interview"}
INTERVIEW_MEDIA_TYPES = {"video", "audio"}

CSV_FIELDS = ARTIFACT_CSV_FIELDS
VETERAN_CSV_FIELDS = INDIVIDUAL_CSV_FIELDS


class LocJsonClient:
    def __init__(self, delay_sec: float, jitter_sec: float) -> None:
        self.delay_sec = delay_sec
        self.jitter_sec = jitter_sec
        self._last_request_at = 0.0

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
        }

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait_for = self.delay_sec + random.uniform(0, self.jitter_sec)
        if elapsed < wait_for:
            time.sleep(wait_for - elapsed)

    def fetch_json(
        self,
        url: str,
        cache_path: Path | None = None,
        cache_validator: Callable[[dict], bool] | None = None,
    ) -> dict:
        if cache_path and cache_path.exists() and cache_path.stat().st_size > 10:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cache_validator is None or cache_validator(cached):
                    return cached
            except json.JSONDecodeError:
                cache_path.unlink(missing_ok=True)

        request_url = ensure_json_url(url)
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            request = urllib.request.Request(request_url, headers=self._headers())
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
                data = json.loads(payload.decode("utf-8"))
                self._last_request_at = time.monotonic()
                if cache_path:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(data, indent=2),
                        encoding="utf-8",
                    )
                return data
            except json.JSONDecodeError as exc:
                last_error = exc
                if cache_path:
                    cache_path.unlink(missing_ok=True)
                if attempt < MAX_RETRIES - 1:
                    wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                    print(
                        f"Retry {attempt + 1}/{MAX_RETRIES - 1} after invalid JSON "
                        f"from {request_url} (waiting {wait:.1f}s)",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue
                raise
            except (urllib.error.HTTPError, http.client.IncompleteRead) as exc:
                last_error = exc
                retryable = isinstance(exc, http.client.IncompleteRead) or (
                    isinstance(exc, urllib.error.HTTPError)
                    and exc.code in RETRYABLE_HTTP_CODES
                )
                if retryable and attempt < MAX_RETRIES - 1:
                    code = exc.code if isinstance(exc, urllib.error.HTTPError) else "IncompleteRead"
                    base_wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                    if isinstance(exc, urllib.error.HTTPError) and code in CLOUDFLARE_HTTP_CODES:
                        base_wait += 5.0
                    print(
                        f"Retry {attempt + 1}/{MAX_RETRIES - 1} after HTTP {code} "
                        f"from {request_url} (waiting {base_wait:.1f}s)",
                        flush=True,
                    )
                    time.sleep(base_wait)
                    continue
                raise
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                    print(
                        f"Retry {attempt + 1}/{MAX_RETRIES - 1} after network error "
                        f"from {request_url} (waiting {wait:.1f}s)",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to fetch {request_url}")


def ensure_json_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["fo"] = ["json"]
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
    )


def join_values(value: object, separator: str = " | ") -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [join_values(item, separator=separator) for item in value]
        return separator.join(part for part in parts if part)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def veteran_name_from_record(record: dict) -> str:
    veteran = join_values(record.get("contributor_veteran"))
    if veteran:
        return clean_text(veteran.title())
    item = record.get("item") or {}
    names = item.get("contributor_names") or []
    for name in names:
        if "," in name and not any(
            token in name.lower()
            for token in ("museum", "library", "project", "center", "school")
        ):
            return clean_text(name)
    title = clean_text(record.get("title", ""))
    title = re.sub(r"\s+Collection$", "", title, flags=re.I)
    return title


def collection_number_from_record(record: dict) -> str:
    item = record.get("item") or {}
    return clean_text(
        item.get("collection_number")
        or join_values(record.get("number_collection"))
        or ""
    )


def loc_item_id_from_record(record: dict) -> str:
    item_id = clean_text(record.get("id", ""))
    if item_id:
        return item_id
    url = clean_text(record.get("url", ""))
    match = re.search(r"/item/([^/?#]+)/?", url)
    return match.group(1) if match else ""


def metadata_page_url_from_record(record: dict) -> str:
    return clean_text(record.get("url", ""))


def capture_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def artifact_index_file_path() -> str:
    return str(OUTPUT_CSV_ARTIFACTS.relative_to(PROJECT_ROOT))


def individual_index_file_path() -> str:
    return str(OUTPUT_CSV_INDIVIDUALS.relative_to(PROJECT_ROOT))


def text_mentions_transcript(text: str) -> bool:
    return bool(re.search(r"\btranscript\b", text, re.I))


def materials_mention_transcript(record: dict) -> bool:
    item = record.get("item") or {}
    materials = join_values(item.get("materials"))
    return text_mentions_transcript(materials)


def is_transcript_artifact_row(row: dict[str, str]) -> bool:
    resource_type = clean_text(row.get("artifact_resource_type", "")).lower()
    if resource_type in TRANSCRIPT_RESOURCE_TYPES:
        return True
    combined = (
        f"{row.get('artifact_caption', '')} {row.get('artifact_resource_label', '')}"
    )
    return text_mentions_transcript(combined)


def transcript_file_url_from_artifact_row(row: dict[str, str]) -> str:
    pdf_url = clean_text(row.get("artifact_file_url_pdf", ""))
    if pdf_url:
        return pdf_url
    primary_url = clean_text(row.get("artifact_file_url_primary", ""))
    if primary_url and primary_url.lower().endswith(".xml"):
        return primary_url
    if is_transcript_artifact_row(row) and primary_url:
        return primary_url
    image_url = clean_text(row.get("artifact_file_url_image", ""))
    if is_transcript_artifact_row(row) and image_url:
        return image_url
    return ""


def empty_transcript_fields() -> dict[str, str]:
    return {
        "transcript_available": "false",
        "transcript_artifact_id": "",
        "transcript_file_url": "",
    }


def annotate_transcript_links(
    artifact_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    transcript_rows = [row for row in artifact_rows if is_transcript_artifact_row(row)]
    transcript_rows_with_files = [
        row for row in transcript_rows if transcript_file_url_from_artifact_row(row)
    ]
    linked_ids = unique_preserve_order(
        [row["artifact_id"] for row in transcript_rows_with_files]
    )
    linked_urls = unique_preserve_order(
        [transcript_file_url_from_artifact_row(row) for row in transcript_rows_with_files]
    )

    for row in artifact_rows:
        row.update(empty_transcript_fields())
        if is_transcript_artifact_row(row):
            transcript_url = transcript_file_url_from_artifact_row(row)
            row["transcript_available"] = "true" if transcript_url else "false"
            row["transcript_artifact_id"] = row["artifact_id"]
            row["transcript_file_url"] = transcript_url
            continue

        resource_type = clean_text(row.get("artifact_resource_type", "")).lower()
        if resource_type in INTERVIEW_MEDIA_TYPES and linked_ids:
            row["transcript_available"] = "true"
            row["transcript_artifact_id"] = ARTIFACT_DELIMITER.join(linked_ids)
            row["transcript_file_url"] = ARTIFACT_DELIMITER.join(linked_urls)

    return artifact_rows


def build_artifact_id(
    collection_number: str,
    sequence: int | str,
    segment_number: str = "",
) -> str:
    base = f"{collection_number}:{sequence}"
    if segment_number:
        return f"{base}:{segment_number}"
    return base


def service_history_summary(service_history: list[dict]) -> dict[str, str]:
    wars: list[str] = []
    battles: list[str] = []
    dates: list[str] = []
    entrances: list[str] = []
    ranks: list[str] = []
    locations: list[str] = []
    statuses: list[str] = []
    for entry in service_history:
        for key, bucket in (
            ("war_or_conflict", wars),
            ("battles_campaigns", battles),
            ("dates_of_service", dates),
            ("entrance_into_service", entrances),
            ("highest_rank", ranks),
            ("location_of_service", locations),
            ("military_status", statuses),
        ):
            value = clean_text(str(entry.get(key, "")))
            if value and value not in bucket:
                bucket.append(value)
    return {
        "war_or_conflicts": " | ".join(wars),
        "battles_campaigns": " | ".join(battles),
        "dates_of_service": " | ".join(dates),
        "entrance_into_service": " | ".join(entrances),
        "highest_rank": " | ".join(ranks),
        "location_of_service": " | ".join(locations),
        "military_status": " | ".join(statuses),
    }


def best_image_url(image_urls: list[str]) -> str:
    if not image_urls:
        return ""
    for url in image_urls:
        if "pct:100" in url or "/full/full/" in url:
            return url.split("#", 1)[0]
    return image_urls[-1].split("#", 1)[0]


def media_urls_from_resource(resource: dict) -> dict[str, str]:
    urls: dict[str, str] = {}
    for key in MEDIA_URL_KEYS:
        value = resource.get(key)
        if isinstance(value, str) and value.startswith("http"):
            urls[key] = value.split("#", 1)[0]
    other: list[str] = []
    for key, value in resource.items():
        if key in MEDIA_URL_KEYS:
            continue
        if isinstance(value, str) and value.startswith("http"):
            other.append(value.split("#", 1)[0])
    if other:
        urls["other"] = " | ".join(dict.fromkeys(other))
    return urls


def choose_primary_media_url(urls: dict[str, str], image_urls: list[str]) -> str:
    for key in ("video", "audio", "pdf", "fulltext_file"):
        if urls.get(key):
            return urls[key]
    if image_urls:
        return best_image_url(image_urls)
    for key in ("image", "background"):
        if urls.get(key):
            return urls[key]
    if urls.get("other"):
        return urls["other"].split(" | ", 1)[0]
    return ""


def flatten_urls(urls: dict[str, str], image_urls: list[str]) -> str:
    combined: list[str] = []
    for key in ("video", "audio", "pdf", "fulltext_file", "image", "background", "video_stream", "info"):
        value = urls.get(key)
        if value:
            combined.append(value)
    image = best_image_url(image_urls)
    if image and image not in combined:
        combined.append(image)
    other = urls.get("other", "")
    if other:
        combined.extend(part for part in other.split(" | ") if part)
    return " | ".join(dict.fromkeys(combined))


def cache_name_for_url(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return digest


def is_valid_search_page_payload(payload: dict) -> bool:
    return isinstance(payload.get("results"), list)


def is_valid_segment_payload(payload: dict) -> bool:
    return isinstance(payload.get("results"), list)


def invalidate_search_cache_from_page(page_number: int) -> None:
    if page_number < 1:
        return
    for cache_path in SEARCH_CACHE_DIR.glob("page_*.json"):
        try:
            page_index = int(cache_path.stem.split("_", 1)[1])
        except ValueError:
            continue
        if page_index >= page_number:
            cache_path.unlink(missing_ok=True)


def last_cached_search_page() -> int:
    last_page = 0
    for cache_path in SEARCH_CACHE_DIR.glob("page_*.json"):
        try:
            page_index = int(cache_path.stem.split("_", 1)[1])
        except ValueError:
            continue
        last_page = max(last_page, page_index)
    return last_page


def load_search_page_payload(page_number: int) -> dict | None:
    cache_path = SEARCH_CACHE_DIR / f"page_{page_number:03d}.json"
    if not cache_path.exists() or cache_path.stat().st_size <= 10:
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_seen_ids_from_cached_pages(max_page: int) -> set[str]:
    seen_ids: set[str] = set()
    for page_number in range(1, max_page + 1):
        payload = load_search_page_payload(page_number)
        if not payload:
            continue
        for record in payload.get("results") or []:
            record_id = clean_text(record.get("id") or record.get("url") or "")
            if record_id:
                seen_ids.add(record_id)
    return seen_ids


def next_url_from_cached_page(page_number: int) -> str:
    payload = load_search_page_payload(page_number)
    if not payload:
        return ""
    return clean_text((payload.get("pagination") or {}).get("next") or "")


def iter_search_records(max_pages: int | None = None):
    seen_ids: set[str] = set()
    for cache_path in sorted(SEARCH_CACHE_DIR.glob("page_*.json")):
        try:
            page_number = int(cache_path.stem.split("_", 1)[1])
        except ValueError:
            continue
        if max_pages is not None and page_number > max_pages:
            break
        payload = load_search_page_payload(page_number)
        if not payload:
            continue
        for record in payload.get("results") or []:
            record_id = clean_text(record.get("id") or record.get("url") or "")
            if not record_id or record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            yield record


def invalidate_segment_caches_for_record(record: dict) -> None:
    for resource in record.get("resources") or []:
        search_url = clean_text(resource.get("search", ""))
        if not search_url:
            continue
        cache_path = SEGMENT_CACHE_DIR / f"{cache_name_for_url(search_url)}.json"
        cache_path.unlink(missing_ok=True)


def clear_checkpoint() -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    for path in (
        CHECKPOINT_STATE_JSON,
        CHECKPOINT_INDIVIDUALS_JSONL,
        CHECKPOINT_ARTIFACTS_JSONL,
    ):
        path.unlink(missing_ok=True)


def load_checkpoint_state() -> dict | None:
    if not CHECKPOINT_STATE_JSON.exists():
        return None
    try:
        state = json.loads(CHECKPOINT_STATE_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if state.get("status") == "completed":
        return None
    return state


def load_checkpoint_rows() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    individuals: list[dict[str, str]] = []
    artifacts: list[dict[str, str]] = []
    if CHECKPOINT_INDIVIDUALS_JSONL.exists():
        for line in CHECKPOINT_INDIVIDUALS_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                individuals.append(json.loads(line))
    if CHECKPOINT_ARTIFACTS_JSONL.exists():
        for line in CHECKPOINT_ARTIFACTS_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                artifacts.append(json.loads(line))
    return individuals, artifacts


def write_checkpoint_state(state: dict) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_STATE_JSON.write_text(json.dumps(state, indent=2), encoding="utf-8")


def rewrite_checkpoint_rows(
    individual_rows: list[dict[str, str]],
    artifact_rows: list[dict[str, str]],
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_INDIVIDUALS_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in individual_rows)
        + ("\n" if individual_rows else ""),
        encoding="utf-8",
    )
    CHECKPOINT_ARTIFACTS_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in artifact_rows)
        + ("\n" if artifact_rows else ""),
        encoding="utf-8",
    )


def append_checkpoint_record(
    individual_row: dict[str, str],
    artifact_rows: list[dict[str, str]],
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT_INDIVIDUALS_JSONL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(individual_row, ensure_ascii=False) + "\n")
    if artifact_rows:
        with CHECKPOINT_ARTIFACTS_JSONL.open("a", encoding="utf-8") as handle:
            for row in artifact_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def trim_checkpoint_overlap(
    individual_rows: list[dict[str, str]],
    artifact_rows: list[dict[str, str]],
    overlap: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    if not individual_rows or overlap <= 0:
        return individual_rows, artifact_rows, 0
    remove_count = min(overlap, len(individual_rows))
    kept_individuals = individual_rows[:-remove_count]
    removed_urls = {
        row.get("metadata_page_url", "")
        for row in individual_rows[-remove_count:]
    }
    kept_artifacts = [
        row
        for row in artifact_rows
        if row.get("individual_metadata_page_url") not in removed_urls
    ]
    return kept_individuals, kept_artifacts, remove_count


def fetch_search_results(
    client: LocJsonClient,
    max_pages: int | None,
    on_page_complete: Callable[..., None] | None = None,
    resume_pages_completed: int = 0,
) -> int:
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    seen_ids: set[str] = set()
    next_url = SOURCE_SEARCH_URL
    page_number = 0
    reported_total: int | None = None

    if resume_pages_completed > 0:
        cached_through = last_cached_search_page()
        if cached_through > 0:
            seen_ids = load_seen_ids_from_cached_pages(cached_through)
            page_number = cached_through
            next_url = next_url_from_cached_page(cached_through)
            print(
                f"Resume: {len(seen_ids)} individuals already cached across "
                f"{cached_through} search pages; continuing pagination.",
                flush=True,
            )
            if not next_url:
                return len(seen_ids)

    while next_url:
        page_number += 1
        if max_pages is not None and page_number > max_pages:
            break
        cache_path = SEARCH_CACHE_DIR / f"page_{page_number:03d}.json"
        if on_page_complete:
            on_page_complete(page_number, completed=False)
        payload = client.fetch_json(
            next_url,
            cache_path=cache_path,
            cache_validator=is_valid_search_page_payload,
        )
        page_results = payload.get("results") or []
        pagination = payload.get("pagination") or {}
        if reported_total is None:
            total_value = pagination.get("total")
            if isinstance(total_value, int) and total_value > 0:
                reported_total = total_value
        new_count = 0
        for record in page_results:
            record_id = clean_text(record.get("id") or record.get("url") or "")
            if not record_id or record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            new_count += 1

        result_range = clean_text(pagination.get("results") or "")
        api_note = ""
        if reported_total is not None:
            api_note = f" (API reports total {reported_total}"
            if result_range:
                api_note += f", range {result_range}"
            api_note += ")"
        print(
            f"Search page {page_number}: {len(page_results)} returned, "
            f"{new_count} new, {len(seen_ids)} total unique{api_note}",
            flush=True,
        )
        if on_page_complete:
            on_page_complete(page_number, completed=True, total_records=len(seen_ids))
        if not page_results or new_count == 0:
            break
        per_page = pagination.get("perpage") or 150
        if len(page_results) < per_page:
            break
        next_url = pagination.get("next") or ""
        if not next_url:
            break

    return len(seen_ids)


def expand_segment_media(
    client: LocJsonClient,
    resource: dict,
    expand_segments: bool,
) -> list[dict]:
    segment_count = int(resource.get("segments") or resource.get("files") or 1)
    search_url = clean_text(resource.get("search", ""))
    if not expand_segments or segment_count <= 1 or not search_url:
        return [
            {
                "segment_number": "",
                "segment_title": "",
                "segment_count": segment_count,
                "image_urls": [],
                "resource": resource,
            }
        ]

    cache_path = SEGMENT_CACHE_DIR / f"{cache_name_for_url(search_url)}.json"
    payload = client.fetch_json(
        search_url,
        cache_path=cache_path,
        cache_validator=is_valid_segment_payload,
    )
    segments = payload.get("results") or []
    expanded: list[dict] = []
    for index, segment in enumerate(segments, start=1):
        expanded.append(
            {
                "segment_number": str(index),
                "segment_title": clean_text(segment.get("title", "")),
                "segment_count": segment_count,
                "image_urls": segment.get("image_url") or [],
                "resource": resource,
                "segment_record": segment,
            }
        )
    if expanded:
        return expanded
    return [
        {
            "segment_number": "",
            "segment_title": "",
            "segment_count": segment_count,
            "image_urls": [],
            "resource": resource,
        }
    ]


def build_individual_row(
    record: dict,
    artifact_rows: list[dict[str, str]],
    captured_at: str,
) -> dict[str, str]:
    item = record.get("item") or {}
    service_history = item.get("service_history") or []
    service_summary = service_history_summary(service_history)
    mets_urls = [
        url
        for url in (item.get("other_formats") or []) + (record.get("aka") or [])
        if isinstance(url, str) and "mets" in url.lower()
    ]
    artifact_ids = unique_preserve_order(
        [row.get("artifact_id", "") for row in artifact_rows]
    )
    artifact_sequences = unique_preserve_order(
        [row.get("artifact_sequence_in_collection", "") for row in artifact_rows]
    )

    return {
        "source_organization": SOURCE_ORGANIZATION,
        "source_collection": SOURCE_COLLECTION,
        "source_repository": clean_text(item.get("repository") or SOURCE_REPOSITORY),
        "source_search_url": SOURCE_SEARCH_URL,
        "captured_at": captured_at,
        "metadata_page_url": metadata_page_url_from_record(record),
        "loc_item_id": loc_item_id_from_record(record),
        "collection_number": collection_number_from_record(record),
        "collection_title": clean_text(record.get("title", "")),
        "veteran_name": veteran_name_from_record(record),
        "birth_place": clean_text(item.get("birth_place", "")),
        "gender": clean_text(item.get("gender") or join_values(record.get("subject_gender"))),
        "race": clean_text(item.get("race", "")),
        "state_of_residence_at_collection": clean_text(
            item.get("state_of_residence_at_time_of_collection_donation", "")
        ),
        "veteran_status": clean_text(item.get("status") or join_values(record.get("subject_status"))),
        "prisoner_of_war": clean_text(item.get("pow", "")),
        "rank": clean_text(item.get("rank") or join_values(record.get("subject_rank"))),
        "branch_of_service": clean_text(
            item.get("branch") or join_values(record.get("subject_branch"))
        ),
        "unit_of_service": clean_text(
            item.get("unit") or join_values(record.get("subject_unit"))
        ),
        "service_location": clean_text(item.get("service_location", "")),
        "war_or_conflicts": service_summary["war_or_conflicts"]
        or clean_text(item.get("war") or join_values(record.get("subject_conflict"))),
        "battles_campaigns": service_summary["battles_campaigns"]
        or join_values(record.get("subject_battles")),
        "dates_of_service": service_summary["dates_of_service"],
        "entrance_into_service": service_summary["entrance_into_service"]
        or join_values(record.get("subject_entrance")),
        "highest_rank": service_summary["highest_rank"],
        "location_of_service": service_summary["location_of_service"]
        or join_values(record.get("location_service")),
        "military_status": service_summary["military_status"],
        "service_history_json": json.dumps(service_history, ensure_ascii=False),
        "description": clean_text(join_values(record.get("description"))),
        "interview_collection_notes": clean_text(join_values(item.get("materials"))),
        "contributor_veteran": join_values(record.get("contributor_veteran")),
        "contributor_interviewer": join_values(record.get("contributor_interviewer")),
        "contributor_organization": join_values(record.get("contributor_organization")),
        "contributor_names": join_values(item.get("contributor_names")),
        "subject_headings": join_values(item.get("subject_headings")),
        "subjects": join_values(record.get("subject")),
        "locations_home": join_values(record.get("location_home")),
        "locations_service": join_values(record.get("location_service")),
        "original_formats": join_values(record.get("original_format")),
        "online_formats": join_values(record.get("online_format")),
        "mime_types": join_values(record.get("mime_type")),
        "digitized": str(record.get("digitized", "")),
        "access_restricted": str(record.get("access_restricted", "")),
        "date_indexed": clean_text(record.get("date", "")),
        "dates_indexed": join_values(record.get("dates")),
        "collection_mets_url": ARTIFACT_DELIMITER.join(dict.fromkeys(mets_urls)),
        "artifact_index_file": artifact_index_file_path(),
        "artifact_ids": ARTIFACT_DELIMITER.join(artifact_ids),
        "artifact_row_count": str(len(artifact_rows)),
        "artifact_count": str(len(artifact_sequences)),
        "transcript_mentioned_in_materials": str(
            materials_mention_transcript(record)
        ).lower(),
        "transcript_artifact_ids": ARTIFACT_DELIMITER.join(
            unique_preserve_order(
                [
                    row["artifact_id"]
                    for row in artifact_rows
                    if is_transcript_artifact_row(row)
                ]
            )
        ),
    }


def build_artifact_link_fields(record: dict, captured_at: str) -> dict[str, str]:
    return {
        "individual_index_file": individual_index_file_path(),
        "individual_metadata_page_url": metadata_page_url_from_record(record),
        "collection_number": collection_number_from_record(record),
        "veteran_name": veteran_name_from_record(record),
        "loc_item_id": loc_item_id_from_record(record),
        "source_organization": SOURCE_ORGANIZATION,
        "source_collection": SOURCE_COLLECTION,
        "source_search_url": SOURCE_SEARCH_URL,
        "captured_at": captured_at,
    }


def build_artifact_rows(
    record: dict,
    client: LocJsonClient,
    expand_segments: bool,
    captured_at: str,
) -> list[dict[str, str]]:
    link_fields = build_artifact_link_fields(record, captured_at)
    collection_number = link_fields["collection_number"]
    resources = record.get("resources") or []
    rows: list[dict[str, str]] = []

    for sequence, resource in enumerate(resources, start=1):
        for segment_info in expand_segment_media(client, resource, expand_segments):
            resource_data = segment_info["resource"]
            image_urls = segment_info.get("image_urls") or []
            segment_record = segment_info.get("segment_record") or {}
            segment_number = clean_text(segment_info.get("segment_number", ""))
            if segment_record.get("url"):
                resource_page_url = clean_text(segment_record.get("url", ""))
            else:
                resource_page_url = clean_text(resource_data.get("url", ""))

            urls = media_urls_from_resource(resource_data)
            if image_urls:
                best_image = best_image_url(image_urls)
                if best_image:
                    urls["image"] = urls.get("image") or best_image

            row = dict(link_fields)
            row.update(
                {
                    "artifact_id": build_artifact_id(
                        collection_number,
                        sequence,
                        segment_number,
                    ),
                    "artifact_sequence_in_collection": str(sequence),
                    "artifact_resource_type": clean_text(resource_data.get("type", "")),
                    "artifact_caption": clean_text(resource_data.get("caption", "")),
                    "artifact_resource_label": clean_text(
                        resource_data.get("resource_label", "")
                    ),
                    "artifact_duration_seconds": str(resource_data.get("duration", "")),
                    "artifact_width": str(resource_data.get("width", "")),
                    "artifact_height": str(resource_data.get("height", "")),
                    "artifact_segment_number": segment_number,
                    "artifact_segment_title": clean_text(
                        segment_info.get("segment_title", "")
                    ),
                    "artifact_segment_count": str(segment_info.get("segment_count", "")),
                    "artifact_resource_page_url": resource_page_url,
                    "artifact_file_url_primary": choose_primary_media_url(urls, image_urls),
                    "artifact_file_url_mp4": urls.get("video", ""),
                    "artifact_file_url_mp3": urls.get("audio", ""),
                    "artifact_file_url_pdf": urls.get("pdf", ""),
                    "artifact_file_url_image": urls.get("image", "")
                    or best_image_url(image_urls),
                    "artifact_file_url_stream": urls.get("video_stream", ""),
                    "artifact_file_url_other": urls.get("other", ""),
                    "artifact_all_file_urls": flatten_urls(urls, image_urls),
                    **empty_transcript_fields(),
                    "raw_resource_json": json.dumps(resource_data, ensure_ascii=False),
                }
            )
            rows.append(row)

    if not rows:
        row = dict(link_fields)
        row.update(
            {
                "artifact_id": build_artifact_id(collection_number, 0),
                "artifact_sequence_in_collection": "",
                "artifact_resource_type": "",
                "artifact_caption": "",
                "artifact_resource_label": "",
                "artifact_duration_seconds": "",
                "artifact_width": "",
                "artifact_height": "",
                "artifact_segment_number": "",
                "artifact_segment_title": "",
                "artifact_segment_count": "",
                "artifact_resource_page_url": "",
                "artifact_file_url_primary": best_image_url(record.get("image_url") or []),
                "artifact_file_url_mp4": "",
                "artifact_file_url_mp3": "",
                "artifact_file_url_pdf": "",
                "artifact_file_url_image": best_image_url(record.get("image_url") or []),
                "artifact_file_url_stream": "",
                "artifact_file_url_other": "",
                "artifact_all_file_urls": best_image_url(record.get("image_url") or []),
                **empty_transcript_fields(),
                "raw_resource_json": "",
            }
        )
        rows.append(row)
    return annotate_transcript_links(rows)


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ordered


def write_artifact_csv(rows: list[dict[str, str]]) -> None:
    OUTPUT_CSV_ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV_ARTIFACTS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ARTIFACT_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_individual_csv(rows: list[dict[str, str]]) -> None:
    OUTPUT_CSV_INDIVIDUALS.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV_INDIVIDUALS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDIVIDUAL_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_csv(rows: list[dict[str, str]]) -> None:
    write_artifact_csv(rows)


def write_veteran_csv(rows: list[dict[str, str]]) -> None:
    write_individual_csv(rows)


def write_output_files(
    individual_rows: list[dict[str, str]],
    artifact_rows: list[dict[str, str]],
    captured_at: str,
    *,
    partial: bool,
    total_expected: int | None = None,
) -> None:
    write_artifact_csv(artifact_rows)
    write_individual_csv(individual_rows)
    manifest = build_manifest(
        artifact_rows,
        individual_rows,
        captured_at,
        status="partial" if partial else "complete",
        total_expected=total_expected,
    )
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def maybe_write_csv_checkpoint(
    index: int,
    interval: int,
    individual_rows: list[dict[str, str]],
    artifact_rows: list[dict[str, str]],
    captured_at: str,
    total_expected: int,
    state: dict,
) -> None:
    if interval <= 0 or index % interval != 0:
        return
    write_output_files(
        individual_rows,
        artifact_rows,
        captured_at,
        partial=True,
        total_expected=total_expected,
    )
    state["last_csv_checkpoint_index"] = index
    write_checkpoint_state(state)
    print(
        f"\nPartial CSV checkpoint: {index} individuals, "
        f"{len(artifact_rows)} artifact rows written to "
        f"{OUTPUT_CSV_INDIVIDUALS.relative_to(PROJECT_ROOT)} and "
        f"{OUTPUT_CSV_ARTIFACTS.relative_to(PROJECT_ROOT)} "
        f"(manifest status=partial).",
        flush=True,
    )


def build_manifest(
    artifact_rows: list[dict[str, str]],
    individual_rows: list[dict[str, str]],
    captured_at: str,
    *,
    status: str = "complete",
    total_expected: int | None = None,
) -> dict:
    artifact_types: dict[str, int] = {}
    for row in artifact_rows:
        artifact_type = row.get("artifact_resource_type") or "unknown"
        artifact_types[artifact_type] = artifact_types.get(artifact_type, 0) + 1

    manifest = {
        "source_organization": SOURCE_ORGANIZATION,
        "source_collection": SOURCE_COLLECTION,
        "source_search_url": SOURCE_SEARCH_URL,
        "captured_at": captured_at,
        "linking": {
            "individual_primary_key": "metadata_page_url",
            "artifact_primary_key": "artifact_id",
            "artifact_foreign_key": "individual_metadata_page_url",
            "individual_to_artifacts": "artifact_ids",
            "individual_artifact_index_file": "artifact_index_file",
            "artifact_individual_index_file": "individual_index_file",
            "artifact_id_format": "{collection_number}:{sequence}[:{segment}]",
            "artifact_id_delimiter": ARTIFACT_DELIMITER,
        },
        "source_urls": {
            "search_results_page": "source_search_url",
            "individual_metadata_page": "metadata_page_url",
            "artifact_viewer_page": "artifact_resource_page_url",
            "artifact_media_files": [
                "artifact_file_url_primary",
                "artifact_file_url_mp4",
                "artifact_file_url_mp3",
                "artifact_file_url_pdf",
                "artifact_file_url_image",
                "artifact_file_url_stream",
                "artifact_all_file_urls",
            ],
            "transcript_files": "transcript_file_url",
        },
        "transcript_fields": {
            "individual_transcript_mentioned_in_materials": (
                "transcript_mentioned_in_materials"
            ),
            "individual_transcript_artifact_ids": "transcript_artifact_ids",
            "artifact_transcript_available": "transcript_available",
            "artifact_transcript_artifact_id": "transcript_artifact_id",
            "artifact_transcript_file_url": "transcript_file_url",
        },
        "output_csv_individuals": str(OUTPUT_CSV_INDIVIDUALS.relative_to(PROJECT_ROOT)),
        "output_csv_artifacts": str(OUTPUT_CSV_ARTIFACTS.relative_to(PROJECT_ROOT)),
        "cache_dir": str(CACHE_DIR.relative_to(PROJECT_ROOT)),
        "request_delay_sec": DEFAULT_DELAY_SEC,
        "status": status,
        "total_individuals": len(individual_rows),
        "total_artifact_rows": len(artifact_rows),
        "artifact_types": artifact_types,
    }
    if total_expected is not None:
        manifest["total_expected_individuals"] = total_expected
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract LOC Veterans History Project WWII personal narratives."
    )
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=DEFAULT_DELAY_SEC,
        help="Minimum delay between HTTP requests.",
    )
    parser.add_argument(
        "--jitter-sec",
        type=float,
        default=DEFAULT_JITTER_SEC,
        help="Random extra delay added to each request.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limit search pagination for testing.",
    )
    parser.add_argument(
        "--expand-segments",
        action="store_true",
        help=(
            "Fetch segment search pages for multi-page artifacts to capture "
            "per-page image URLs (many extra requests)."
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any checkpoint and start a new extraction run.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint when one exists (default if checkpoint found).",
    )
    parser.add_argument(
        "--resume-overlap",
        type=int,
        default=DEFAULT_RESUME_OVERLAP,
        help=(
            "When resuming, re-process this many individuals immediately before "
            "the last checkpoint in case cached data was truncated."
        ),
    )
    parser.add_argument(
        "--csv-checkpoint-every",
        type=int,
        default=DEFAULT_CSV_CHECKPOINT_EVERY,
        help=(
            "During processing, rewrite CSV outputs every N individuals "
            "(0 disables periodic writes)."
        ),
    )
    return parser.parse_args()


def should_resume(args: argparse.Namespace) -> bool:
    if args.fresh:
        return False
    if args.resume:
        return True
    return load_checkpoint_state() is not None


def init_run_state(args: argparse.Namespace, resume: bool) -> dict:
    if resume:
        state = load_checkpoint_state()
        if state:
            return state
    captured_at = capture_timestamp()
    clear_checkpoint()
    state = {
        "status": "in_progress",
        "phase": "search",
        "captured_at": captured_at,
        "expand_segments": args.expand_segments,
        "max_pages": args.max_pages,
        "resume_overlap": args.resume_overlap,
        "csv_checkpoint_every": args.csv_checkpoint_every,
        "last_csv_checkpoint_index": 0,
        "search_pages_completed": 0,
        "last_search_page_attempted": 0,
        "last_completed_index": 0,
        "total_records": 0,
        "error": "",
    }
    write_checkpoint_state(state)
    return state


def main() -> None:
    args = parse_args()
    resume = should_resume(args)
    state = init_run_state(args, resume)
    client = LocJsonClient(delay_sec=args.delay_sec, jitter_sec=args.jitter_sec)

    if resume and state.get("last_search_page_attempted", 0) > 0:
        invalidate_from = max(1, state["last_search_page_attempted"])
        if state.get("search_pages_completed", 0) < invalidate_from:
            invalidate_from = max(1, state.get("search_pages_completed", 0))
        invalidate_search_cache_from_page(invalidate_from)
        print(
            f"Resume: invalidated search cache from page {invalidate_from} "
            "in case the last download was truncated.",
            flush=True,
        )

    def on_search_page(
        page_number: int,
        completed: bool,
        total_records: int = 0,
    ) -> None:
        state["phase"] = "search"
        state["last_search_page_attempted"] = page_number
        if completed:
            state["search_pages_completed"] = page_number
            state["total_records"] = total_records
        write_checkpoint_state(state)

    try:
        state["error"] = ""
        write_checkpoint_state(state)
        total_records = fetch_search_results(
            client,
            max_pages=args.max_pages,
            on_page_complete=on_search_page,
            resume_pages_completed=state.get("search_pages_completed", 0) if resume else 0,
        )
        state["phase"] = "processing"
        state["total_records"] = total_records
        write_checkpoint_state(state)
        print(
            f"\nSearch complete: {total_records} individuals found. "
            "Processing records from cached search pages "
            "(no extra API calls unless --expand-segments)...",
            flush=True,
        )

        captured_at = state["captured_at"]
        individual_rows, artifact_rows = load_checkpoint_rows()
        if resume and individual_rows:
            trimmed_individuals, trimmed_artifacts, removed = trim_checkpoint_overlap(
                individual_rows,
                artifact_rows,
                state.get("resume_overlap", args.resume_overlap),
            )
            if removed:
                individual_rows = trimmed_individuals
                artifact_rows = trimmed_artifacts
                rewrite_checkpoint_rows(individual_rows, artifact_rows)
                print(
                    f"Resume: re-processing last {removed} individuals "
                    f"after overlap trim ({len(individual_rows)} kept).",
                    flush=True,
                )

        start_index = len(individual_rows) + 1
        overlap = state.get("resume_overlap", args.resume_overlap)
        if start_index > total_records:
            print("Checkpoint already contains all records; finalizing outputs.", flush=True)
        else:
            if start_index > 1:
                print(
                    f"Resume: continuing at record {start_index}/{total_records}.",
                    flush=True,
                )

            for index, record in enumerate(iter_search_records(args.max_pages), start=1):
                if index < start_index:
                    continue
                if resume and index < start_index + overlap:
                    invalidate_segment_caches_for_record(record)

                record_artifacts = build_artifact_rows(
                    record,
                    client,
                    expand_segments=args.expand_segments,
                    captured_at=captured_at,
                )
                individual_row = build_individual_row(
                    record, record_artifacts, captured_at
                )
                individual_rows.append(individual_row)
                artifact_rows.extend(record_artifacts)
                append_checkpoint_record(individual_row, record_artifacts)

                state["phase"] = "processing"
                state["last_completed_index"] = index
                state["error"] = ""
                write_checkpoint_state(state)
                print(
                    f"[{index}/{total_records}] {veteran_name_from_record(record)}: "
                    f"{len(record_artifacts)} artifact rows",
                    flush=True,
                )
                csv_checkpoint_every = state.get(
                    "csv_checkpoint_every", args.csv_checkpoint_every
                )
                maybe_write_csv_checkpoint(
                    index,
                    csv_checkpoint_every,
                    individual_rows,
                    artifact_rows,
                    captured_at,
                    total_records,
                    state,
                )

        print("\nWriting output files...", flush=True)
        write_output_files(
            individual_rows,
            artifact_rows,
            captured_at,
            partial=False,
            total_expected=total_records,
        )
        print(
            f"Wrote {len(individual_rows)} individual rows to "
            f"{OUTPUT_CSV_INDIVIDUALS.relative_to(PROJECT_ROOT)}",
            flush=True,
        )
        print(
            f"Wrote {len(artifact_rows)} artifact rows to "
            f"{OUTPUT_CSV_ARTIFACTS.relative_to(PROJECT_ROOT)}",
            flush=True,
        )
        print(
            f"Wrote manifest to {MANIFEST_JSON.relative_to(PROJECT_ROOT)}",
            flush=True,
        )

        state["status"] = "completed"
        state["phase"] = "completed"
        state["last_completed_index"] = len(individual_rows)
        state["error"] = ""
        write_checkpoint_state(state)

        print(
            f"\nExtraction complete. captured_at={captured_at}",
            flush=True,
        )
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as exc:
        state["status"] = "in_progress"
        state["error"] = str(exc)
        partial_individuals, partial_artifacts = load_checkpoint_rows()
        if partial_individuals:
            try:
                write_output_files(
                    partial_individuals,
                    partial_artifacts,
                    state.get("captured_at", capture_timestamp()),
                    partial=True,
                    total_expected=state.get("total_records") or None,
                )
                state["last_csv_checkpoint_index"] = len(partial_individuals)
                print(
                    f"\nWrote partial CSVs with {len(partial_individuals)} individuals "
                    f"and {len(partial_artifacts)} artifact rows before exit.",
                    flush=True,
                )
            except Exception as write_exc:
                print(
                    f"\nFailed to write partial CSVs: {write_exc}",
                    flush=True,
                )
        write_checkpoint_state(state)
        print(
            "\nExtraction failed. Checkpoint saved; re-run the same command to resume.",
            flush=True,
        )
        raise


if __name__ == "__main__":
    main()