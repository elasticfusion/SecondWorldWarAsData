"""BATCH_PHASE2/BATCH_PHASE3 env override for _should_use_batch_mode (no-rebuild testing hook)."""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as E

# --- _env_batch_override parsing ---


def test_override_truthy_variants(monkeypatch):
    for v in ("1", "true", "TRUE", "Yes", "on"):
        monkeypatch.setenv("BATCH_PHASE2", v)
        assert E._env_batch_override("phase2") is True


def test_override_falsy_variants(monkeypatch):
    for v in ("0", "false", "FALSE", "No", "off"):
        monkeypatch.setenv("BATCH_PHASE2", v)
        assert E._env_batch_override("phase2") is False


def test_override_absent_returns_none(monkeypatch):
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    assert E._env_batch_override("phase2") is None


def test_override_unparseable_returns_none(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "maybe")
    assert E._env_batch_override("phase2") is None


def test_override_empty_phase_key_returns_none(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "false")
    assert E._env_batch_override("") is None


def test_override_is_per_phase(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE3", "true")
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    assert E._env_batch_override("phase3") is True
    assert E._env_batch_override("phase2") is None


# --- _should_use_batch_mode precedence (env WINS, config never read when env set) ---


def test_env_false_wins_over_config(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "false")

    def _boom(*a, **k):  # if config is read, the env short-circuit failed
        raise AssertionError("config.yaml must NOT be read when env override is set")

    monkeypatch.setattr("builtins.open", _boom)
    assert E._should_use_batch_mode("phase2_extract.py") is False


def test_env_true_wins_and_skips_config(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "true")
    monkeypatch.setattr(
        "builtins.open",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("config read")),
    )
    assert E._should_use_batch_mode("phase2_extract.py") is True


def test_absent_env_falls_back_to_config(monkeypatch, tmp_path):
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("batch:\n  phase2: true\n")
    real_open = open
    monkeypatch.setattr(
        "builtins.open",
        lambda p, *a, **k: (
            real_open(cfg, *a, **k)
            if str(p) == "/app/config.yaml"
            else real_open(p, *a, **k)
        ),
    )
    assert E._should_use_batch_mode("phase2_extract.py") is True


# --- trigger-side passthrough: _build_env_overrides forwards BATCH_PHASE* from lambda env ---


def test_build_env_overrides_forwards_batch(monkeypatch):
    import importlib
    import lambda_handlers.trigger_handler as T

    monkeypatch.setenv("BATCH_PHASE2", "false")
    monkeypatch.delenv("BATCH_PHASE3", raising=False)
    env = T._build_env_overrides("USA-E-XChannel-8", None)
    names = {e["name"]: e["value"] for e in env}
    assert names.get("BOOK_NAME") == "USA-E-XChannel-8"
    assert names.get("BATCH_PHASE2") == "false"
    assert "BATCH_PHASE3" not in names


def test_build_env_overrides_absent_batch_not_added(monkeypatch):
    import lambda_handlers.trigger_handler as T

    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    monkeypatch.delenv("BATCH_PHASE3", raising=False)
    env = T._build_env_overrides("", [{"name": "CONVERT_KEY", "value": "k"}])
    names = {e["name"] for e in env}
    assert names == {"CONVERT_KEY"}  # no BOOK_NAME (empty), no BATCH_*


# --- explicit-disable detection + St. Vith guard honoring it ---


def test_explicitly_disabled_env_falsy(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "false")
    assert E._batch_explicitly_disabled("phase2_extract.py") is True


def test_explicitly_disabled_env_truthy_is_false(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "true")
    assert E._batch_explicitly_disabled("phase2_extract.py") is False


def test_explicitly_disabled_config_false(monkeypatch, tmp_path):
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("batch:\n  phase2: false\n")
    real = open
    monkeypatch.setattr(
        "builtins.open",
        lambda p, *a, **k: (
            real(cfg, *a, **k) if str(p) == "/app/config.yaml" else real(p, *a, **k)
        ),
    )
    assert E._batch_explicitly_disabled("phase2_extract.py") is True


def test_explicitly_disabled_config_absent_key_is_false(monkeypatch, tmp_path):
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("batch:\n  phase3: false\n")  # no phase2 key
    real = open
    monkeypatch.setattr(
        "builtins.open",
        lambda p, *a, **k: (
            real(cfg, *a, **k) if str(p) == "/app/config.yaml" else real(p, *a, **k)
        ),
    )
    assert E._batch_explicitly_disabled("phase2_extract.py") is False


def test_explicitly_disabled_read_error_is_false(monkeypatch):
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    monkeypatch.setattr(
        "builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    # a READ FAILURE must NOT be treated as an explicit disable (St. Vith stays safe)
    assert E._batch_explicitly_disabled("phase2_extract.py") is False


def test_guard_strips_batch_when_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("ECS_TASK_ID", "t1")
    monkeypatch.setenv("BATCH_PHASE2", "false")

    class _Sync:
        def stop(self):
            raise AssertionError("must NOT force submit-only on explicit disable")

    handled, args = E._resolve_ecs_batch_args(
        "phase2_extract.py", ["--batch", "--book", "X"], "phase2_extract", _Sync()
    )
    assert handled is False
    assert "--batch" not in args and "--book" in args


def test_guard_force_submits_when_ambiguous(monkeypatch):
    monkeypatch.setenv("ECS_TASK_ID", "t1")
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    # config read fails -> NOT explicit disable -> must force submit-only
    monkeypatch.setattr(
        "builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    called = {"stop": False, "submit": False}
    monkeypatch.setattr(
        E, "run_submit_only", lambda *a, **k: called.__setitem__("submit", True)
    )

    class _Sync:
        def stop(self):
            called["stop"] = True

    handled, _ = E._resolve_ecs_batch_args(
        "phase2_extract.py", ["--batch"], "phase2_extract", _Sync()
    )
    assert handled is True and called["submit"] and called["stop"]
