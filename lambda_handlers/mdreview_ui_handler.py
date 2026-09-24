"""Lambda handler for the OCR markdown-review web UI.

Shows a human reviewer the original scanned page image (display-only
90-degree-reorientable) beside an editable Markdown **snippet** for that page,
and saves the corrected snippet to S3. Used for OCR pages whose 2-D task-org
tables PP-StructureV3's layout model could not recover (see
docs/current/dataquality/CHANDRA_OCR_DESIGN.md "Recovery limits & the
human-review compromise"). The saved snippet overrides that page's raw OCR at
merge time and is consumed once the merge succeeds (see
``submit_ocr_job.merge_outputs`` / ``_load_reviewed_snippets``).

Data contract (per book ``{book}`` under the data bucket):
  - Candidates: ``ocr-output/{book}/recovery/pN.recovery.json`` with
    ``recovered_a_table == false`` (a flagged, un-recovered page).
  - Page image: ``ocr-output/{book}/recovery/pN.png`` (300-DPI render).
  - Saved correction: ``ocr-output/{book}/reviewed/pN.md`` (this handler writes).

Protected by the same API Gateway basic-auth authorizer as the dedup UI.

Routes:
  GET  /mdreview                  — HTML review page
  GET  /mdreview/api/pages        — JSON list of flagged pages (book, page, status)
  GET  /mdreview/api/page         — one page: image (base64) + current snippet
  POST /mdreview/api/save         — write the corrected snippet to reviewed/pN.md
"""

import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def _s3():
    """Return an S3 client bound to the configured region."""
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _bucket() -> str:
    """Return the data bucket name from the environment."""
    return os.environ.get("S3_BUCKET", "")


def _json(status: int, body: Any) -> Dict[str, Any]:
    """Build an API Gateway JSON response."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _list_flagged_pages(s3, bucket: str) -> List[Dict[str, Any]]:
    """Scan every book's recovery JSONs for pages that recovered no table.

    Returns ``[{book, page, has_review}]`` sorted by (book, page). ``has_review``
    is True when a ``reviewed/pN.md`` already exists (in-progress/done).
    """
    pages: List[Dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for obj_page in paginator.paginate(Bucket=bucket, Prefix="ocr-output/"):
        for obj in obj_page.get("Contents", []) or []:
            key = obj["Key"]
            if not key.endswith(".recovery.json"):
                continue
            book, page_num = _parse_recovery_key(key)
            if book is None or page_num is None:
                continue
            try:
                doc = json.loads(
                    s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
                )
            except Exception:  # pragma: no cover - skip unreadable
                continue
            if doc.get("recovered_a_table"):
                continue  # already recovered; not a review candidate
            review_key = f"ocr-output/{book}/reviewed/p{page_num}.md"
            pages.append(
                {
                    "book": book,
                    "page": page_num,
                    "has_review": _exists(s3, bucket, review_key),
                }
            )
    pages.sort(key=lambda p: (p["book"], p["page"]))
    return pages


def _parse_recovery_key(key: str) -> Tuple[Optional[str], Optional[int]]:
    """Parse ``ocr-output/{book}/recovery/pN.recovery.json`` → (book, N)."""
    parts = key.split("/")
    if len(parts) < 4 or parts[0] != "ocr-output" or parts[-2] != "recovery":
        return None, None
    book = parts[1]
    stem = parts[-1]
    num = stem[1:].split(".", 1)[0]
    if stem.startswith("p") and num.isdigit():
        return book, int(num)
    return None, None


def _exists(s3, bucket: str, key: str) -> bool:
    """Return True if an S3 object exists."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:  # pragma: no cover - not found / no access
        return False


def _get_object_text(s3, bucket: str, key: str) -> Optional[str]:
    """Return an object's UTF-8 text, or None if absent."""
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception:  # pragma: no cover
        return None


def _page_snippet(s3, bucket: str, book: str, page_num: int) -> str:
    """Return the current editable snippet for a page.

    Prefers an existing ``reviewed/pN.md`` (in-progress edit); otherwise falls
    back to the Chandra-parsed ``flattened_hints`` from the recovery JSON,
    rendered as a starting-point markdown scaffold the reviewer completes.
    """
    reviewed = _get_object_text(
        s3, bucket, f"ocr-output/{book}/reviewed/p{page_num}.md"
    )
    if reviewed is not None:
        return reviewed
    recovery = _get_object_text(
        s3, bucket, f"ocr-output/{book}/recovery/p{page_num}.recovery.json"
    )
    if not recovery:
        return ""
    try:
        doc = json.loads(recovery)
    except json.JSONDecodeError:  # pragma: no cover
        return ""
    return _scaffold_from_hints(doc.get("flattened_hints", []))


def _scaffold_from_hints(hints: List[dict]) -> str:
    """Render flattened_hints into a starting-point markdown scaffold."""
    lines: List[str] = [
        "<!-- Reviewer: rebuild the 2-D table below from the page image. "
        "The groups/units are Chandra's flattened reading (columns not "
        "recovered). -->",
        "",
    ]
    for span in hints:
        for snap in span.get("snapshots", []):
            label = snap.get("label") or "(unlabeled)"
            descriptor = snap.get("descriptor")
            header = f"### {label}" + (f" — {descriptor}" if descriptor else "")
            lines.append(header)
            for grp in snap.get("groups", []):
                units = ", ".join(grp.get("units", []))
                lines.append(f"- **{grp.get('group')}**: {units}")
            lines.append("")
    return "\n".join(lines)


def _save_snippet(event, s3, bucket: str) -> Dict[str, Any]:
    """Write a corrected snippet to ``reviewed/pN.md``."""
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _json(400, {"error": "invalid JSON body"})
    book = payload.get("book")
    page_num = payload.get("page")
    markdown = payload.get("markdown")
    if not book or not isinstance(page_num, int) or markdown is None:
        return _json(400, {"error": "book, page (int), and markdown are required"})
    key = f"ocr-output/{book}/reviewed/p{page_num}.md"
    s3.put_object(Bucket=bucket, Key=key, Body=markdown.encode("utf-8"))
    logger.info(
        "Saved reviewed snippet: s3://%s/%s (%d chars)", bucket, key, len(markdown)
    )
    return _json(200, {"saved": key})


def _get_page(event, s3, bucket: str) -> Dict[str, Any]:
    """Return one page's image (base64) + current editable snippet."""
    params = event.get("queryStringParameters") or {}
    book = params.get("book")
    page_raw = params.get("page") or ""
    if not book or not page_raw.isdigit():
        return _json(400, {"error": "book and numeric page are required"})
    page_num = int(page_raw)
    img = None
    try:
        raw = s3.get_object(
            Bucket=bucket, Key=f"ocr-output/{book}/recovery/p{page_num}.png"
        )["Body"].read()
        img = base64.b64encode(raw).decode("ascii")
    except Exception:  # pragma: no cover - image optional
        img = None
    return _json(
        200,
        {
            "book": book,
            "page": page_num,
            "image_b64": img,
            "snippet": _page_snippet(s3, bucket, book, page_num),
        },
    )


def handler(event, _context=None):
    """API Gateway proxy handler for the markdown-review UI."""
    method = event.get("httpMethod", "GET")
    path = event.get("path", "/mdreview")
    s3 = _s3()
    bucket = _bucket()
    if not bucket:
        return _json(500, {"error": "S3_BUCKET not configured"})

    if method == "GET" and path == "/mdreview":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": _HTML_UI,
        }
    if method == "GET" and path == "/mdreview/api/pages":
        return _json(200, {"pages": _list_flagged_pages(s3, bucket)})
    if method == "GET" and path == "/mdreview/api/page":
        return _get_page(event, s3, bucket)
    if method == "POST" and path == "/mdreview/api/save":
        return _save_snippet(event, s3, bucket)
    return _json(404, {"error": f"no route for {method} {path}"})


_HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OCR Markdown Review</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#f5f7fa;color:#1f2937}
.header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:16px;text-align:center}
.wrap{display:flex;height:calc(100vh - 56px)}
.sidebar{width:220px;background:#fff;border-right:1px solid #e5e7eb;overflow-y:auto}
.sidebar h3{padding:12px;font-size:13px;color:#6b7280;text-transform:uppercase}
.pageitem{padding:10px 12px;cursor:pointer;border-bottom:1px solid #f0f0f0;font-size:14px}
.pageitem:hover{background:#f0f4ff}
.pageitem.active{background:#667eea;color:#fff}
.pageitem .done{color:#10b981;font-weight:bold}
.pageitem.active .done{color:#d1fae5}
.panes{flex:1;display:flex;overflow:hidden}
.pane{flex:1;display:flex;flex-direction:column;overflow:hidden}
.pane.left{border-right:1px solid #e5e7eb;background:#fff}
.toolbar{padding:8px;background:#f9fafb;border-bottom:1px solid #e5e7eb;display:flex;gap:8px;align-items:center}
.imgbox{flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;background:#374151}
.imgbox img{max-width:100%;max-height:100%;transition:transform .2s}
textarea{flex:1;width:100%;border:none;padding:16px;font-family:ui-monospace,Menlo,monospace;font-size:13px;resize:none;outline:none}
button{background:#667eea;color:#fff;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;font-size:13px}
button:hover{background:#5568d3}
button.ghost{background:#e5e7eb;color:#374151}
.save{background:#10b981}
.save:hover{background:#059669}
.status{font-size:13px;color:#6b7280;margin-left:auto}
</style>
</head>
<body>
<div class="header"><h2>OCR Markdown Review</h2></div>
<div class="wrap">
  <div class="sidebar"><h3>Flagged pages</h3><div id="pagelist"></div></div>
  <div class="panes">
    <div class="pane left">
      <div class="toolbar">
        <button class="ghost" onclick="rotate(-90)">&#8630; Rotate</button>
        <button class="ghost" onclick="rotate(90)">Rotate &#8631;</button>
        <span class="status" id="imgstatus"></span>
      </div>
      <div class="imgbox"><img id="pageimg" alt="page image"></div>
    </div>
    <div class="pane">
      <div class="toolbar">
        <button class="save" onclick="save()">Save</button>
        <span class="status" id="savestatus"></span>
      </div>
      <textarea id="md" placeholder="Select a flagged page..."></textarea>
    </div>
  </div>
</div>
<script>
let cur=null, rot=0;
async function loadPages(){
  const r=await fetch('/mdreview/api/pages'); const d=await r.json();
  const el=document.getElementById('pagelist'); el.innerHTML='';
  (d.pages||[]).forEach(p=>{
    const div=document.createElement('div');
    div.className='pageitem'; div.dataset.book=p.book; div.dataset.page=p.page;
    div.innerHTML=(p.has_review?'<span class="done">&#10003;</span> ':'')+p.book+' &mdash; p'+p.page;
    div.onclick=()=>selectPage(p.book,p.page,div);
    el.appendChild(div);
  });
}
async function selectPage(book,page,div){
  document.querySelectorAll('.pageitem').forEach(x=>x.classList.remove('active'));
  if(div) div.classList.add('active');
  cur={book,page}; rot=0;
  const r=await fetch('/mdreview/api/page?book='+encodeURIComponent(book)+'&page='+page);
  const d=await r.json();
  const img=document.getElementById('pageimg');
  img.style.transform='rotate(0deg)';
  img.src=d.image_b64?('data:image/png;base64,'+d.image_b64):'';
  document.getElementById('imgstatus').textContent=d.image_b64?'':'(no image)';
  document.getElementById('md').value=d.snippet||'';
  document.getElementById('savestatus').textContent='';
}
function rotate(deg){
  rot=(rot+deg)%360;
  document.getElementById('pageimg').style.transform='rotate('+rot+'deg)';
}
async function save(){
  if(!cur){return;}
  const md=document.getElementById('md').value;
  const r=await fetch('/mdreview/api/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({book:cur.book,page:cur.page,markdown:md})});
  const s=document.getElementById('savestatus');
  if(r.ok){s.textContent='Saved \\u2713'; loadPages();} else {s.textContent='Save failed';}
}
loadPages();
</script>
</body>
</html>"""
