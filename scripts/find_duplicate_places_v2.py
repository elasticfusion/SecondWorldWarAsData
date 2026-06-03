#!/usr/bin/env python3
"""Identify possible duplicate places based on name similarity and coordinates."""

import json
import logging
import sys
import unicodedata
from difflib import SequenceMatcher
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

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


def _similarity(a: str, b: str) -> float:
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
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371


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


def find_duplicate_places(places_dir: Path) -> List[Dict]:
    """Find potential duplicate places."""
    places = _load_places(places_dir)
    excluded_pairs, excluded_names = _load_exclusions(places_dir)

    duplicates: List[Dict[str, Any]] = []
    seen: set = set()

    for i, p1 in enumerate(places):
        if i in seen:
            continue
        cluster, reasons = _find_cluster(i, p1, places, seen)
        if len(cluster) >= 2:
            seen.add(i)
            duplicates.append(_build_group(cluster, reasons))

    duplicates.sort(key=lambda x: float(x["confidence"]), reverse=True)
    return _filter_excluded(duplicates, excluded_pairs, excluded_names)


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


def _load_places(places_dir: Path) -> list:
    """Load place data from local files + index.json for non-local entries."""
    places = []
    seen_filenames: set = set()
    for f in sorted(places_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        seen_filenames.add(f.name)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = data.get(
                "current_name", data.get("place_name", data.get("name", ""))
            )
            if name:
                lat, lon = _get_coords(data)
                places.append(
                    {
                        "name": name,
                        "filename": f.name,
                        "PlaceID": data.get("PlaceID", ""),
                        "lat": lat,
                        "lon": lon,
                    }
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", f.name, e)

    # Add entries from index.json that aren't local
    index_file = places_dir / "index.json"
    if index_file.exists():
        try:
            raw = json.loads(index_file.read_text(encoding="utf-8"))
            for name, filename in raw.items():
                if filename not in seen_filenames and filename not in SKIP_FILES:
                    places.append(
                        {
                            "name": name.replace("_", " "),
                            "filename": filename,
                            "PlaceID": "",
                            "lat": None,
                            "lon": None,
                        }
                    )
        except (json.JSONDecodeError, OSError):
            pass

    return places


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
    return _evaluate_match(sim, dist, near)


def _place_distance(p1, p2):
    """Calculate distance between two places, or None if coords missing."""
    if p1["lat"] is not None and p2["lat"] is not None:
        return _haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
    return None


def _evaluate_match(sim: float, dist, near: bool):
    """Evaluate whether similarity and distance indicate a match."""
    reasons: set = set()
    if sim >= 0.65:
        reasons.add(f"name similarity {sim:.0%}")
        if near:
            reasons.add("within 5km")
        elif dist is not None:
            reasons.add(f"{dist:.0f}km apart")
        return True, reasons
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    script_dir = Path(__file__).parent
    project_root = script_dir.parent if script_dir.name == "scripts" else script_dir

    places_dir = project_root / "output/places"
    output_file = places_dir / "duplicate_report.json"

    if not places_dir.exists():
        logger.error("Places directory not found: %s", places_dir)
        sys.exit(1)

    generate_duplicate_report(places_dir, output_file)
