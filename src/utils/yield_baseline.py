"""Deterministic expected-yield BASELINE + data-quality severity classification.

Complements `yield_observer` (which compares produced vs a supplied fulltext baseline) with a
baseline derived DETERMINISTICALLY from the source text itself. A high-recall regex/dictionary scan
gives a FLOOR estimate of how many entities of each type the text contains; Grok's produced count is
measured against it. `ratio = produced / deterministic`.

The floor is intentionally high-recall / low-precision — a confident LOWER BOUND, not ground truth —
so the alarm is on GROSS shortfall (produced << deterministic), never on exact match. Grok finding a
few MORE than the floor is healthy (context-only mentions the regex can't see).

Severity ladder (owner 2026-10-28 — a deterministic shortfall can jeopardize an entire book):
  • ok         — ratio healthy, or baseline below the min floor (too small to judge).
  • warn       — one entity moderately short -> WARN + recall-gap review, proceed.
  • fail_stop  — produced==0 while deterministic>=floor, OR shortfall across >=2 entity types
                 (the paragraph_numbers fingerprint: an upstream fault starves ALL passes). The
                 caller must halt the job (SystemExit) so a corrupt corpus is never persisted.

People are handled differently: the floor counts DISTINCT rank-anchored person CLUSTERS (so
"General Cota"/"Norman Cota"/"Cota" = 1), and low-confidence ambiguous names ("Private Smith") are
NOT scored here — they route to a recall-gap review instead (see yield_review).
"""

import logging
import re
from typing import Dict, List, Tuple

from src.utils.observe import observe_error

logger = logging.getLogger(__name__)

# Default thresholds (overridable via config).
MIN_BASELINE = 5  # below this the floor is too small to judge -> never alarms
RATIO_WARN = 0.5  # single entity below this (with baseline>=MIN) -> warn
MULTI_ENTITY_SHORTFALL = 2  # this many entities simultaneously short -> fail_stop

# --- Group / unit designators (high-signal structural patterns) ---
_UNIT_RE = re.compile(
    r"\b(\d{1,3}(?:st|nd|rd|d|th))\s+"
    r"(?:[A-Z][a-z]+\s+){0,3}"
    r"(Division|Corps|Army|Regiment|Battalion|Brigade|Fleet|Flotilla|Wing|Group)\b"
)
_FORCE_RE = re.compile(r"\bForce\s+[A-Z]\b")
_NAMED_ORG_RE = re.compile(
    r"\b(RAF|OB\s+WEST|Luftwaffe|Wehrmacht|Kriegsmarine|SHAEF|USAAF|OKW|OKH)\b"
)

# --- People: rank-anchored name (the high-confidence band) ---
_RANKED_NAME_RE = re.compile(
    r"\b(?:Field\s+Marshal|Gen(?:eral)?|Lt\.?\s*Gen(?:eral)?|Maj\.?\s*Gen(?:eral)?|"
    r"Brig\.?\s*Gen(?:eral)?|Col(?:onel)?|Lt\.?\s*Col|Maj(?:or)?|Capt(?:ain)?|"
    r"Adm(?:iral)?|Field\s+Marshal)\.?\s+"
    r"((?:[A-Z]\.\s*)*[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)


def _count_distinct(matches: List[str]) -> int:
    """Distinct count, normalized (lowercased, whitespace-collapsed)."""
    norm = {re.sub(r"\s+", " ", m).strip().lower() for m in matches if m and m.strip()}
    return len(norm)


def _count_groups(text: str) -> int:
    hits = [m.group(0) for m in _UNIT_RE.finditer(text)]
    hits += _FORCE_RE.findall(text)
    hits += [m.group(0) for m in _NAMED_ORG_RE.finditer(text)]
    return _count_distinct(hits)


def _count_people_clusters(text: str) -> int:
    """Distinct rank-anchored people, clustered by surname (last token). High-confidence band
    only: a ranked name with a distinctive surname. 'General Cota'/'Cota' collapse by surname.
    """
    surnames = set()
    for m in _RANKED_NAME_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        toks = [t for t in name.split(" ") if not t.endswith(".")]
        if toks:
            surnames.add(toks[-1].lower())
    return len(surnames)


def _count_dict_terms(text: str, terms: List[str]) -> int:
    """Count distinct dictionary terms (equipment nouns, place aliases) present in text."""
    low = text.lower()
    return len({t.lower() for t in terms if t and t.lower() in low})


def deterministic_count(entity_type: str, text: str, terms: List[str] = None) -> int:
    """High-recall FLOOR count of entities of `entity_type` in `text`. Fail-safe -> 0.

    `terms` is an optional dictionary (equipment/place aliases) for dict-based entities.
    """
    try:
        if not text:
            return 0
        if entity_type in ("groups", "people_groups"):
            return _count_groups(text)
        if entity_type == "people":
            return _count_people_clusters(text)
        if terms:
            return _count_dict_terms(text, terms)
        return 0
    except Exception as e:  # noqa: BLE001 - baseline must never crash the phase
        logger.debug("deterministic_count skipped for %s: %s", entity_type, e)
        return 0


def classify_entity(deterministic: int, produced: int) -> str:
    """Per-entity verdict: ok | warn | starved. Pure logic (testable)."""
    if deterministic < MIN_BASELINE:
        return "ok"  # floor too small to judge
    ratio = produced / deterministic if deterministic else 1.0
    if produced == 0:
        return "starved"  # total starvation on a real baseline
    if ratio < RATIO_WARN:
        return "warn"
    return "ok"


def classify_run(per_entity: Dict[str, Tuple[int, int]]) -> Tuple[str, List[str]]:
    """Book-level severity from {entity: (deterministic, produced)}.

    Returns (verdict, offenders) where verdict is ok | warn | fail_stop.
    fail_stop when ANY entity is 'starved' OR >=MULTI_ENTITY_SHORTFALL entities are short
    (starved or warn) at once (the book-jeopardizing upstream-fault fingerprint).
    """
    verdicts = {e: classify_entity(d, p) for e, (d, p) in per_entity.items()}
    starved = [e for e, v in verdicts.items() if v == "starved"]
    short = [e for e, v in verdicts.items() if v in ("starved", "warn")]
    if starved or len(short) >= MULTI_ENTITY_SHORTFALL:
        return "fail_stop", sorted(set(starved) | set(short))
    if short:
        return "warn", sorted(short)
    return "ok", []


# Entity dir -> (yield-baseline entity key, optional dict-terms loader name).
_SCORED_ENTITIES = ("groups", "people", "equipment", "places")


def _book_source_text(output_root) -> str:
    """Concatenate all parsed-chapter paragraph text for the book. Fail-safe -> ''."""
    import json
    from pathlib import Path

    parts = []
    try:
        for pf in (Path(output_root) / "content").rglob("*-parsed.json"):
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for p in data.get("paragraphs") or []:
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(parts)


def _load_terms(entity: str) -> List[str]:
    """Load a high-recall term dictionary for a dict-based entity (equipment/places). Fail-safe."""
    import yaml
    from pathlib import Path

    paths = {
        "equipment": "config/equipment_aliases.yaml",
        "places": "config/place_aliases.yaml",
    }
    p = paths.get(entity)
    if not p or not Path(p).exists():
        return []
    try:
        raw = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    terms: List[str] = []

    def _walk(node):
        if isinstance(node, str):
            terms.append(node)
        elif isinstance(node, list):
            for x in node:
                _walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)

    _walk(raw.get("aliases", raw))
    return [t for t in terms if isinstance(t, str) and len(t) >= 3]


# Maps a SCORED entity name (the baseline scorer's name) to the key used in the Phase-2 produced
# summary / on-disk entity dir, when they differ. The groups entity is scored as "groups" but is
# counted/persisted as "people_groups" — without this alias the gate reads produced=0 for a book
# that actually extracted groups and FALSELY fail_stops (XChannel-8 2026-10-29).
_PRODUCED_KEY_ALIAS = {"groups": "people_groups"}


def _produced_for(entity: str, produced_counts: Dict[str, int]) -> int:
    """Produced count for a scored entity, honoring the summary/dir key alias (groups<->people_groups).
    Checks the scored name first, then the aliased key, so either spelling in produced_counts works.
    """
    for key in (entity, _PRODUCED_KEY_ALIAS.get(entity, entity)):
        val = produced_counts.get(key)
        if val:
            return int(val)
    return 0


def _compute_baseline(
    text: str, produced_counts: Dict[str, int]
) -> Dict[str, Tuple[int, int]]:
    """Per-entity (deterministic floor, produced) + the per-entity baseline OBSERV lines."""
    per: Dict[str, Tuple[int, int]] = {}
    for entity in _SCORED_ENTITIES:
        terms = _load_terms(entity) if entity in ("equipment", "places") else None
        det = deterministic_count(entity, text, terms)
        prod = _produced_for(entity, produced_counts)
        per[entity] = (det, prod)
        logger.info(
            "OBSERV yield.baseline entity=%s deterministic=%d produced=%d verdict=%s",
            entity,
            det,
            prod,
            classify_entity(det, prod),
        )
    return per


def _log_verdict(
    verdict: str, offenders: List[str], per: Dict[str, Tuple[int, int]]
) -> None:
    """Emit the end-of-gate verdict OBSERV at the right level."""
    if verdict == "fail_stop":
        logger.error(
            "OBSERV yield.fail_stop offenders=%s per_entity=%s — HALTING: deterministic "
            "baseline vastly exceeds extracted yield (possible book-wide extraction fault; "
            "cf. paragraph_numbers 2026-10-28). Job stopped for root-cause.",
            offenders,
            {e: per[e] for e in offenders},
        )
    elif verdict == "warn":
        logger.warning(
            "OBSERV yield.warn offenders=%s per_entity=%s — extraction short of the "
            "deterministic floor; recall-gap review recommended.",
            offenders,
            {e: per[e] for e in offenders},
        )
    else:
        logger.info("OBSERV yield.ok per_entity=%s", per)


def run_quality_gate(
    output_root, produced_counts: Dict[str, int]
) -> Tuple[str, List[str]]:
    """Phase-2 end-of-phase data-quality gate. Computes the deterministic floor per scored entity,
    classifies the run, emits OBSERV, and appends to output/metrics/yield_quality.json (cumulative
    weighted assessment). Returns (verdict, offenders); the CALLER decides to halt on 'fail_stop'.
    Fail CLOSED: if the gate's own machinery crashes OR the deterministic baseline can't be read
    (empty source text), emit a loud OBSERV yield.gate_error / yield.gate_empty_baseline and return
    'fail_stop' — a BLIND or BROKEN gate halts for root-cause, it never silently passes.
    """
    try:
        text = _book_source_text(output_root)
        if not text:
            # The baseline source text is empty/unreadable. This is NOT healthy — the gate exists to
            # catch starvation, and with no baseline it is BLIND. Fail CLOSED + loud (anti-swallow):
            # a book whose source text can't be read must halt for root-cause, not silently pass.
            observe_error(
                "yield",
                "gate_empty_baseline",
                note="no source text for the deterministic baseline — gate is BLIND, halting",
                produced_total=sum(int(v or 0) for v in produced_counts.values()),
            )
            return "fail_stop", ["__baseline__"]
        per = _compute_baseline(text, produced_counts)
        verdict, offenders = classify_run(per)
        _append_quality_metrics(output_root, per, verdict)
        _log_verdict(verdict, offenders, per)
        return verdict, offenders
    except (
        Exception
    ) as e:  # noqa: BLE001 - the gate's own failure must be LOUD, not silent
        # Fail CLOSED (anti-swallow): the gate crashing is NOT a pass. Previously this returned
        # ("ok", []) — a broken safety net that silently green-lit every run (the backstop the
        # whole Pattern-8 remediation relies on was itself blind). Emit a loud OBSERV and halt.
        observe_error(
            "yield",
            "gate_error",
            reason=type(e).__name__,
            note="quality gate crashed — failing CLOSED (halting) rather than silently passing",
        )
        return "fail_stop", ["__gate_error__"]


def _append_quality_metrics(
    output_root, per: Dict[str, Tuple[int, int]], verdict: str
) -> None:
    """Append this run's per-entity (deterministic, produced, ratio) + verdict to the cumulative
    output/metrics/yield_quality.json for the long-term weighted assessment. Fail-safe.
    """
    import json
    import time
    from pathlib import Path

    try:
        mdir = Path(output_root) / "metrics"
        mdir.mkdir(parents=True, exist_ok=True)
        f = mdir / "yield_quality.json"
        hist: List[dict] = []
        if f.exists():
            try:
                hist = json.loads(f.read_text(encoding="utf-8")) or []
            except Exception:  # noqa: BLE001
                hist = []
        hist.append(
            {
                "ts": int(time.time()),
                "verdict": verdict,
                "entities": {
                    e: {
                        "deterministic": d,
                        "produced": p,
                        "ratio": round(p / d, 3) if d else None,
                    }
                    for e, (d, p) in per.items()
                },
            }
        )
        f.write_text(json.dumps(hist, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("yield_quality metrics append skipped: %s", e)
