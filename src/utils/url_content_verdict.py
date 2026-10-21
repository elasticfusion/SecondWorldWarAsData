"""Grok content-verdict + source_qualification derivation
(URL_SOURCED_ENRICHMENT_DESIGN.md step 3 + SOURCE_QUALIFICATION_DESIGN.md).

Given fetched CONTENT (url_fetch.FetchResult) + the entity context, Grok renders a
verdict ON THE CONTENT (not the URL string, not Grok's own knowledge):
  - relevance to THIS entity + WWII,
  - FALSE-LEAD detection (reachable 200 page that is the wrong entity / spam / does not
    substantiate — a content rejection, distinct from mechanical dead),
  - per claim: fact (CONFIRMED AGAINST THE SOURCE) vs opinion -> claim_type.
Then derive the two-axis source_qualification rating (primary/secondary/tertiary +
proximity | evidentiary_support, the HIGH..LOWEST ladder).

Trust model: Grok FACTS are untrusted; Grok is trusted to EVALUATE the fetched content.
A fact Grok cannot confirm against the source is NOT written. An opinion is written
tagged. A Grok assertion with NO cited URL never reaches here (the caller only invokes
this with a FetchResult = a URL was fetched — #10 drop-no-URL enforced at the boundary).

FAIL-CLOSED: no grok_client / any error / unparseable -> not relevant, no claims.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROMPT = """You are evaluating whether a fetched web page supports facts about a \
specific WWII entity. Judge ONLY from the PAGE CONTENT below — do NOT use outside \
knowledge.

Entity: {entity}
Context: {context}

PAGE CONTENT (first 6000 chars):
{content}

Return STRICT JSON:
{{
  "relevant": true|false,              // is this page actually about THIS entity AND WWII?
  "false_lead": true|false,            // reachable but WRONG entity / spam / unrelated?
  "false_lead_reason": "...",          // short reason if false_lead
  "source_type": "primary|secondary|tertiary|reference|unknown",
  "proximity": "participant|witness|contemporaneous|recollection|n/a",
  "evidentiary_support": "dense|light|none|n/a",
  "claims": [                          // ONLY claims the PAGE substantiates
    {{"text": "...", "claim_type": "fact|opinion", "confirmed": true|false}}
  ]
}}
A FACT must be CONFIRMED by the page to set confirmed=true; if the page does not clearly \
support it, omit it or set confirmed=false (it will NOT be written). An OPINION is \
written tagged as opinion. Return ONLY the JSON."""

_CONTENT_CAP = 6000


@dataclass
class Claim:
    text: str
    claim_type: str  # fact | opinion
    confirmed: bool


@dataclass
class ContentVerdict:
    relevant: bool
    false_lead: bool
    false_lead_reason: str = ""
    source_qualification: Dict[str, Any] = field(default_factory=dict)
    claims: List[Claim] = field(default_factory=list)
    writable_claims: List[Claim] = field(
        default_factory=list
    )  # confirmed facts + opinions


_VALID_SOURCE_TYPE = {"primary", "secondary", "tertiary", "reference", "unknown"}
_VALID_PROXIMITY = {"participant", "witness", "contemporaneous", "recollection", "n/a"}
_VALID_EVIDENTIARY = {"dense", "light", "none", "n/a"}


def _reject(reason: str) -> ContentVerdict:
    return ContentVerdict(relevant=False, false_lead=False, false_lead_reason=reason)


def _parse_claims(raw_claims: list) -> "List[Claim]":
    """Parse the raw Grok claims array into Claim objects: require text, normalize claim_type to
    fact|opinion (default fact), carry the confirmed flag. Malformed entries are skipped.
    """
    claims: List[Claim] = []
    for c in raw_claims:
        if not isinstance(c, dict) or not c.get("text"):
            continue
        ct = c.get("claim_type", "fact")
        ct = ct if ct in ("fact", "opinion") else "fact"
        claims.append(
            Claim(
                text=str(c["text"])[:2000],
                claim_type=ct,
                confirmed=bool(c.get("confirmed", False)),
            )
        )
    return claims


def evaluate(
    content: Optional[str],
    entity: str,
    context: str,
    grok_client: Any,
    source_url: str = "",
) -> ContentVerdict:
    """Render the Grok content verdict for fetched CONTENT. Fail-closed on no content /
    no grok / error -> not relevant, no writable claims. ``writable_claims`` = confirmed
    facts + opinions (unconfirmed facts are dropped)."""
    if not content or not str(content).strip():
        return _reject("no content")
    if grok_client is None:
        return _reject("no grok client (fail-closed)")

    prompt = _PROMPT.format(
        entity=(entity or "")[:200],
        context=(context or "")[:300],
        content=str(content)[:_CONTENT_CAP],
    )
    try:
        raw = grok_client.extract_json(
            prompt=prompt, use_cache=True, cache_type="url_content_verdict"
        )
    except Exception as e:  # noqa: BLE001 - fail-closed
        logger.warning("content verdict grok error for %s: %s", source_url[:120], e)
        return _reject(f"grok error: {e}")

    if not isinstance(raw, dict):
        return _reject("unparseable verdict")

    return _verdict_from_raw(raw, source_url)


def _verdict_from_raw(raw: Dict[str, Any], source_url: str) -> ContentVerdict:
    """Build the ContentVerdict from Grok's parsed dict: short-circuit on false_lead / not
    relevant, else keep writable claims (opinions + confirmed facts). Preserves exactly.
    """
    relevant = bool(raw.get("relevant", False))
    false_lead = bool(raw.get("false_lead", False))
    if false_lead or not relevant:
        return ContentVerdict(
            relevant=relevant,
            false_lead=false_lead,
            false_lead_reason=str(raw.get("false_lead_reason", ""))[:300],
        )

    claims = _parse_claims(raw.get("claims", []) or [])
    # Writable: opinions (tagged) + facts ONLY if confirmed against the source.
    writable = [c for c in claims if c.claim_type == "opinion" or c.confirmed]

    return ContentVerdict(
        relevant=True,
        false_lead=False,
        source_qualification=_derive_qualification(raw, source_url),
        claims=claims,
        writable_claims=writable,
    )


def _derive_qualification(raw: Dict[str, Any], source_url: str) -> Dict[str, Any]:
    """Build the source_qualification object from Grok's judgment, validated against the
    schema enums (SOURCE_QUALIFICATION_DESIGN.md). method=llm_assisted (Grok judged the
    content); basis records the source."""
    st = raw.get("source_type", "unknown")
    st = st if st in _VALID_SOURCE_TYPE else "unknown"
    prox = raw.get("proximity", "n/a")
    prox = prox if prox in _VALID_PROXIMITY else "n/a"
    ev = raw.get("evidentiary_support", "n/a")
    ev = ev if ev in _VALID_EVIDENTIARY else "n/a"
    return {
        "source_type": st,
        "proximity": prox if st == "primary" else "n/a",
        "evidentiary_support": ev if st in ("secondary", "tertiary") else "n/a",
        "method": "llm_assisted",
        "basis": f"grok content verdict: {source_url[:160]}",
        "confidence": "medium",
    }
