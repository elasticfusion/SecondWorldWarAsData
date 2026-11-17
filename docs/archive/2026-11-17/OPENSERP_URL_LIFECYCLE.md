# What happens when OpenSERP discovers a URL — end-to-end lifecycle

Discovery (owner-requested HIGH) prompted by the clean RoerDams reruns, which logged 60+
`OBSERV vision.image_blocked reason=ssrf_or_invalid` and ~0 images actually added for groups/places.
This traces a single URL returned by OpenSERP from search to persistence, with file:line, and lists
the findings. **Review-only map — no behavior claims beyond what the cited code does.**

## The pipeline (Phase-3 step 5/6, `enrich_*_with_openserp`)

### STAGE 1 — SEARCH → RESULT
`src/enrichment/openserp_enrichment.py::_search_openserp(query, openserp_url, limit=5)`
- Circuit-breaker gated (`_breaker_is_open`), rate-limited (`rate_limit_seconds`, default 5s),
  `GET {openserp_url}/mega/search?text=&limit=&mode=any` via `recorded_fetch("openserp", TEXTUAL)`
  (request_timeout_seconds, default 60 — browser rendering is slow).
- Parses flat list OR `{"results":[...]}` → returns `[{url, title, description(=snippet)}, ...]`.
- **The `url` is whatever the search engine returned** — a direct resource OR (usually) an HTML
  **page** URL (observed: `wikipedia.org/wiki/...`, `commons/wiki/Category:`, `army.mil/*.aspx`,
  `ebay/itm`, militaria product pages; and junk from poorly-disambiguated short queries like "2d"
  → crazygames/pencil2d, "5th" → fifth-third bank, Amazon sign-in).

### STAGE 2 — RELEVANCE GATE (Grok)
`_verify_result(title, context, grok_client, snippet, url)` (openserp_enrichment.py:298)
- Grok YES/NO on title+snippet+url; **FAIL-CLOSED** (no client or any error → reject, :307-312);
  cached in `openserp_verify` (YES/NO/NOT_FOUND). Metrics: `verify_yes`/`verify_no`. Observed ~35-68%
  pass — correctly rejecting junk (e.g. "Roer department", "Walmart Careers").

### STAGE 3 — CLASSIFY (the yield gap)
Per entity type the verified result's `url` is appended to a candidate list, e.g. people/groups:
`images.append({"url": url, "title": title, "source": "openserp"})` (openserp_enrichment.py:824/864).
- **The page URL is appended AS-IS. There is NO step that resolves a page URL to its underlying
  image** (no `og:image`, no `commons File:` / `upload.wikimedia.org` thumb resolution — grep of
  openserp_enrichment.py + images.py finds none). So a search-result PAGE can never become an image.

### STAGE 4 — FETCH + SSRF (where page URLs die)
Image candidates are downloaded SSRF-safe via `src/grok_client.py::_fetch_image_as_data_url` →
`src/extraction/images.py::fetch_image_bytes(url)`:
- `url_hygiene.clean_url` (scheme allowlist + reject loopback/private/link-local/cloud-metadata incl.
  decimal/hex/octal/IDN forms) → resolved-IP re-check (DNS-rebind) → `allow_redirects=False` →
  byte/gzip-bomb caps → **content-type + magic-byte image validation** (images.py:42-58, 93-109).
- A **page URL** returns `text/html` → `_is_valid_image_body` False → `fetch_image_bytes` returns
  `(None, None)` → `_fetch_image_as_data_url` logs
  **`OBSERV vision.image_blocked reason=ssrf_or_invalid`** (grok_client.py:1313) and raises.
- **This is correct (not a bug): it refuses to treat an HTML page as an image.** But combined with
  STAGE 3 (no page→image resolution), it is why group/place image yield is ~0.

### STAGE 5 — PERSIST
`enrich_*_with_openserp` → `_verify_and_apply` (images → `data["images"]`, web results →
`data["resource_urls"]`), `_mark_openserp_searched(data)` (sets `openserp_searched`/`_searched_at`,
the 90-day recheck gate), then **`write_json_with_lock(file, data, entity=<entity>)`** (schema-guarded,
version-stamped). Each kept item increments `items_added`. Run rollup:
`write_metrics` → `output/metrics/openserp_metrics.json` + `OBSERV openserp.summary` (searched/
enriched/queries/results/verify_yes/verify_no/breaker_opened/items_added) + `openserp.anomaly`
(breaker-open OR 0-of-N).

## FINDINGS

### F1 [HIGH yield] — no page-URL → image resolution → ~0 image yield
OpenSERP returns mostly HTML page URLs; STAGE 3 appends them unresolved; STAGE 4 correctly rejects
them as non-images. Net: group/place image enrichment yields ~0 while logging a flood of
`vision.image_blocked`. FIX: before (or instead of) fetching a page URL as an image, resolve it to a
direct image — fetch the page, extract `og:image` / for Wikimedia resolve `commons File:` →
`upload.wikimedia.org` thumbnail; or bias the `search_queries/*.yaml` templates toward direct-image
sources. (Ties to the earlier LOW "group-image search returns page URLs" note — fold in.)

### F2 [MED observability] — `vision.image_blocked reason=ssrf_or_invalid` conflates 3 causes
`grok_client.py:1313` emits a single reason whenever `fetch_image_bytes` returns no bytes, but
`images.py` internally distinguishes: (a) **SSRF-blocked** (clean_url/resolved-IP refusal — a real
security event), (b) **not-an-image** (text/html content-type — benign, the common case), (c)
**fetch error** (timeout/transport). Operators can't tell a genuine SSRF block from a benign
page-URL rejection. FIX: have `fetch_image_bytes` return a reason enum (ssrf_blocked / not_an_image /
fetch_error) and emit it, so `vision.image_blocked reason=<specific>` is actionable (and an SSRF
block can alert while not-an-image stays INFO).

## Security posture (confirmed sound)
Every download of an OpenSERP/LLM-returned URL routes through the SSRF guard (clean_url →
resolved-IP → no-redirect → caps → magic-byte). No module fetches a result-derived URL raw. The
flood of `vision.image_blocked` is the guard working, not a leak — only the reason label and the
upstream yield need improvement.
