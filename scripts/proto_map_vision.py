#!/usr/bin/env python3
"""Tier-1 Grok-vision map-interior extraction PROTOTYPE (not wired into the pipeline).

Downloads the Ardennes 'Map III' (Green Book), reads the legend once globally, then
extracts features from overlapping tiles, and merges tile outputs into ONE deduped
GeoJSON FeatureCollection matching docs/current/features/maps/MAP_FEATURES_SCHEMA.md.

Coordinates are intentionally LEFT NULL here: geocoding is owned by the places subsystem
(resolved via PlaceID), not by map vision. This prototype proves the EXTRACTION tier only.

Usage: python scripts/proto_map_vision.py
"""

import base64
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from src.grok_client import GrokClient  # noqa: E402

MAP_PATH = Path("output/map_proto/map_III.jpg")
OUT_PATH = Path("output/map_proto/map_III.features.json")
MAX_TILE_PX = 1100  # downscale each tile's long edge to control payload


def _b64(img: Image.Image) -> str:
    img = img.convert("RGB")
    if max(img.size) > MAX_TILE_PX:
        img.thumbnail((MAX_TILE_PX, MAX_TILE_PX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _vision(client: GrokClient, prompt: str, img: Image.Image) -> dict:
    try:
        r = client.extract_json_with_image_base64(
            prompt=prompt,
            image_base64=_b64(img),
            cache_type="default",
            temperature=0.0,
            use_cache=True,
        )
        return r if isinstance(r, dict) else {}
    except Exception as e:  # pragma: no cover - prototype
        print("  vision call failed:", str(e)[:160])
        return {}


def read_legend(client: GrokClient, full: Image.Image) -> dict:
    prompt = (
        "This is a WWII US Army 'Green Book' operational map. Read the MAP NUMBER, TITLE, "
        "the LEGEND, and any ELEVATION and DISTANCE scales. Return STRICT JSON only: "
        '{"map_number": <e.g. "MAP III" or null>, "title": <or null>, '
        '"legend": [{"symbol_description": <e.g. "solid red line">, '
        '"meaning": <verbatim legend text>, "date_text": <date in the entry or null>}], '
        '"elevation_scale": <verbatim elevation legend, e.g. "ELEVATIONS IN METERS '
        '0 400 500 600 AND ABOVE" or null>, '
        '"distance_scale": <verbatim distance/bar scale, e.g. "0 1 2 3 MILES / '
        '0 1 2 3 KILOMETERS" or null>}'
    )
    return _vision(client, prompt, full)


def extract_tile(client: GrokClient, tile: Image.Image, legend: dict, tid: str) -> dict:
    legend_txt = json.dumps(legend.get("legend", []))[:1200]
    prompt = (
        "WWII US Army 'Green Book' operational map TILE (a crop of a larger map). "
        f"Use this LEGEND to interpret colors/line styles:\n{legend_txt}\n\n"
        "Extract ONLY what is visibly in THIS tile. STRICT JSON only:\n"
        '{"places": [<town/city/village names, verbatim>], '
        '"units": [{"label": <verbatim unit label e.g. "423 INF" or "18 VG">, '
        '"affiliation": <"friend"|"hostile"|"unknown" (US/Allied=friend, German=hostile)>, '
        '"echelon": <"division"|"regiment"|"corps"|"combat_command"|"battalion"|null>}], '
        '"fortifications": [<verbatim labels, e.g. "WEST WALL">], '
        '"rivers": [<verbatim>], "roads_railroads": [<verbatim>]}'
    )
    r = _vision(client, prompt, tile)
    r["_tile"] = tid
    return r


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load_group_index(groups_dir: Path):
    """name/common_name/group_name -> GroupID (lowercase)."""
    idx = {}
    if not groups_dir.exists():
        return idx
    for gf in groups_dir.glob("*.json"):
        if "index" in gf.name:
            continue
        try:
            d = json.loads(gf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        gid = d.get("GroupID")
        if not gid:
            continue
        for key in (d.get("name"), d.get("common_name"), d.get("group_name")):
            if key:
                idx[key.lower()] = gid
    return idx


def resolve_features(fc: dict, places_dir: Path, groups_dir: Path) -> dict:
    """Resolve each feature's verbatim label to PlaceID/GroupID via the SHARED matchers,
    then re-dedup on the resolved id. Unresolved -> null id, verbatim label preserved
    (null over guess). Mutates + returns fc with a resolution_report."""
    from src.extraction.places import _build_place_name_index
    from src.extraction.weather_central import _match_place_id

    place_index, _ = _build_place_name_index(places_dir)
    group_index = _load_group_index(groups_dir)

    seen_place, seen_group, out = {}, {}, []
    stats = {
        "place_resolved": 0,
        "place_null": 0,
        "group_resolved": 0,
        "group_null": 0,
        "merged_on_id": 0,
    }

    for f in fc["features"]:
        p = f["properties"]
        label = p["original_label"]
        kind = p["feature_kind"]

        if kind == "place":
            pid = _match_place_id(label, place_index)
            p["PlaceID"] = pid
            stats["place_resolved" if pid else "place_null"] += 1
            key = pid or f"name:{_norm(label)}"
            if key in seen_place:
                prev = seen_place[key]["properties"]
                prev["tile_id"] = ",".join(
                    sorted(set(prev["tile_id"].split(",") + p["tile_id"].split(",")))
                )
                stats["merged_on_id"] += 1
                continue
            seen_place[key] = f
        elif kind == "unit_position":
            gid = group_index.get(label.lower())
            p["GroupID"] = gid
            stats["group_resolved" if gid else "group_null"] += 1
            key = gid or f"name:{_norm(label)}"
            if key in seen_group:
                stats["merged_on_id"] += 1
                continue
            seen_group[key] = f
        out.append(f)

    fc["features"] = out
    fc["resolution_report"] = stats
    return fc


def tiles(full: Image.Image, nx: int = 2, ny: int = 2, overlap: float = 0.12):
    w, h = full.size
    tw, th = w // nx, h // ny
    ox, oy = int(tw * overlap), int(th * overlap)
    out = []
    for iy in range(ny):
        for ix in range(nx):
            left = max(0, ix * tw - ox)
            upper = max(0, iy * th - oy)
            right = min(w, (ix + 1) * tw + ox)
            lower = min(h, (iy + 1) * th + oy)
            out.append((f"r{iy}c{ix}", full.crop((left, upper, right, lower))))
    return out


def to_feature_collection(map_meta: dict, tile_results: list) -> dict:
    """Merge tiles into ONE deduped FeatureCollection (collapse by normalized name)."""
    places: dict = {}
    units: dict = {}
    forts: dict = {}

    for tr in tile_results:
        tid = tr.get("_tile", "?")
        for p in tr.get("places", []) or []:
            k = _norm(p)
            if k and k not in places:
                places[k] = {"original_label": p, "tiles": [tid]}
            elif k:
                places[k]["tiles"].append(tid)
        for u in tr.get("units", []) or []:
            label = u.get("label") if isinstance(u, dict) else u
            k = _norm(label)
            if not k:
                continue
            if k not in units:
                units[k] = {
                    "original_label": label,
                    "affiliation": (
                        u.get("affiliation") if isinstance(u, dict) else None
                    ),
                    "echelon": (u.get("echelon") if isinstance(u, dict) else None),
                    "tiles": [tid],
                }
            else:
                units[k]["tiles"].append(tid)
        for f in tr.get("fortifications", []) or []:
            k = _norm(f)
            if k and k not in forts:
                forts[k] = {"original_label": f, "tiles": [tid]}

    feats = []

    def feat(kind, rec, **props):
        feats.append(
            {
                "type": "Feature",
                "geometry": None,  # null: coordinates come from resolved PlaceID, not pixels
                "properties": {
                    "feature_kind": kind,
                    "original_label": rec["original_label"],
                    "source": "map",
                    "PlaceID": None,
                    "GroupID": None,
                    "DateID": None,
                    "coordinate_source": "none",
                    "tile_id": ",".join(sorted(set(rec["tiles"]))),
                    **props,
                },
            }
        )

    for rec in places.values():
        feat("place", rec)
    for rec in units.values():
        feat(
            "unit_position",
            rec,
            affiliation=rec.get("affiliation"),
            echelon=rec.get("echelon"),
        )
    for rec in forts.values():
        feat("fortification", rec)

    return {
        "type": "FeatureCollection",
        "MapID": None,  # prototype — not yet a catalog MapID
        "map_number": map_meta.get("map_number"),
        "map_title": map_meta.get("title"),
        "legend": map_meta.get("legend", []),
        "elevation_scale": map_meta.get("elevation_scale"),
        "distance_scale": map_meta.get("distance_scale"),
        "features": feats,
    }


def main() -> int:
    load_dotenv(dotenv_path=".env")
    if not MAP_PATH.exists():
        print("map image missing:", MAP_PATH)
        return 1
    full = Image.open(MAP_PATH)
    client = GrokClient(cache_dir=Path("cache/api"))

    print("1) global legend pass...")
    legend = read_legend(client, full.copy())
    print(
        "   map_number:",
        legend.get("map_number"),
        "| legend items:",
        len(legend.get("legend", []) or []),
    )

    print("2) tiled feature extraction (2x2, overlap)...")
    results = []
    for tid, tile in tiles(full):
        r = extract_tile(client, tile, legend, tid)
        print(
            f"   tile {tid}: places={len(r.get('places',[]) or [])} "
            f"units={len(r.get('units',[]) or [])} forts={len(r.get('fortifications',[]) or [])}"
        )
        results.append(r)

    print("3) merge -> unified deduped FeatureCollection...")
    fc = to_feature_collection(legend, results)

    print("4) resolve labels -> PlaceID/GroupID + re-dedup on resolved id...")
    fc = resolve_features(fc, Path("output/places"), Path("output/people_groups"))
    print("   resolution:", fc["resolution_report"])

    OUT_PATH.write_text(json.dumps(fc, indent=2, ensure_ascii=False), encoding="utf-8")

    kinds: dict = {}
    for f in fc["features"]:
        kinds[f["properties"]["feature_kind"]] = (
            kinds.get(f["properties"]["feature_kind"], 0) + 1
        )
    print("\n=== UNIFIED RESULT ===")
    print("map_number:", fc["map_number"])
    print("title:", fc["map_title"])
    print("legend items:", len(fc["legend"]))
    print("feature counts:", kinds)
    print("total features:", len(fc["features"]))
    print("written:", OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
