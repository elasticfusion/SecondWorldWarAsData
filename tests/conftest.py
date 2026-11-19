"""Shared pytest fixtures for testing."""

import os

# The import step's title-page metadata request (src/ingestion/book_metadata) is ON in production;
# in tests it is OFF unless a test turns it on and injects a fake client - no test may reach Grok
# (a developer's .env key would otherwise make real, billed calls).
os.environ.setdefault("BOOK_METADATA", "0")
# Wikimedia pacing (src/utils/wikimedia_client._pace) would sleep 1 s per mocked request
os.environ.setdefault("WIKIMEDIA_MIN_INTERVAL_SECONDS", "0")
# Phase 2's place/unit Grokipedia + Wikipedia lookups (batch_parallel._enrich_in_place) reach the
# network: off unless a test turns them on and mocks them
os.environ.setdefault("PHASE2_REFERENCE_LOOKUPS", "0")
# Phase-0 translation is ON by default (production); tests that exercise it set it themselves
os.environ.setdefault("PHASE0_TRANSLATE", "0")
# small batches are answered live in production (batch.min_requests); tests batch everything
os.environ.setdefault("BATCH_MIN_REQUESTS", "0")

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock

import pytest


@pytest.fixture
def mock_grok_client():
    """Mock GrokClient for testing without API calls."""
    client = Mock()
    client.extract_structured = Mock()
    client.extract_json = Mock()
    client.chat_completion = Mock()
    return client


@pytest.fixture
def sample_person_data() -> Dict[str, Any]:
    """Sample person data for testing."""
    return {
        "PersonID": "01H8XYZI1AB123CD456EF789GH",
        "name": "Dwight D. Eisenhower",
        "source_language": "English",
        "aliases": ["Ike"],
        "biographical_profile": {
            "birth_date": "1890-10-14",
            "nationality": "American",
            "role_type": "military_leader",
        },
        "event_mentions": [
            {
                "MentionID": "01H8XYZ...",
                "Event_Name": "D-Day",
                "EventID": "01H8XYZ...",
                "position_at_event": "Supreme Commander",
            }
        ],
    }


@pytest.fixture
def sample_parsed_chapter(tmp_path) -> Path:
    """Sample parsed chapter data as a file."""
    from pathlib import Path
    import json

    data = {
        "book": "Breakout and Pursuit",
        "chapter": 1,
        "chapter_title": "The Allies",
        "author": "Martin Blumenson",
        "series": "United States Army in World War II",
        "paragraphs": [
            {
                "paragraph_number": 1,
                "text": "General Eisenhower commanded the Allied forces.",
            }
        ],
    }

    event_data = {
        "Chapter": "The Allies",
        "Event": {
            "EventID": "01TEST123",
            "Sub-events": [
                {
                    "Sub-eventID": "01TESTSUB",
                    "Sub-event_summary": "Test event",
                    "Sub-event_fulltext": {
                        "1": "General Eisenhower commanded the Allied forces."
                    },
                }
            ],
        },
    }

    # Create parsed file
    parsed_file = tmp_path / "chapter1-parsed.json"
    with open(parsed_file, "w") as f:
        json.dump(data, f)

    # Create event file
    event_file = tmp_path / "chapter1-event.json"
    with open(event_file, "w") as f:
        json.dump(event_data, f)

    return event_file


@pytest.fixture
def temp_output_dir(tmp_path):
    """Temporary output directory for tests."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "people").mkdir()
    (output_dir / "places").mkdir()
    (output_dir / "events").mkdir()
    return output_dir


@pytest.fixture
def mock_config():
    """Mock configuration for testing."""
    return {
        "paths": {
            "output_root": "output",
            "cache_root": "cache",
        },
        "api": {
            "grok": {
                "max_retries": 3,
                "timeout": 60,
            }
        },
        "weather": {"enabled": True, "fetch_api_data": False},
        "maps": {"enabled": False},
        "external_maps": {"enabled": False},
    }


@pytest.fixture(autouse=True)
def _no_leftover_grok_stop(monkeypatch):
    """A run-stopping Grok/service condition (src/utils/grok_models.stop_run) is process-global:
    one test's stop must not make later tests' Grok calls raise (task 1.2e/1.2f)."""
    from src.utils import grok_models

    monkeypatch.setattr(grok_models, "_stopped", {})


@pytest.fixture(autouse=True)
def _no_live_xai_balance(monkeypatch):
    """src/grok_client loads .env, which may hold the real xAI management key: no test may read
    the live credit balance (task 1.1c). Tests that need a balance pass keys and a fake session.
    """
    from src.utils import grok_balance

    monkeypatch.delenv(grok_balance.MANAGEMENT_KEY_ENV, raising=False)
    monkeypatch.setattr(grok_balance, "_team", {})
    monkeypatch.setattr(grok_balance, "_management_key", {})
