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
TRANSLATE = (
    False  # when True, prompts return verbatim foreign label + English/modern name
)


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
    kind = (
        "a WWII GERMAN operational/situation map (Lage/Feindlage; labels in German)"
        if TRANSLATE
        else "a WWII US Army 'Green Book' operational map"
    )
    tr = (
        " Labels are in German: for every label give BOTH the verbatim German text AND its "
        "English/modern equivalent (e.g. 'Feindlage West'→'Enemy situation, West'; "
        "'Köln'→'Cologne')."
        if TRANSLATE
        else ""
    )
    prompt = (
        f"This is {kind}. Read the MAP NUMBER, TITLE, the LEGEND, and any ELEVATION and "
        f"DISTANCE scales.{tr} Return STRICT JSON only: "
        '{"map_number": <e.g. "MAP III" or null>, "title": <verbatim or null>, '
        '"title_en": <English translation of the title or null>, '
        '"legend": [{"symbol_description": <e.g. "solid red line">, '
        '"meaning": <verbatim legend text>, "meaning_en": <English or null>, '
        '"date_text": <date in the entry or null>}], '
        '"elevation_scale": <verbatim or null>, "distance_scale": <verbatim or null>}'
    )
    return _vision(client, prompt, full)


def extract_tile(client: GrokClient, tile: Image.Image, legend: dict, tid: str) -> dict:
    legend_txt = json.dumps(legend.get("legend", []))[:1200]
    if TRANSLATE:
        head = (
            "WWII GERMAN operational/situation map TILE (a crop; labels in German). On a "
            "'Feindlage' (enemy-situation) map the plotted units are the GERMAN assessment "
            "of ALLIED (enemy) forces. For PLACES give the verbatim German name AND the "
            "English/modern name. For units, read German-notation labels.\n"
        )
        places_field = '"places": [{"label": <verbatim German name>, "name_en": <English/modern or null>}], '
    else:
        head = (
            "WWII US Army 'Green Book' operational map TILE (a crop of a larger map). "
        )
        places_field = '"places": [<town/city/village names, verbatim>], '
    prompt = (
        head + f"Use this LEGEND to interpret colors/line styles:\n{legend_txt}\n\n"
        "Units use NATO/APP-6 military symbology. For each unit symbol read the SYMBOL, "
        "not just the text label: the FRAME color/shape gives affiliation; the TICKS above "
        "the frame give echelon (XXXX=army, XXX=corps, XX=division, X=brigade, "
        "III=regiment, II=battalion); the ICON inside gives branch.\n"
        "Extract ONLY what is visibly in THIS tile. STRICT JSON only:\n"
        "{" + places_field + '"units": [{"label": <verbatim unit label>, '
        '"affiliation": <"friend"|"hostile"|"unknown" from the FRAME>, '
        '"echelon": <"army"|"corps"|"division"|"brigade"|"regiment"|"battalion"|'
        '"combat_command"|null from the TICKS>, '
        '"branch": <"infantry"|"armored"|"cavalry"|"artillery"|"airborne"|null from the ICON>}], '
        '"fortifications": [<verbatim labels>], '
        '"rivers": [<verbatim>], "roads_railroads": [<verbatim>]}'
    )
    r = _vision(client, prompt, tile)
    r["_tile"] = tid
    return r


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _group_affiliation(name: str) -> str:
    """Infer friend/hostile from a group name's nationality signal (coarse, for the
    symbology veto). German/SS/Volksgrenadier/Panzer -> hostile; else unknown."""
    from src.dedup.unit_key import _NON_US_SIGNAL

    n = (name or "").lower()
    if _NON_US_SIGNAL.search(n) and not any(
        a in n for a in ("british", "canadian", "french", "polish", "soviet", "us ")
    ):
        # _NON_US_SIGNAL also catches british/soviet/etc; restrict "hostile" to the
        # German-family markers that actually appear as the enemy on ETO maps.
        if any(
            g in n
            for g in (
                "german",
                "ss",
                "volksgrenadier",
                "volks grenadier",
                "panzer",
                "vg",
                "wehrmacht",
                "waffen",
            )
        ):
            return "hostile"
    return "unknown"


def _load_group_unitkey_index(groups_dir: Path):
    """[(GroupID, name, UnitKey, affiliation)] over all people_groups records, for
    symbology-constrained matching via the SHARED unit_key."""
    from src.dedup.unit_key import derive_unit_key

    idx = []
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
        name = d.get("name") or d.get("common_name") or d.get("group_name") or ""
        if not name:
            continue
        # nationality field is authoritative when present; else infer from the name
        nat = (d.get("nationality") or d.get("country_of_origin") or "").lower()
        aff = (
            "hostile"
            if any(g in nat for g in ("german", "germany"))
            else (
                "friend"
                if any(
                    a in nat
                    for a in ("us", "united states", "american", "british", "allied")
                )
                else _group_affiliation(name)
            )
        )
        idx.append((gid, name, derive_unit_key(name), aff))
    return idx


_ECHELON_ALIAS = {
    "combat_command": "combat_command",
    "division": "division",
    "regiment": "regiment",
    "corps": "corps",
    "battalion": "battalion",
    "brigade": "brigade",
    "army": "army",
}


def _resolve_unit(label: str, affiliation, echelon, group_index, branch=None):
    """Resolve a map unit label -> GroupID using the shared unit_key, constrained by the
    symbology-derived affiliation + echelon (veto cross-side / cross-echelon matches).
    Returns (GroupID or None, n_candidates_before_constraint)."""
    from src.dedup.unit_key import derive_unit_key, unit_keys_match

    k = derive_unit_key(label)
    raw = [
        (gid, nm, aff, gk)
        for (gid, nm, gk, aff) in group_index
        if unit_keys_match(k, gk)[0]
    ]
    before = len({gid for gid, *_ in raw})
    cand = raw
    # Affiliation veto: a hostile map unit cannot be a friendly entity, and vice versa.
    if affiliation in ("friend", "hostile"):
        cand = [c for c in cand if c[2] == affiliation or c[2] == "unknown"]
    # Echelon veto: if the symbol gives an echelon and the entity's key has one, they
    # must agree (corps label must not resolve to a regiment).
    if echelon in _ECHELON_ALIAS:
        want = _ECHELON_ALIAS[echelon]
        cand = [c for c in cand if (c[3].echelon is None or c[3].echelon == want)]
    gids = {gid for gid, *_ in cand}
    if len(gids) == 1:
        return next(iter(gids)), before
    if not gids:
        return None, before
    # Multiple candidates: if they all share ONE canonical unit key, they are unmerged
    # duplicate records of the SAME real unit (a people_groups dedup gap) — resolving to
    # any one is correct. Pick deterministically (lowest GroupID).
    keys = {gk for (_gid, _nm, _aff, gk) in cand}
    if len(keys) == 1:
        return sorted(gids)[0], before
    # Genuinely distinct units remain: branch is a SOFT tiebreaker (rank, never veto) —
    # doctrinal names (Volksgrenadier/SS/Panzer) legitimately diverge from frame icons.
    if branch:
        narrowed = {gid for (gid, _nm, _aff, gk) in cand if gk.arm == branch}
        if len(narrowed) == 1:
            return next(iter(narrowed)), before
    return None, before


def resolve_features(fc: dict, places_dir: Path, groups_dir: Path) -> dict:
    """Resolve each feature's verbatim label to PlaceID/GroupID via the SHARED matchers,
    then re-dedup on the resolved id. Unresolved -> null id, verbatim label preserved
    (null over guess). Units use the symbology-constrained unit_key resolver. Mutates +
    returns fc with a resolution_report."""
    from src.extraction.places import _build_place_name_index
    from src.extraction.weather_central import _match_place_id

    place_index, _ = _build_place_name_index(places_dir)
    group_index = _load_group_unitkey_index(groups_dir)

    seen_place, seen_group, out = {}, {}, []
    stats = {
        "place_resolved": 0,
        "place_null": 0,
        "group_resolved": 0,
        "group_null": 0,
        "group_narrowed_by_symbology": 0,
        "merged_on_id": 0,
    }

    for f in fc["features"]:
        p = f["properties"]
        label = p["original_label"]
        kind = p["feature_kind"]

        if kind == "place":
            pid = _match_place_id(label, place_index)
            if not pid and p.get("additionalInformation"):
                # German label didn't match — try the English/modern equivalent.
                pid = _match_place_id(p["additionalInformation"], place_index)
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
            gid, before = _resolve_unit(
                label,
                p.get("affiliation"),
                p.get("echelon"),
                group_index,
                branch=p.get("branch"),
            )
            p["GroupID"] = gid
            stats["group_resolved" if gid else "group_null"] += 1
            if before > 1 and gid:
                stats["group_narrowed_by_symbology"] += 1
            key = gid or f"name:{_norm(label)}"
            if key in seen_group:
                stats["merged_on_id"] += 1
                continue
            seen_group[key] = f
        out.append(f)

    fc["features"] = out
    fc["resolution_report"] = stats
    return fc


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_legend_dates(legend, default_year=1944):
    """Pull ISO dates from legend date_text/meaning (e.g. '16-19 DEC' -> 1944-12-16,
    1944-12-19). Returns a sorted list of ISO date strings found."""
    found = set()
    for item in legend or []:
        txt = f"{item.get('date_text') or ''} {item.get('meaning') or ''}".lower()
        mon = next((v for k, v in _MONTHS.items() if k in txt), None)
        if mon is None:
            continue
        # day numbers (handle ranges like '16-19')
        for d in re.findall(r"\b(\d{1,2})\b", txt):
            day = int(d)
            if 1 <= day <= 31:
                found.add(f"{default_year:04d}-{mon:02d}-{day:02d}")
    return sorted(found)


def derive_extent(fc: dict) -> dict:
    """Stamp coverage extent on the FeatureCollection for reverse-registration:
    covered_places = resolved PlaceIDs of place/anchor features; date_range =
    earliest/latest date parsed from the dated legend. Enables 'any narrative entity at a
    (PlaceID, DateID) inside this extent links back to this map'."""
    pids = sorted(
        {
            f["properties"].get("PlaceID")
            for f in fc.get("features", [])
            if f["properties"].get("PlaceID")
        }
    )
    fc["covered_places"] = pids
    dates = _parse_legend_dates(fc.get("legend"))
    fc["date_range"] = {"earliest": dates[0], "latest": dates[-1]} if dates else None
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
            label = p.get("label") if isinstance(p, dict) else p
            name_en = p.get("name_en") if isinstance(p, dict) else None
            k = _norm(label)
            if k and k not in places:
                places[k] = {
                    "original_label": label,
                    "name_en": name_en,
                    "tiles": [tid],
                }
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
                    "branch": (u.get("branch") if isinstance(u, dict) else None),
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
        feat("place", rec, additionalInformation=rec.get("name_en"))
    for rec in units.values():
        feat(
            "unit_position",
            rec,
            affiliation=rec.get("affiliation"),
            echelon=rec.get("echelon"),
            branch=rec.get("branch"),
        )
    for rec in forts.values():
        feat("fortification", rec)

    return {
        "type": "FeatureCollection",
        "MapID": None,  # prototype — not yet a catalog MapID
        "map_number": map_meta.get("map_number"),
        "map_title": map_meta.get("title"),
        "title_en": map_meta.get("title_en"),
        "legend": map_meta.get("legend", []),
        "elevation_scale": map_meta.get("elevation_scale"),
        "distance_scale": map_meta.get("distance_scale"),
        "features": feats,
    }


def main() -> int:
    load_dotenv(dotenv_path=".env")
    global MAP_PATH, OUT_PATH, TRANSLATE
    args = [a for a in sys.argv[1:] if a != "--translate"]
    if "--translate" in sys.argv:
        TRANSLATE = True
    if args:
        MAP_PATH = Path(args[0])
        OUT_PATH = (
            Path(args[1]) if len(args) > 1 else MAP_PATH.with_suffix(".features.json")
        )
    if not MAP_PATH.exists():
        print("map image missing:", MAP_PATH)
        return 1
    print(f"input: {MAP_PATH}  translate={TRANSLATE}")
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

    fc = derive_extent(fc)
    print(
        f"   extent: {len(fc.get('covered_places') or [])} covered places,"
        f" date_range={fc.get('date_range')}"
    )

    print("5) validate against enforced map_features schema...")
    import jsonschema
    from src.schemas.map_features_output import MAP_FEATURES_OUTPUT_SCHEMA

    try:
        jsonschema.validate(fc, MAP_FEATURES_OUTPUT_SCHEMA)
        print("   schema: VALID")
    except jsonschema.ValidationError as e:
        print(f"   schema: INVALID -> {e.message} at {list(e.absolute_path)}")

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
