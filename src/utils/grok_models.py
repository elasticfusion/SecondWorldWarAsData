"""A model version that is gone fails LOUDLY and STOPS the run (owner, 2026-11-12).

xAI does not always refuse a retired model: it answers with another one (``grok-3-mini`` and
``grok-3-mini-fast`` are served by ``grok-4.3``, HTTP 200), or returns 404 (``grok-2-latest``).
Either way the pipeline would keep running on a model nobody chose - different answers, different
price, different cache keys - and the only trace was a warning. Now:

* every phase task starts with ``preflight``: each configured model must be in xAI's model list
  and must answer a 1-token request AS ITSELF;
* every answer is checked (``check_served``): live calls against the model that request asked
  for, batch results against the configured models; a 404 for a model is the same failure;
* a failure emits ``OBSERV grok.model_unavailable status=error`` (Slack + email via the
  phase2-complete topic) and an operator alert - once per model per process - and raises
  ``GrokModelUnavailable``.

``GrokModelUnavailable`` derives from ``BaseException`` ON PURPOSE (like ``KeyboardInterrupt``): the
extractors' fail-safe ``except Exception`` handlers must not turn it into "no entities found" -
it has to reach the phase and stop it. ``asyncio.gather(..., return_exceptions=True)`` returns it
as a result, so a gather must hand it on (``raise_if_unavailable``).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, Iterable, Optional, Set

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.3"

# a dated release of the requested model: grok-4.3 answered as grok-4.3-0430 / grok-4.3-20260430
_DATED = re.compile(r"-\d{4}(?:\d{2}(?:\d{2})?)?")
# (requested, served/detail) already alerted in this process
_alerted: Set[tuple] = set()
_alert_lock = threading.Lock()
# set by the first stop: every later call in this process raises BEFORE touching the network (a
# thread pool's queued work would otherwise each make a billed call to the redirected model)
_stopped: Dict[str, str] = {}


class GrokModelUnavailable(BaseException):
    """The requested model version is not served (retired, redirected or unknown). Stops the run:
    not an ``Exception``, so no fail-safe handler absorbs it."""


class PreflightUnverified(RuntimeError):
    """The preflight could not verify a model (network, 429, 5xx, no model in the reply) - not
    proof it is gone: the caller proceeds, and every answer is still checked (``check_served``).
    """


def served_matches(requested: str, served: str) -> bool:
    """The answering model is the one requested, or a dated release of it (``grok-4.3`` answered
    by ``grok-4.3-0430``); ``-latest`` names whatever the latest dated release is. Another variant
    (``grok-4.3-mini``) is another model."""
    requested, served = (requested or "").strip(), (served or "").strip()
    base = requested[: -len("-latest")] if requested.endswith("-latest") else requested
    if served in (requested, base):
        return True
    return served.startswith(base) and bool(_DATED.fullmatch(served[len(base) :]))


def model_unavailable(requested: str, where: str, served: str = "", detail: str = ""):
    """Alert (OBSERV error + operator alert, once per model) and raise ``GrokModelUnavailable``."""
    from src.utils.observe import observe_error, publish_alert

    what = f"served by {served!r}" if served else (detail or "not available")
    note = (
        f"model {requested!r} is {what}: update config.yaml api.grok.model / model_map "
        "(src/utils/grok_models). The run is STOPPED - nothing was extracted with a model "
        "nobody chose."
    )
    # parallel chapters hit it together: alert once per model, stop every time
    with _alert_lock:
        first = (requested, served or detail) not in _alerted
        _alerted.add((requested, served or detail))
        _stopped.setdefault("reason", f"{where}: model {requested!r} is {what}")
    if first:
        observe_error(
            "grok",
            "model_unavailable",
            requested=requested,
            served=served or "-",
            where=where,
            note=note,
        )
        publish_alert(
            f"WWII Pipeline: Grok model {requested} unavailable - run stopped", note
        )
    raise GrokModelUnavailable(f"{where}: {note}")


def ensure_running() -> None:
    """Raise ``GrokModelUnavailable`` (no network, no new alert) when this process already
    stopped on an unavailable model. Called before every Grok call."""
    reason = _stopped.get("reason")
    if reason:
        raise GrokModelUnavailable(f"stopped earlier - {reason}")


def raise_if_unavailable(results: Iterable[Any]) -> None:
    """Re-raise the first ``GrokModelUnavailable`` among gathered results
    (``asyncio.gather(..., return_exceptions=True)`` returns it as a plain result - the run would
    otherwise go on). Call it once the completed work is saved."""
    for r in results:
        if isinstance(r, GrokModelUnavailable):
            raise r


def check_served(requested: str, result: Dict[str, Any], where: str) -> None:
    """A live answer must come from the model its request asked for."""
    served = str((result or {}).get("model") or "")
    if served and not served_matches(requested, served):
        model_unavailable(requested, where, served=served)


def is_model_missing(status: int, text: str) -> bool:
    """An HTTP 404 saying the requested MODEL does not exist (xAI: ``"code":"not-found"``,
    "The model X does not exist or your team ... does not have access to it")."""
    t = (text or "").lower()
    # the model name may hold dots (grok-4.3): match within the message, not up to a "."
    return status == 404 and bool(
        re.search(r"\bmodel\b.{0,200}?\b(does not exist|not found)\b", t, re.S)
    )


def check_batch_served(served: str, allowed: Iterable[str], where: str) -> None:
    """A batch result must come from one of the models the pipeline requests. LIMIT: a batch
    result does not say which model its request named, so an answer from the WRONG configured
    model passes here; the phase preflight is what checks each configured model answers as
    itself."""
    served = (served or "").strip()
    allowed = [m for m in allowed if m]
    if served and allowed and not any(served_matches(m, served) for m in allowed):
        model_unavailable(", ".join(sorted(set(allowed))), where, served=served)


def default_model(config: Optional[Dict[str, Any]] = None) -> str:
    """The pipeline's model: ``GROK_MODEL`` env over ``api.grok.model`` over ``DEFAULT_MODEL``."""
    if config is None:
        try:
            from src.utils.config import load_config

            config = load_config()
        except Exception:  # noqa: BLE001 - no config (e.g. a bare Lambda): the default
            config = {}
    grok = (config.get("api") or {}).get("grok") or {}
    return os.getenv("GROK_MODEL") or grok.get("model") or DEFAULT_MODEL


def configured_models(config: Optional[Dict[str, Any]] = None) -> Set[str]:
    """Every model the pipeline requests: the default and each ``api.grok.model_map`` value."""
    if config is None:
        from src.utils.config import load_config

        config = load_config()
    grok = (config.get("api") or {}).get("grok") or {}
    models = {default_model(config)}
    models |= {str(m) for m in (grok.get("model_map") or {}).values()}
    return {m for m in models if m}


def _listed_models(body: Dict[str, Any]) -> Set[str]:
    """Model ids and their aliases from xAI's model list."""
    listed: Set[str] = set()
    for m in body.get("models") or body.get("data") or []:
        listed.add(str(m.get("id") or ""))
        listed |= {str(a) for a in m.get("aliases") or []}
    return listed


def preflight(
    api_key: str,
    models: Iterable[str],
    timeout: float = 30.0,
    probed: Iterable[str] = (),
) -> None:
    """Each model is listed by xAI AND answers a 1-token request as itself (a retired model may
    still be listed and silently redirected). ``probed``: models the caller already probed and
    checked (``check_served``) - list-checked only. Raises ``GrokModelUnavailable`` on the first
    that is gone; ``PreflightUnverified`` when it cannot tell; returns when every model is served.
    """
    import requests

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(f"{API_BASE}/language-models", headers=headers, timeout=timeout)
    resp.raise_for_status()
    listed = _listed_models(resp.json())
    done = set(probed)
    for model in sorted(set(models)):
        if model not in listed:
            model_unavailable(model, "preflight", detail="not in xAI's model list")
        if model in done:
            continue
        probe = requests.post(
            f"{API_BASE}/chat/completions",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
            timeout=timeout,
        )
        if is_model_missing(probe.status_code, probe.text):
            model_unavailable(model, "preflight", detail=f"HTTP {probe.status_code}")
        if probe.status_code != 200:
            raise PreflightUnverified(f"{model}: probe HTTP {probe.status_code}")
        reply = probe.json()
        if not reply.get("model"):
            raise PreflightUnverified(f"{model}: the reply names no model")
        check_served(model, reply, "preflight")
        logger.info("OBSERV grok.model_preflight model=%s status=ok", model)
