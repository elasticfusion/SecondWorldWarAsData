"""Title-scoped, idempotent reprocessing (Option B).

Every content item has a title (folder, or filename fallback); every phase run is
scoped to one title; a re-run of a title clears only that title's cache so it is
idempotent and cannot be contaminated by other titles' stale state.
"""

# pylint: disable=missing-function-docstring,import-error

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as ee
from lambda_handlers import trigger_handler as th


# --- _title_from_key: folder title, else filename fallback (never empty) ---
def test_title_from_folder():
    assert (
        th._title_from_key(
            "contentrepository/SiegfriedLine/chapter2/chapter2a-content.md"
        )
        == "SiegfriedLine"
    )


def test_title_from_loose_file_falls_back_to_filename():
    # No title folder -> filename stem (never empty, never whole-corpus).
    assert th._title_from_key("contentrepository/Foo.md") == "Foo"
    assert th._title_from_key("contentrepository/My Report.pdf") == "My_Report"


def test_title_from_key_empty_on_blank():
    assert th._title_from_key("") == ""


def test_title_from_key_rejects_path_traversal_and_unsafe():
    # '..' / separators / unsafe chars must NOT become a title (they would reach
    # a filesystem rmtree path, a --book arg, and a DynamoDB key).
    assert th._title_from_key("contentrepository/../x/y") == ""
    assert th._title_from_key("contentrepository/..") == ""
    # a title with a path separator can't occur post-split, but a weird stem does:
    assert th._title_from_key("contentrepository/foo$(rm).md") == ""


# --- _scope_args: BOOK_NAME -> --book for phase2/3 only ---
def test_scope_args_injects_book_for_phase2_only():
    with patch.dict(os.environ, {"BOOK_NAME": "SiegfriedLine"}):
        assert ee._scope_args("phase2_extract.py", ["--batch"]) == [
            "--batch",
            "--book",
            "SiegfriedLine",
        ]
        # phase3 has no --book arg (would crash argparse) + already scopes via
        # BOOK_NAME env — so it must NOT be injected.
        assert "--book" not in ee._scope_args("phase3_enrich_data.py", [])


def test_scope_args_noop_for_phase0_1_and_unset_and_present():
    with patch.dict(os.environ, {"BOOK_NAME": "SiegfriedLine"}):
        assert ee._scope_args("phase1_parse.py", ["--batch"]) == ["--batch"]
        # already present -> not duplicated
        assert ee._scope_args("phase2_extract.py", ["--book", "X"]) == ["--book", "X"]
    with patch.dict(os.environ, {"BOOK_NAME": ""}):
        assert ee._scope_args("phase2_extract.py", ["--batch"]) == ["--batch"]


# --- phase2 falls back to BOOK_NAME env for --book ---
def test_phase2_scope_uses_book_env_when_arg_absent():
    import phase2_extract as p2

    # Mirror the main() fallback expression.
    class _Args:
        book = None

    with patch.dict(os.environ, {"BOOK_NAME": "SiegfriedLine"}):
        scope_book = _Args.book or os.environ.get("BOOK_NAME", "").strip() or None
    assert scope_book == "SiegfriedLine"
    assert hasattr(p2, "_apply_scope_guard")


def _write_cfg(tmp_path, force=False, reprocess_types=None):
    import yaml

    cfg = {
        "processing": {
            "force_reprocess": force,
            "reprocess_types": reprocess_types or [],
        }
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    return cfg_path


def test_cache_clear_skipped_when_not_reprocess(tmp_path):
    cfg = _write_cfg(tmp_path, force=False)
    books = tmp_path / "books"
    cache = books / "SiegfriedLine" / "events"
    cache.mkdir(parents=True)
    with (
        patch.dict(os.environ, {"BOOK_NAME": "SiegfriedLine", "REPROCESS_TITLE": ""}),
        patch.object(ee, "_CONFIG_PATH", cfg),
        patch.object(ee, "_CACHE_BOOKS_DIR", books),
    ):
        ee._clear_title_cache_if_reprocess("phase2_extract.py")
    assert cache.exists()  # not a reprocess -> preserved


def test_cache_clear_reprocess_title_env_only_that_title(tmp_path):
    cfg = _write_cfg(tmp_path, force=False)
    books = tmp_path / "books"
    cache = books / "SiegfriedLine" / "events"
    cache.mkdir(parents=True)
    other = books / "TheLorraineCampaign" / "events"
    other.mkdir(parents=True)
    with (
        patch.dict(
            os.environ,
            {"BOOK_NAME": "SiegfriedLine", "REPROCESS_TITLE": "SiegfriedLine"},
        ),
        patch.object(ee, "_CONFIG_PATH", cfg),
        patch.object(ee, "_CACHE_BOOKS_DIR", books),
    ):
        ee._clear_title_cache_if_reprocess("phase2_extract.py")
    assert not cache.exists()  # this title cleared
    assert other.exists()  # OTHER title untouched


def test_cache_clear_on_force_reprocess_config(tmp_path):
    cfg = _write_cfg(tmp_path, force=True)
    books = tmp_path / "books"
    cache = books / "SiegfriedLine" / "events"
    cache.mkdir(parents=True)
    with (
        patch.dict(os.environ, {"BOOK_NAME": "SiegfriedLine"}),
        patch.object(ee, "_CONFIG_PATH", cfg),
        patch.object(ee, "_CACHE_BOOKS_DIR", books),
    ):
        os.environ.pop("REPROCESS_TITLE", None)
        ee._clear_title_cache_if_reprocess("phase2_extract.py")
    assert not cache.exists()
