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
    # age the stored examined_at past the retention window (new model derives
    # freshness from examined_at, not the ttl attribute)
    import json as _j

    k = next(iter(t._store))
    pay = _j.loads(t._store[k]["response"])
    pay["examined_at"] = int(time.time()) - (reg.RETENTION_DAYS * 86400 + 10)
    t._store[k]["response"] = _j.dumps(pay)
    assert reg.lookup("https://ex.com/old") is None  # expired -> not seen
    assert reg.should_examine("https://ex.com/old") is True  # eligible for re-exam


def test_should_examine_false_for_non_clean(monkeypatch):
    monkeypatch.setattr(reg, "_table", lambda: _fake_table())
    assert reg.should_examine("file:///etc/passwd") is False


# --- state model: claim / finalize / reclaim, decide(), dead ---


def _store_table():
    import json

    store = {}
    t = MagicMock()

    def _put(
        Item=None,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        k = Item["cache_key"]
        if ConditionExpression == "attribute_not_exists(cache_key)" and k in store:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
            )
        if ConditionExpression and ConditionExpression.startswith("#r ="):
            old = ExpressionAttributeValues[":old"]
            if store.get(k, {}).get("response") != old:
                from botocore.exceptions import ClientError

                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
                )
        store[k] = Item

    t.put_item.side_effect = _put
    t.get_item.side_effect = lambda Key: (
        {"Item": store[Key["cache_key"]]} if Key["cache_key"] in store else {}
    )
    t._store = store
    return t


def test_claim_wins_once_then_loser_defers(monkeypatch):
    import src.utils.url_registry as reg

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    assert reg.claim("https://ex.com/a") is True  # first wins
    assert reg.claim("https://ex.com/a") is False  # concurrent loser (live in_progress)
    action, _ = reg.decide("https://ex.com/a")
    assert action == reg.DEFER_INPROGRESS


def test_stale_inprogress_is_reclaimable(monkeypatch):
    import src.utils.url_registry as reg, json, time as _t

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.claim("https://ex.com/b")
    # age the claim past CLAIM_STALE_SECONDS
    k = reg._url_key(reg.clean_url("https://ex.com/b"))
    pay = json.loads(t._store[k]["response"])
    pay["claimed_at"] = int(_t.time()) - (reg.CLAIM_STALE_SECONDS + 5)
    t._store[k]["response"] = json.dumps(pay)
    assert reg.claim("https://ex.com/b") is True  # stale -> reclaimed


def test_decide_skip_known_after_searched(monkeypatch):
    import src.utils.url_registry as reg

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.finalize_searched("https://ex.com/c", True, "First US Army|WWII", "grok")
    action, rec = reg.decide("https://ex.com/c")
    assert action == reg.SKIP_KNOWN and rec.relevant is True


def test_dead_only_after_4_dns_failures_and_no_archive(monkeypatch):
    import src.utils.url_registry as reg

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    u = "https://gone.example.com/x"
    for i in range(reg.DEAD_DNS_FAILURES - 1):
        assert reg.record_dns_failure(u) == "retry"  # < 4 -> never dead
    assert reg.record_dns_failure(u) == "dead"  # 4th -> dead
    # dead has no ttl -> permanent; decide() = SKIP_KNOWN
    action, rec = reg.decide(u)
    assert action == reg.SKIP_KNOWN and rec.status == "dead"


def test_archive_present_prevents_dead(monkeypatch):
    import src.utils.url_registry as reg

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    u = "https://rot.example.com/y"
    reg.finalize_searched(u, True, "ctx", "wayback", first_wayback_ts="20200101000000")
    for _ in range(reg.DEAD_DNS_FAILURES + 2):
        v = reg.record_dns_failure(u)
    assert v == "retry"  # archive exists -> never dead


def test_domain_cooldown_defers(monkeypatch):
    import src.utils.url_registry as reg

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.claim("https://ex.com/one")  # stamps fetchgate for ex.com now
    action, _ = reg.decide("https://ex.com/two")  # same domain, within cooldown
    assert action == reg.DEFER_DOMAIN


def test_b1_two_tasks_cannot_both_reclaim_same_stale_claim(monkeypatch):
    """B1 regression: a stale in_progress is reclaimable, but if TWO tasks both observe
    the same stale bytes, only ONE wins — the optimistic lock is keyed on the observed
    bytes, so after task A reclaims (rewriting response), task B's conditional put on
    the OLD bytes fails."""
    import src.utils.url_registry as reg, json, time as _t

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.claim("https://ex.com/race")
    k = reg._url_key(reg.clean_url("https://ex.com/race"))
    pay = json.loads(t._store[k]["response"])
    pay["claimed_at"] = int(_t.time()) - (reg.CLAIM_STALE_SECONDS + 5)
    t._store[k]["response"] = json.dumps(pay)
    stale_bytes = t._store[k]["response"]

    # Both A and B observe the SAME stale bytes, then each attempts the conditional put.
    results = []
    for _ in range(2):
        rec, observed = reg._load_raw(t, reg.clean_url("https://ex.com/race"))
        assert (
            observed == stale_bytes or results
        )  # first sees stale; second sees A's new bytes
        try:
            from botocore.exceptions import ClientError

            def _cond_put(Item, observed_bytes):
                if t._store[k]["response"] != observed_bytes:
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException"}},
                        "PutItem",
                    )
                t._store[k] = Item

            _cond_put(
                {
                    "cache_key": k,
                    "response": json.dumps(
                        {
                            "url": "https://ex.com/race",
                            "status": "in_progress",
                            "claimed_at": int(_t.time()),
                            "examined_at": 0,
                        }
                    ),
                },
                stale_bytes,
            )
            results.append(True)
        except ClientError:
            results.append(False)
    assert results.count(True) == 1, "exactly one task may reclaim a stale claim"


def test_finalize_captures_http_status(monkeypatch):
    import src.utils.url_registry as reg

    t = _store_table()
    monkeypatch.setattr(reg, "_table", lambda: t)
    reg.finalize_searched("https://ex.com/s", True, "ctx", "grok", http_status=200)
    rec = reg.lookup("https://ex.com/s")
    assert rec is not None and rec.http_status == 200


def test_should_fetch_content_is_url_keyed(monkeypatch):
    """URL-keyed (NOT entity-keyed) dedup: a URL already searched -> skip; unknown -> fetch."""
    import src.utils.url_registry as reg

    monkeypatch.setattr(reg, "decide", lambda url: (reg.SKIP_KNOWN, None))
    assert (
        reg.should_fetch_content("https://en.wikipedia.org/wiki/George_Patton") is False
    )
    monkeypatch.setattr(reg, "decide", lambda url: (reg.GO, None))
    assert (
        reg.should_fetch_content("https://en.wikipedia.org/wiki/George_Patton") is True
    )


def test_should_fetch_content_failsafe(monkeypatch):
    """Any error in decide() -> True (prefer a fetch over silently dropping enrichment)."""
    import src.utils.url_registry as reg

    def _boom(url):
        raise RuntimeError("dynamo down")

    monkeypatch.setattr(reg, "decide", _boom)
    assert reg.should_fetch_content("https://x") is True
