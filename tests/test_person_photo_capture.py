"""Wikipedia person portrait is downloaded + preserved to storage (the photo itself,
not just the URL)."""

import src.extraction.enrich_biographies as EB


class _FakeStorage:
    def __init__(self):
        self.saved = {}

    def exists(self, p):
        return p in self.saved

    def write_bytes(self, p, d):
        self.saved[p] = d


class _Resp:
    status_code = 200
    content = b"\xff\xd8\xffREALJPEGBYTES"
    headers = {"Content-Type": "image/jpeg"}

    def iter_content(self, n):  # safe_get streams the body
        return [self.content]


class _Session:
    def get(self, url, **kw):
        return _Resp()


def test_store_wikipedia_image_preserves_photo_bytes(monkeypatch):
    fs = _FakeStorage()
    monkeypatch.setattr(EB, "_award_storage", lambda: fs)
    monkeypatch.setattr(EB, "_fetch_image_license", lambda fn: "Public domain")
    # _preserve_person_photo now routes through url_hygiene.safe_get (SSRF-safe). Mock the
    # resolve-check (public host, deterministic offline) + the underlying requests.get it calls.
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = _Resp()
    resp._content = _Resp.content  # safe_get materializes the capped body here
    monkeypatch.setattr("requests.Session.get", lambda *a, **k: resp)

    resp_json = {
        "query": {
            "pages": {
                "123": {
                    "original": {"source": "https://upload.wikimedia.org/x/P.jpg"},
                    "pageimage": "P.jpg",
                }
            }
        }
    }
    EB._store_wikipedia_image("Jane Roe", resp_json)
    info = EB.get_wikipedia_image("Jane Roe")
    assert info["url"].endswith("P.jpg")
    assert info["license"] == "Public domain"
    # the photo BYTES were preserved under person_photos/<slug>/
    assert "preserved_path" in info
    assert info["preserved_path"].startswith("person_photos/Jane_Roe/")
    assert info["preserved_path"].endswith(".jpg")
    assert fs.saved[info["preserved_path"]] == _Resp.content


def test_store_wikipedia_image_noop_without_image(monkeypatch):
    fs = _FakeStorage()
    monkeypatch.setattr(EB, "_award_storage", lambda: fs)
    EB._store_wikipedia_image("No Image", {"query": {"pages": {"-1": {}}}})
    assert EB.get_wikipedia_image("No Image") is None
    assert not fs.saved
