"""DRY P1.9 guard: the byte-identical Wikimedia-Commons license lookup that equipment_wikipedia and
groups_wikipedia each duplicated is now the shared _wikipedia_common.fetch_commons_license. These
tests pin the shared helper's parse behavior + that both modules' _fetch_license delegate to it.
"""

from unittest import mock

import src.enrichment._wikipedia_common as wc


def _fake_resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


def test_fetch_commons_license_parses_shortname(monkeypatch):
    payload = {
        "query": {
            "pages": {
                "123": {
                    "imageinfo": [
                        {"extmetadata": {"LicenseShortName": {"value": "CC BY-SA 3.0"}}}
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(
        "src.utils.fetch_provenance.recorded_fetch",
        lambda *a, **k: _fake_resp(payload),
    )
    assert wc.fetch_commons_license("Tiger_I.jpg") == "CC BY-SA 3.0"


def test_fetch_commons_license_fail_safe(monkeypatch):
    # Any error -> None (best-effort; never breaks enrichment).
    def boom(*a, **k):
        raise RuntimeError("commons down")

    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", boom)
    assert wc.fetch_commons_license("X.jpg") is None


def test_both_modules_delegate_to_shared_license(monkeypatch):
    import src.enrichment.equipment_wikipedia as eq
    import src.enrichment.groups_wikipedia as gr

    calls = []
    monkeypatch.setattr(
        wc, "fetch_commons_license", lambda fn: calls.append(fn) or "CC0"
    )
    assert eq._fetch_license("Panther.jpg") == "CC0"
    assert gr._fetch_license("1st_Infantry_Division_SSI.svg") == "CC0"
    assert calls == ["Panther.jpg", "1st_Infantry_Division_SSI.svg"]
