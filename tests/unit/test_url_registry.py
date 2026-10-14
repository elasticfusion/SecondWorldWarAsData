"""DynamoDB URL registry: clean-URL gate, register/lookup, TTL expiry."""

import json
import time
from unittest.mock import MagicMock

import src.utils.url_registry as reg


def _fake_table():
    store = {}
    t = MagicMock()
    t.put_item.side_effect = lambda Item: store.__setitem__(Item["cache_key"], Item)
    t.get_item.side_effect = lambda Key: (
        {"Item": store[Key["cache_key"]]} if Key["cache_key"] in store else {}
    )
    t._store = store
    return t


def test_register_rejects_non_clean_url(monkeypatch):
    t = _fake_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    # SSRF + bad scheme are rejected, nothing stored
    assert reg.register("http://169.254.169.254/x", True, "ctx", "grok") is None
    assert reg.register("javascript:alert(1)", True, "ctx", "grok") is None
    assert t.put_item.call_count == 0


def test_register_then_lookup_roundtrip(monkeypatch):
    t = _fake_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    clean = reg.register(
        "HTTPS://Archive.org/details/x?utm_source=z",
        True,
        "First U.S. Army | WWII",
        "openserp",
    )
    assert clean == "https://archive.org/details/x"  # normalized, tracking dropped
    rec = reg.lookup("https://archive.org/details/x")
    assert rec is not None and rec.relevant is True and rec.source == "openserp"


def test_lookup_normalizes_so_tracking_variants_hit_same_entry(monkeypatch):
    t = _fake_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.register("https://ex.com/doc?id=5", True, "c", "grok")
    # a lookup with extra tracking params + different case still hits the same key
    assert reg.lookup("https://EX.com/doc?id=5&utm_medium=email#frag") is not None


def test_expired_entry_treated_as_not_seen(monkeypatch):
    t = _fake_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.register("https://ex.com/old", True, "c", "grok")
    # force the stored ttl into the past
    k = next(iter(t._store))
    t._store[k]["ttl"] = int(time.time()) - 10
    assert reg.lookup("https://ex.com/old") is None  # expired -> not seen
    assert reg.should_examine("https://ex.com/old") is True  # eligible for re-exam


def test_should_examine_false_for_non_clean(monkeypatch):
    monkeypatch.setattr(reg, "_table", lambda: _fake_table())
    assert reg.should_examine("file:///etc/passwd") is False
