"""Regression for the live Phase-3 systemic block (run 027c): the bibliography resolver wrote
`local_source_path` (not in the schema, additionalProperties:false -> blocked) and statuses
(held_unprocessed / not_citation / reference_identified) absent from the search_status enum ->
~15/17 records blocked, losing enrichment. Fix: field renamed to download_path + enum widened.
"""

import jsonschema
import pytest

from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA as SCHEMA


def _valid_base():
    return {
        "BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "title": "V Corps Operations in the ETO",
        "citation": {},
        "_schema_version": "2.28",
    }


def test_download_path_is_allowed_local_source_path_was_not():
    rec = _valid_base()
    rec["download_path"] = "/holdings/B405.pdf"
    jsonschema.Draft7Validator(SCHEMA).validate(rec)  # must not raise

    rec2 = _valid_base()
    rec2["local_source_path"] = "/holdings/B405.pdf"  # the OLD (blocked) field
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(SCHEMA).validate(rec2)


@pytest.mark.parametrize(
    "status",
    [
        "resolved",
        "not_found",
        "pending",
        "unresolved",
        "held_unprocessed",
        "not_citation",
        "reference_identified",
    ],
)
def test_all_resolver_statuses_validate(status):
    rec = _valid_base()
    rec["search_status"] = status
    jsonschema.Draft7Validator(SCHEMA).validate(rec)  # must not raise


def test_bogus_status_still_rejected():
    rec = _valid_base()
    rec["search_status"] = "definitely_not_a_status"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(SCHEMA).validate(rec)
