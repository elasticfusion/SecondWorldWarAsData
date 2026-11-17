#!/usr/bin/env python3
"""Identify possible duplicate places based on name similarity and coordinates."""

import itertools
import json
import logging
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List

from src.enrichment.places_geo import coords_verified as _coords_verified
from src.utils.name_start import MIN_START_MATCH, distinctive_start, starts_match

logger = logging.getLogger(__name__)

# Repo root (src/dedup/<this>.py -> parents[2]); output/ and config/ resolve from here.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SKIP_FILES = {"index.json", "duplicate_report.json", "not_duplicates.json"}


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _normalize(name: str) -> str:
    name = _strip_accents(name.lower().strip())
    for filler in [" of ", " the ", " de ", " du ", " la ", " le ", " des "]:
        name = name.replace(filler, " ")
    return " ".join(name.split())


# Common geographic terms that inflate similarity between unrelated places
_GEO_PREFIXES = (
    "fort ",
    "hill ",
    "mont ",
    "monte ",
    "bois ",
    "foret ",
    "col ",
    "pont ",
    "saint ",
    "st ",
    "sainte ",
    "ste ",
    "camp ",
    "chateau ",
)
_GEO_SUFFIXES = (
    " river",
    " creek",
    " bridge",
    " forest",
    " woods",
    " ridge",
    " hill",
    " mountain",
    " pass",
    " crossing",
    " canal",
    " lake",
)


def _strip_geo_terms(name: str) -> str:
    """Strip common geographic prefixes/suffixes for core name comparison."""
    n = name.lower().strip()
    for prefix in _GEO_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix) :]
            break
    for suffix in _GEO_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    return n.strip()


# Leading words that do not identify a place (titles/articles): skipped when finding the
# distinctive START of a name, so "St. Lô" / "Saint-Lô" / "Le Lô" all start at "lo".
_LEADING_NOISE = frozenset(p.strip() for p in _GEO_PREFIXES) | {
    # articles / elided articles ("L'Aigle", "d'Isigny", "Pont-a-Mousson")
    "le",
    "la",
    "les",
    "the",
    "l",
    "d",
    "de",
    "du",
    "des",
    "a",
    "au",
    "aux",
    "sur",
    "en",
    # title variants of the _GEO_PREFIXES words ("Mt. Cassino" == "Monte Cassino")
    "mt",
    "mte",
    "mount",
}
_MIN_START_MATCH = (
    MIN_START_MATCH  # shared leading characters required (src/utils/name_start)
)


def _distinctive_start(name: str) -> str:
    """The first identifying word of a place name (accent/case/punctuation-insensitive)."""
    return distinctive_start(_normalize(name), _LEADING_NOISE)


def _starts_match(a: str, b: str) -> bool:
    """True when the two names begin the same way.

    A place name is identified by its START. Shared ENDINGS are generic toponym suffixes
    (-mouth, -ville, Beach, -au-Plain, -du-Mont) that tell nothing about identity: Plymouth /
    Weymouth, Colleville / Culoville, OMAHA Beach / UTAH Beach, Neuville-au-Plain /
    Beuzeville-au-Plain are different places. So when the starts differ the names cannot
    be duplicates, however much of their tails match.
    """
    return starts_match(_normalize(a), _normalize(b), _LEADING_NOISE, _MIN_START_MATCH)


_GATED_HIGH_SIM = [
    0
]  # pairs zeroed by the start gate that raw string similarity rated >= 0.85
_RAW_HIGH_SIM = 0.85


def _similarity(a: str, b: str) -> float:
    if not _starts_match(a, b):
        # The ending must not matter when the beginning does not match. Count the rare pair that
        # is nearly identical apart from its first letters (a typo like Havre/Harve) so a lost
        # duplicate is visible in place_dedup.summary instead of silently gone.
        if SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() >= _RAW_HIGH_SIM:
            _GATED_HIGH_SIM[0] += 1
        return 0.0
    base_sim = SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()
    # If names share a geographic prefix/suffix, also check core name similarity
    core_a = _strip_geo_terms(_normalize(a))
    core_b = _strip_geo_terms(_normalize(b))
    if core_a != _normalize(a) or core_b != _normalize(b):
        # At least one name had a geo term stripped — use the lower of base vs core
        core_sim = SequenceMatcher(None, core_a, core_b).ratio()
        return min(base_sim, max(core_sim, base_sim * 0.7))
    return base_sim


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from src.utils.geo import haversine_km

    return haversine_km(lat1, lon1, lat2, lon2)


def _get_coords(data: dict) -> tuple:
    """Extract lat/lon from a place dict, checking top-level and nested fields."""
    lat, lon = _extract_flat_coords(data)
    if lat is None:
        lat, lon = _extract_nested_coords(data)
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (ValueError, TypeError):
            pass
    return None, None


def _extract_flat_coords(data: dict) -> tuple:
    """Extract coordinates from top-level fields."""
    lat = data.get("latitude") or data.get("lat")
    lon = data.get("longitude") or data.get("lon") or data.get("lng")
    return lat, lon


def _extract_nested_coords(data: dict) -> tuple:
    """Extract coordinates from nested 'coordinates' object."""
    coords = data.get("coordinates")
    if isinstance(coords, dict):
        lat = coords.get("latitude") or coords.get("lat")
        lon = coords.get("longitude") or coords.get("lon")
        return lat, lon
    return None, None


MAX_DISTANCE_KM = 20  # Places farther apart than this are NOT duplicates
_SUPPRESSED_UNVERIFIED = [0]  # weak-rule pairs skipped for unverified coords (per run)


def find_duplicate_places(places_dir: Path) -> List[Dict]:
    """Find potential duplicate places."""
    _SUPPRESSED_UNVERIFIED[0] = 0
    _GATED_HIGH_SIM[0] = 0
    places = _load_places(places_dir)
    excluded_pairs, excluded_names = _load_exclusions(places_dir)

    # Incremental: only process clusters containing new files
    from src.dedup.incremental import get_last_dedup_run, get_new_files

    since = get_last_dedup_run("places")
    new_files = get_new_files(places_dir, since)
    if new_files:
        logger.info(
            "Incremental dedup: %d new place files since last run", len(new_files)
        )

    duplicates: List[Dict[str, Any]] = []
    seen: set = set()

    for i, p1 in enumerate(places):
        if i in seen:
            continue
        cluster, reasons = _find_cluster(i, p1, places, seen)
        if len(cluster) >= 2:
            # Incremental: skip cluster if no member is new
            if new_files and not any(p["filename"] in new_files for p in cluster):
                continue
            seen.add(i)
            duplicates.append(_build_group(cluster, reasons))

    duplicates.sort(key=lambda x: float(x["confidence"]), reverse=True)
    filtered = _filter_excluded(duplicates, excluded_pairs, excluded_names)

    filtered = _drop_fully_reviewed(filtered)
    logger.info(
        "OBSERV place_dedup.summary status=ok places=%d weak_suppressed_unverified=%d "
        "start_gated_near_identical=%d",
        len(places),
        _SUPPRESSED_UNVERIFIED[0],
        _GATED_HIGH_SIM[0],
    )
    return filtered


def _drop_fully_reviewed(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Suppress groups whose every pair was already reviewed by a human."""
    from src.dedup.exclusions import load_reviewed_pairs

    reviewed = load_reviewed_pairs("places")
    if not reviewed:
        return groups
    remaining = []
    for g in groups:
        names = [p["filename"] for p in g["people"]]
        pairs = itertools.combinations(names, 2)
        if not all(tuple(sorted(pair)) in reviewed for pair in pairs):
            remaining.append(g)
    return remaining


def _load_exclusions(places_dir: Path) -> tuple:
    """Load excluded pairs from DynamoDB or local JSON."""
    from src.dedup.exclusions import get_exclusion_store

    store = get_exclusion_store("places", places_dir)
    pairs = store.load()
    name_pairs = store.load_name_exclusions()
    return pairs, name_pairs


def _build_group(cluster: list, reasons: set) -> Dict[str, Any]:
    """Build a duplicate group dict from a cluster."""
    confidence = max(
        _similarity(a["name"], b["name"])
        for a in cluster
        for b in cluster
        if a is not b
    )
    return {
        "confidence": round(confidence, 2),
        "reasons": sorted(reasons),
        "people": [
            {"name": p["name"], "filename": p["filename"], "PlaceID": p["PlaceID"]}
            for p in cluster
        ],
    }


def _filter_excluded(
    duplicates: List[Dict[str, Any]], excluded_pairs: set, excluded_names: set
) -> List[Dict[str, Any]]:
    """Remove groups where all pairs are excluded (by filename or name)."""
    if not excluded_pairs and not excluded_names:
        return duplicates
    from src.dedup.exclusions import _normalize_exclusion_name

    filtered = []
    for dup in duplicates:
        filenames = [p["filename"] for p in dup["people"]]
        names = [p["name"] for p in dup["people"]]
        all_excluded = True
        for i, (a, na) in enumerate(zip(filenames, names)):
            for b, nb in zip(filenames[i + 1 :], names[i + 1 :]):
                file_pair = tuple(sorted([a, b]))
                name_pair = tuple(
                    sorted(
                        [_normalize_exclusion_name(na), _normalize_exclusion_name(nb)]
                    )
                )
                if file_pair not in excluded_pairs and name_pair not in excluded_names:
                    all_excluded = False
                    break
            if not all_excluded:
                break
        if not all_excluded:
            filtered.append(dup)
    return filtered


def _load_coords_cache(places_dir: Path) -> Dict[str, Dict]:
    """Load coords cache from local file or DynamoDB."""
    coords_file = places_dir / "coords.json"
    if coords_file.exists():
        try:
            return json.loads(coords_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Fall back to DynamoDB (per-place items)
    try:
        from src.utils.config import load_config

        cfg = load_config()
        if cfg.get("aws", {}).get("enabled"):
            import boto3

            table_name = cfg["aws"].get("cache_table", "")
            region = cfg["aws"].get("region", "us-east-1")
            table = boto3.resource("dynamodb", region_name=region).Table(table_name)
            coords: Dict[str, Dict] = {}
            kwargs = {
                "FilterExpression": "begins_with(cache_key, :prefix)",
                "ExpressionAttributeValues": {":prefix": "place_coords#"},
                "ProjectionExpression": "cache_key, lat, lon, PlaceID",
            }
            while True:
                resp = table.scan(**kwargs)
                for item in resp.get("Items", []):
                    filename = item["cache_key"].split("#", 1)[1]
                    coords[filename] = {
                        "lat": float(item.get("lat", 0)),
                        "lon": float(item.get("lon", 0)),
                        "PlaceID": item.get("PlaceID", ""),
                    }
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            return coords
    except Exception:
        pass
    return {}


def _load_local_places(places_dir: Path) -> "tuple[list, set]":
    """Place records from the local *.json files. Returns (places, filenames seen)."""
    places: list = []
    seen_filenames: set = set()
    for f in sorted(places_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        seen_filenames.add(f.name)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", f.name, e)
            continue
        name = data.get("current_name", data.get("place_name", data.get("name", "")))
        if not (isinstance(name, str) and name.strip()):
            continue  # missing / non-string name: not a comparable place
        lat, lon = _get_coords(data)
        places.append(
            {
                "name": name,
                "filename": f.name,
                "PlaceID": data.get("PlaceID", ""),
                "lat": lat,
                "lon": lon,
                "coords_verified": _coords_verified(data),
            }
        )
    return places, seen_filenames


def _load_index_places(places_dir: Path, seen_filenames: set) -> list:
    """Entries from index.json that have no local file (coords from the shared cache)."""
    index_file = places_dir / "index.json"
    if not index_file.exists():
        return []
    coords_cache = _load_coords_cache(places_dir)
    try:
        raw = json.loads(index_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    places = []
    for name, filename in raw.items():
        if filename in seen_filenames or filename in SKIP_FILES:
            continue
        cached = coords_cache.get(filename, {})
        places.append(
            {
                "name": name.replace("_", " "),
                "filename": filename,
                "PlaceID": cached.get("PlaceID", ""),
                "lat": cached.get("lat"),
                "lon": cached.get("lon"),
            }
        )
    return places


def _load_places(places_dir: Path) -> list:
    """Load place data from local files + index.json for non-local entries."""
    places, seen_filenames = _load_local_places(places_dir)
    return places + _load_index_places(places_dir, seen_filenames)


def _find_cluster(i, p1, places, seen):
    """Find all places that match p1. Returns (cluster, reasons)."""
    cluster = [p1]
    reasons = set()

    for j, p2 in enumerate(places[i + 1 :], i + 1):
        if j in seen:
            continue
        match, match_reasons = _check_match(p1, p2)
        if match:
            cluster.append(p2)
            reasons.update(match_reasons)
            seen.add(j)

    return cluster, reasons


def _check_match(p1, p2):
    """Check if two places are potential duplicates. Returns (is_match, reasons)."""
    sim = _similarity(p1["name"], p2["name"])
    dist = _place_distance(p1, p2)

    if dist is not None and dist > MAX_DISTANCE_KM:
        return False, set()

    near = dist is not None and dist < 5
    # Only trust proximity as corroborating evidence when BOTH coordinates are verified; index-only
    # entries (no record loaded) are conservatively treated as unverified.
    verified = bool(p1.get("coords_verified")) and bool(p2.get("coords_verified"))
    return _evaluate_match(sim, dist, near, verified=verified)


def _place_distance(p1, p2):
    """Calculate distance between two places, or None if coords missing."""
    if p1["lat"] is not None and p2["lat"] is not None:
        return _haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
    return None


def _evaluate_match(sim: float, dist, near: bool, verified: bool = True):
    """Evaluate whether similarity and distance indicate a match."""
    reasons: set = set()
    qual = "" if verified else " (approximate coords)"
    if sim >= 0.65:
        reasons.add(f"name similarity {sim:.0%}")
        if near:
            reasons.add(f"within 5km{qual}")
        elif dist is not None:
            reasons.add(f"{dist:.0f}km apart{qual}")
        return True, reasons
    if near and sim >= 0.4 and not verified:
        # Countable, not silent: re-evaluated once Phase 3 geocoding verifies the coords.
        logger.debug("OBSERV place_dedup.weak_suppressed_unverified sim=%.2f", sim)
        _SUPPRESSED_UNVERIFIED[0] += 1
        return False, set()
    if near and sim >= 0.4:
        reasons.add(f"nearby + partial name match {sim:.0%}")
        return True, reasons
    return False, set()


def generate_duplicate_report(places_dir: Path, output_file: Path) -> None:
    """Generate duplicate places report."""
    duplicates = find_duplicate_places(places_dir)

    # Filter out entries whose files don't exist
    from src.dedup.validation import validate_report_groups

    duplicates = validate_report_groups(duplicates, places_dir)

    report = {
        "total_places": len(
            [f for f in places_dir.glob("*.json") if f.name not in SKIP_FILES]
        ),
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Found %d potential duplicate place groups", len(duplicates))
    print(f"\nTotal places: {report['total_places']}")
    print(f"Duplicate groups found: {len(duplicates)}")
    for i, dup in enumerate(duplicates[:10], 1):
        print(
            f"\n{i}. Confidence: {dup['confidence']:.2f} ({', '.join(dup['reasons'])})"
        )
        for p in dup["people"]:
            print(f"   - {p['name']} ({p['filename']})")
    if len(duplicates) > 10:
        print(f"\n... and {len(duplicates) - 10} more groups")


def main() -> int:
    """CLI / ECS entry point: write output/places/duplicate_report.json. Returns exit code."""
    logging.basicConfig(level=logging.INFO)

    places_dir = PROJECT_ROOT / "output/places"
    output_file = places_dir / "duplicate_report.json"

    if not places_dir.exists():
        logger.error("Places directory not found: %s", places_dir)
        return 1

    generate_duplicate_report(places_dir, output_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
