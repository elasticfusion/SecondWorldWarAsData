"""Source sections live per book under content/<book>/source_section/: the registry and the
referential audit must read them there (reading output/source_section/ found none, so every
images.SourceSectionID looked dangling - 37/37 on Cross-Channel Attack)."""

import json

from src.schemas.entity_registry import ENTITY_REGISTRY
from src.utils import referential_integrity as R
from src.utils.entity_records import entity_files
from src.utils.storage import LocalStorage

SID = "01M7GFE9WDTBRMW7CJADT6K67F"


def _tree(tmp_path):
    ss = tmp_path / "content" / "B" / "source_section"
    ss.mkdir(parents=True)
    (ss / f"{SID}.json").write_text(json.dumps({"SourceSectionID": SID}))
    (tmp_path / "content" / "B" / "chapter1-event.json").write_text("{}")
    return tmp_path


def test_the_registry_finds_the_per_book_source_sections(tmp_path):
    spec = next(s for s in ENTITY_REGISTRY if s.name == "source_section")
    assert [f.name for f in entity_files(_tree(tmp_path), spec.output_glob)] == [
        f"{SID}.json"
    ]


def test_the_referential_audit_reads_them(tmp_path):
    files = R._record_files(LocalStorage(_tree(tmp_path)), "source_section")
    assert files == [f"content/B/source_section/{SID}.json"]


def test_an_s3_style_recursive_listing_is_filtered_to_source_sections():
    class S3Like:
        def list_files(self, prefix, pattern="*.json"):
            # basename match, recursive (S3Storage); the nested pattern matches nothing
            if "/" in pattern:
                return []
            return [
                "content/B/chapter1-event.json",
                f"content/B/source_section/{SID}.json",
            ]

    assert R._record_files(S3Like(), "source_section") == [
        f"content/B/source_section/{SID}.json"
    ]
