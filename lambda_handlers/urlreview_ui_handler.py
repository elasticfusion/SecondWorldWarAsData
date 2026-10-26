"""Lambda handler for the URL (fetch-find) Review UI.

Human review of TEXTUAL fetch-provenance finds surfaced at the end of Phase 3 — EVEN AFTER Grok's
relevance confirmation. A reviewer AFFIRMS a URL (→ promoted into Phase-0/1 ingestion as a
``SourceMetadata``, like an uploaded book) or REJECTS it (→ marked "won't do" = review_status
REJECTED, never ingested). Mirrors dedup_ui_handler / mdreview_ui_handler: an API-Gateway proxy
Lambda behind the shared DedupApi + Basic-auth authorizer.

Routes (all behind Basic auth):
  GET  /urlreview                   — HTML review page
  GET  /urlreview/api/pending       — JSON list of pending TEXTUAL finds (review_status=pending)
  POST /urlreview/api/action        — {fetch_event_id, decision: "affirm"|"reject"}

Fail-safe: every handler path returns a JSON/HTML response, never an unhandled 500. Untrusted
fields (URL/title/summary from scraped web content) are HTML-escaped before rendering (stored-XSS).
"""

from __future__ import annotations

import html
import json
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def _json(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _html(status: int, body: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def handler(event, _context):
    """API-Gateway proxy entrypoint. Fail-safe routing."""
    try:
        method = event.get("httpMethod", "GET")
        path = event.get("path", "/urlreview")
        route = f"{method} {path.rstrip('/') or '/urlreview'}"
        if route == "GET /urlreview":
            return _serve_ui()
        if route == "GET /urlreview/api/pending":
            return _get_pending()
        if route == "POST /urlreview/api/action":
            return _handle_action(event)
        return _json(404, {"error": "not found", "route": route})
    except Exception as e:  # noqa: BLE001 - a review UI must never 500 unhandled
        logger.exception("urlreview handler error")
        return _json(500, {"error": str(e)[:200]})


def _get_pending() -> dict:
    """JSON list of pending TEXTUAL finds (safe subset of fields)."""
    from src.enrichment.fetch_review import list_pending

    finds = list_pending()
    items = [
        {
            "fetch_event_id": f.get("fetch_event_id", ""),
            "provider": f.get("provider", ""),
            "url": f.get("endpoint", ""),
            "summary": f.get("result_summary", ""),
            "queried_at": f.get("queried_at", ""),
        }
        for f in finds
        if f.get("fetch_event_id")
    ]
    return _json(200, {"count": len(items), "pending": items})


def _handle_action(event) -> dict:
    """Affirm (→ stage for Phase-0/1 ingestion) or reject (→ won't-do) one find."""
    from src.enrichment.fetch_review import list_pending

    parsed = _parse_action(event)
    if isinstance(parsed, dict):  # an error response
        return parsed
    fid, decision = parsed

    find = next((f for f in list_pending() if f.get("fetch_event_id") == fid), None)
    if find is None:
        return _json(404, {"error": f"no pending find {fid}"})
    return _apply_decision(find, fid, decision)


def _parse_action(event):
    """Validate the action body. Returns (fetch_event_id, decision) or an error dict response."""
    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:  # noqa: BLE001
        return _json(400, {"error": "invalid JSON body"})
    fid = str(body.get("fetch_event_id", "")).strip()
    decision = str(body.get("decision", "")).strip().lower()
    if not fid or decision not in ("affirm", "reject", "exclude"):
        return _json(
            400,
            {"error": "require fetch_event_id + decision in {affirm,reject,exclude}"},
        )
    return fid, decision


def _apply_decision(find: dict, fid: str, decision: str) -> dict:
    """Execute a reviewer decision: reject → won't-do, exclude → site/url denylist (Grok scope),
    or affirm → stage for ingestion."""
    from src.enrichment.fetch_review import affirm_fetch_find, reject_fetch_find

    if decision == "reject":
        ok = reject_fetch_find(find)
        return _json(
            200 if ok else 500,
            {"fetch_event_id": fid, "decision": "rejected", "ok": ok},
        )

    if decision == "exclude":
        return _do_exclude(find, fid)

    # affirm → promote into ingestion (SourceMetadata). The affirmed content lives in the
    # source_retrieval quarantine keyed by the find; if absent, acquisition re-fetches the endpoint.
    from pathlib import Path

    qpath = Path(find.get("quarantine_path") or find.get("endpoint", ""))
    src = affirm_fetch_find(find, qpath)
    return _json(
        200 if src is not None else 500,
        {
            "fetch_event_id": fid,
            "decision": "affirmed",
            "ok": src is not None,
            "staged_source_id": getattr(src, "source_id", None),
        },
    )


def _grok_client():
    """Best-effort GrokClient for the exclude-scope decision. None on any error (→ fail-closed to
    URL-only scope in exclude_fetch_find)."""
    try:
        from src.grok_client import GrokClient
        from src.utils.backends import create_cache_backend
        from src.utils.config import load_config
        from pathlib import Path

        config = load_config()
        cache = create_cache_backend(config, Path("cache/api"))
        api_key = (config.get("secrets", {}) or {}).get(
            "grok_api_key"
        ) or os.environ.get("GROK_API_KEY", "")
        return GrokClient(cache, api_key=api_key)
    except Exception as e:  # noqa: BLE001
        logger.debug("urlreview grok client unavailable: %s", e)
        return None


def _do_exclude(find: dict, fid: str) -> dict:
    """Exclude action: Grok decides SITE vs URL scope; persist at that scope."""
    from src.enrichment.fetch_review import exclude_fetch_find

    result = exclude_fetch_find(find, _grok_client())
    return _json(
        200 if result.get("ok") else 500,
        {
            "fetch_event_id": fid,
            "decision": "excluded",
            "scope": result.get("scope"),
            "host": result.get("host"),
            "ok": bool(result.get("ok")),
        },
    )


def _serve_ui() -> dict:
    """Render the review page. Untrusted fields are escaped client-side via textContent in the JS
    (no innerHTML of find data), and server-side any embedded strings are html.escaped.
    """
    title = (
        html.escape(os.getenv("ENV_NAME", "dev"))
        + " — URL Review (Phase 3 fetch finds)"
    )
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;max-width:60rem}}
 h1{{font-size:1.3rem}} .find{{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:.75rem 0}}
 .prov{{color:#555;font-size:.85rem}} .u{{word-break:break-all}} button{{margin-right:.5rem;padding:.4rem .8rem}}
 .affirm{{background:#2a7;color:#fff;border:0;border-radius:4px}} .reject{{background:#a33;color:#fff;border:0;border-radius:4px}}
 .exclude{{background:#555;color:#fff;border:0;border-radius:4px}}
 .empty{{color:#777}} .msg{{margin:.5rem 0;color:#06a}}
</style></head><body>
<h1>{title}</h1>
<p>Textual finds awaiting review — affirmed URLs are promoted into Phase-0/1 ingestion (like an
uploaded source); rejected URLs are marked "won't do" and never ingested. "Exclude" asks Grok
whether to skip just this URL or the whole site, then persists that scope so future searches skip it.</p>
<div id="msg" class="msg"></div>
<div id="list"><p class="empty">loading…</p></div>
<script>
const base = location.pathname.replace(/\\/$/, "");
async function load() {{
  const r = await fetch(base + "/api/pending");
  const d = await r.json();
  const list = document.getElementById("list");
  list.innerHTML = "";
  if (!d.pending || !d.pending.length) {{ list.innerHTML = '<p class="empty">No URLs pending review.</p>'; return; }}
  for (const f of d.pending) {{
    const div = document.createElement("div"); div.className = "find";
    const u = document.createElement("div"); u.className = "u"; u.textContent = f.url || "(no url)";
    const p = document.createElement("div"); p.className = "prov";
    p.textContent = "provider=" + (f.provider||"") + "  •  " + (f.summary||"") + "  •  " + (f.queried_at||"");
    const a = document.createElement("button"); a.className="affirm"; a.textContent="Affirm → ingest";
    const x = document.createElement("button"); x.className="reject"; x.textContent="Reject (won't do)";
    const e = document.createElement("button"); e.className="exclude"; e.textContent="Exclude (ask Grok: site?)";
    a.onclick = () => act(f.fetch_event_id, "affirm");
    x.onclick = () => act(f.fetch_event_id, "reject");
    e.onclick = () => act(f.fetch_event_id, "exclude");
    div.append(u, p, a, x, e); list.append(div);
  }}
}}
async function act(id, decision) {{
  document.getElementById("msg").textContent = "…";
  const r = await fetch(base + "/api/action", {{method:"POST",headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{fetch_event_id:id, decision}})}});
  const d = await r.json();
  let note = "";
  if (d.staged_source_id) note = " — staged " + d.staged_source_id;
  else if (d.scope) note = " — scope=" + d.scope + (d.host ? " (" + d.host + ")" : "");
  document.getElementById("msg").textContent = d.ok ? (decision + " ok" + note) : ("failed: " + (d.error||""));
  load();
}}
load();
</script></body></html>"""
    return _html(200, page)
