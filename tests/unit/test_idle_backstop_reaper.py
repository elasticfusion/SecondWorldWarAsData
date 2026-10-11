"""Tests for the idle-infra backstop reaper (cost defense-in-depth)."""

import lambda_handlers.idle_backstop_reaper as r


class _State:
    """In-memory stand-in for the DynamoDB state table."""

    def __init__(self):
        self.strikes = 0
        self.last_count = -1


def _patch(monkeypatch, *, nat_up, lock_held, count, state):
    monkeypatch.setattr(r, "_nat_up", lambda ec2: nat_up)
    monkeypatch.setattr(r, "_lock_or_lease_held", lambda t: lock_held)
    monkeypatch.setattr(r, "_output_object_count", lambda s3: count)
    monkeypatch.setattr(
        r,
        "_load_state",
        lambda t: (state.strikes, state.last_count, "2000-01-01T00:00:00+00:00"),
    )

    def _save(t, strikes, last_count):
        state.strikes, state.last_count = strikes, last_count

    monkeypatch.setattr(r, "_save_state", _save)
    reaped = {"n": 0}
    monkeypatch.setattr(r, "_force_teardown", lambda *a: reaped.__setitem__("n", 1))
    monkeypatch.setattr(r, "_alert", lambda *a: None)
    # boto3 clients are only used by the real (now-patched) helpers; stub the import.
    monkeypatch.setattr(r, "handler", r.handler)  # no-op, keep reference
    import sys
    import types

    fake_boto3 = types.SimpleNamespace(
        client=lambda *a, **k: object(),
        resource=lambda *a, **k: types.SimpleNamespace(Table=lambda n: object()),
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return reaped


def test_nat_down_resets_and_noops(monkeypatch):
    st = _State()
    st.strikes = 1
    reaped = _patch(monkeypatch, nat_up=False, lock_held=False, count=100, state=st)
    out = r.handler({})
    assert out["reason"] == "nat already down"
    assert reaped["n"] == 0 and st.strikes == 0


def test_lock_held_never_reaps(monkeypatch):
    st = _State()
    st.strikes = 1
    reaped = _patch(monkeypatch, nat_up=True, lock_held=True, count=100, state=st)
    out = r.handler({})
    assert "lock/lease held" in out["reason"]
    assert reaped["n"] == 0 and st.strikes == 0  # strikes reset while work in flight


def test_progress_resets_strikes(monkeypatch):
    st = _State()
    st.strikes = 1
    st.last_count = 100
    reaped = _patch(monkeypatch, nat_up=True, lock_held=False, count=105, state=st)
    out = r.handler({})
    assert out["reason"] == "progress observed"
    assert reaped["n"] == 0 and st.strikes == 0


def test_first_no_progress_strike_does_not_reap(monkeypatch):
    st = _State()
    st.strikes = 0
    st.last_count = 100
    reaped = _patch(monkeypatch, nat_up=True, lock_held=False, count=100, state=st)
    out = r.handler({})
    assert out["reason"] == "first strike" and out["strikes"] == 1
    assert reaped["n"] == 0


def test_second_consecutive_no_progress_reaps(monkeypatch):
    st = _State()
    st.strikes = 1  # already one strike
    st.last_count = 100
    reaped = _patch(monkeypatch, nat_up=True, lock_held=False, count=100, state=st)
    out = r.handler({})
    assert out["action"] == "force_teardown"
    assert reaped["n"] == 1
    assert st.strikes == 0  # reset after firing


def test_too_soon_does_not_advance_strike(monkeypatch):
    """<50min since last observation -> strike must NOT advance to reap (anti-double-count)."""
    import datetime as _dt

    st = _State()
    st.strikes = 1
    st.last_count = 100
    reaped = _patch(monkeypatch, nat_up=True, lock_held=False, count=100, state=st)
    monkeypatch.setattr(
        r,
        "_load_state",
        lambda t: (
            st.strikes,
            st.last_count,
            _dt.datetime.now(_dt.timezone.utc).isoformat(),
        ),
    )
    out = r.handler({})
    assert reaped["n"] == 0 and "too soon" in out["reason"]


class _FakeTable:
    """scan() paginates 1 item/page so a naive Limit pre-filter would miss a late match."""

    def __init__(self, items):
        self._items = items

    def scan(self, **kw):
        prefix = kw["ExpressionAttributeValues"][":p"]
        start = kw.get("ExclusiveStartKey", {}).get("i", 0)
        matched = [it for it in self._items if it["cache_key"].startswith(prefix)]
        page = matched[start : start + 1]
        resp = {"Items": page}
        if start + 1 < len(matched):
            resp["LastEvaluatedKey"] = {"i": start + 1}
        return resp


def test_lock_held_found_across_pages():
    items = [{"cache_key": f"cache#{i}"} for i in range(20)] + [
        {"cache_key": "lock#phase2"}
    ]
    assert r._lock_or_lease_held(_FakeTable(items)) is True


def test_stale_expired_lease_not_counted():
    import time as _t

    now = int(_t.time())
    assert (
        r._lock_or_lease_held(
            _FakeTable([{"cache_key": "nat#lease#dead", "ttl": now - 100}])
        )
        is False
    )
    assert (
        r._lock_or_lease_held(
            _FakeTable([{"cache_key": "nat#lease#live", "ttl": now + 3600}])
        )
        is True
    )


def test_fail_safe_on_error(monkeypatch):
    # _nat_up raising must NOT reap and must not propagate.
    monkeypatch.setattr(
        r, "_nat_up", lambda ec2: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    import sys
    import types

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(
            client=lambda *a, **k: object(),
            resource=lambda *a, **k: types.SimpleNamespace(Table=lambda n: object()),
        ),
    )
    out = r.handler({})
    assert out["action"] == "none" and "error" in out["reason"]
