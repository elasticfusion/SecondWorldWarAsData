"""§1.0c NIT: openserp must download the VISION-APPROVED image, not a re-extracted map_images[0]."""

from pathlib import Path
from unittest import mock


def test_download_map_image_uses_approved_url(tmp_path, monkeypatch):
    import src.extraction.openserp_maps as OM

    captured = {}
    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes",
        lambda u: (captured.setdefault("url", u), (b"\xff\xd8\xffJPG", "jpg"))[1],
    )
    # _extract_map_images must NOT be consulted when an approved url is given
    monkeypatch.setattr(
        "src.extraction.search_external_maps._extract_map_images",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not re-extract")),
    )
    ok, path = OM._download_map_image(
        "https://page/x",
        "01MAP0",
        str(tmp_path),
        10,
        30,
        approved_image_url="https://cdn/approved-map.png",
    )
    assert ok is True
    assert (
        captured["url"] == "https://cdn/approved-map.png"
    )  # the APPROVED image fetched


def test_verify_single_image_returns_approved_url(monkeypatch):
    import src.extraction.search_external_maps as SEM

    monkeypatch.setattr("src.utils.url_hygiene.clean_url", lambda u: u)
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda h: ["93.184.216.34"]
    )
    gc = mock.Mock()
    gc.extract_json_with_image.return_value = {
        "is_relevant": True,
        "is_government_map": True,
        "reason": "ok",
    }
    out = SEM._verify_single_image(
        {"url": "https://cdn/m.png", "alt": "", "caption": ""},
        "Caen",
        "1944",
        "ctx",
        "title",
        gc,
        30,
    )
    # 4-tuple now (map-as-source P1): (is_relevant, is_government, approved_url, depicted).
    # depicted is None here because the mocked result omits it (backward-compatible).
    assert out == (True, True, "https://cdn/m.png", None)


def test_verify_single_image_passes_depicted_through(monkeypatch):
    import src.extraction.search_external_maps as SEM

    monkeypatch.setattr("src.utils.url_hygiene.clean_url", lambda u: u)
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda h: ["93.184.216.34"]
    )
    gc = mock.Mock()
    depicted = {
        "units": ["12th SS Panzer"],
        "places": ["Caen"],
        "description": "map of Caen",
    }
    gc.extract_json_with_image.return_value = {
        "is_relevant": True,
        "is_government_map": False,
        "reason": "ok",
        "depicted": depicted,
    }
    out = SEM._verify_single_image(
        {"url": "https://cdn/m.png", "alt": "", "caption": ""},
        "Caen",
        "1944",
        "ctx",
        "title",
        gc,
        30,
    )
    assert out == (True, False, "https://cdn/m.png", depicted)
