"""Golden-output test for the dedup detectors (behaviour lock for the src/scripts move).

Pins the exact duplicate / related-groups reports the dedup detectors produce for a fixed synthetic
corpus, so that moving them from ``scripts/`` to ``src/dedup/`` (and the complexity refactors that
follow, docs/current/design/SRC_SCRIPTS_SEPARATION.md D4) cannot silently change a dedup decision.
A changed decision fails this test; an intended change is accepted by regenerating the golden file:

    UPDATE_DEDUP_GOLDEN=1 python -m pytest tests/unit/test_dedup_golden.py

and reviewing the JSON diff in the same commit.

What it pins: which records group together, each group's confidence, and the reason strings.
What it does NOT pin: the order of lists (groups, members, reasons). Reports are canonicalised
(lists sorted, floats rounded to 6 dp) so filesystem/hash order cannot make it flaky.

It records CURRENT behaviour, not correct behaviour. Some pinned decisions are known to be wrong
(e.g. John Smith grouped with Walter Bedell Smith; the bare surname "Marshall" grouped with George
Marshall; Panzer Lehr clustered with 2nd Panzer Division in related groups). Fixing one of those is
an intended change: regenerate and review the diff.

The run reads committed repo data (config/equipment_aliases.yaml, data/surname_frequency.yaml,
data/unit_nicknames.yaml), so editing those files can legitimately change the golden.

Not covered: find_duplicate_people._check_proximity has no callers (dead code), so it and the two
helpers only it calls (_names_within_distance, _get_person_titles) cannot be exercised. The LLM
verification paths are deliberately disabled.

Isolation: no AWS, no Grok, no network. The run uses built-in dedup defaults (not the local
config.yaml), the local exclusion store, a full (non-incremental) comparison, and the repo root as
cwd (the lookup tables above resolve relative to it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# The modules under test (moved from scripts/ in PR 1 with the golden file unchanged byte-for-byte).
# A pure move or refactor must not change the golden file.
import src.dedup.cleanup_indexes as cleanup_indexes
import src.dedup.find_duplicate_equipment as dup_equipment
import src.dedup.find_duplicate_groups as dup_groups
import src.dedup.find_duplicate_people as dup_people
import src.dedup.find_duplicate_places_v2 as dup_places
import src.dedup.find_related_groups as related_groups
import src.dedup.reclassify_military_units as reclassify

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "dedup_golden" / "expected.json"

# --------------------------------------------------------------------------- fixture corpus

_ULID_PAD = "0" * 26


def _id(prefix: str, n: int) -> str:
    """A schema-shaped (26-char Crockford) stable id: deterministic across runs."""
    body = (
        f"{prefix}{n:04d}".upper().replace("I", "J").replace("L", "K").replace("O", "P")
    )
    body = body.replace("U", "V")
    return (body + _ULID_PAD)[:26]


_SUB_EVENTS = {
    "SE1": "General Omar N. Bradley commanded First Army. Bradley conferred with Gerow of V "
    "Corps before the landing. Later Omar Bradley visited the front with Lt. Gen. Gerow.",
    "SE2": "General Bradley ordered the attack. Walter Bedell Smith, chief of staff, approved "
    "the plan the same evening.",
    "SE3": "Walter B. Smith and Eisenhower met at SHAEF. Private John Smith carried the message.",
    "SE4": "Paul Hausser commanded Seventh Army; Hauser and Choltitz withdrew to Coutances. "
    "Dietrich von Choltitz agreed. Panzer Lehr and the 352d Division were shattered.",
    "SE5": "Günther von Kluge took command of OB WEST. Guenther von Kluge visited Hausser, and "
    "Hans von Kluge later criticised him.",
    "SE6": "Private John Smith of the 16th Infantry was wounded near Colleville; the 1st Infantry "
    "Division held the beach.",
    "SE7": "James Smith, a sergeant, led the patrol across the hedgerows.",
    "SE8": "Erich Brandenberger commanded Seventh Army in the Ardennes.",
    "SE9": "Leonard T. Gerow, commanding V Corps, landed at Omaha. Leonard Gerow later took the "
    "5th Corps staff forward.",
    # SE10: the two Marshall spellings are far apart (> the 400-word "near" radius) — loose band.
    "SE10": "George C. Marshall briefed the President. "
    + " ".join(["The staff reviewed shipping and replacement tables."] * 75)
    + " Later George Marshall approved the plan, and Marshall flew to London.",
    "SE11": "S.L.A. Marshall interviewed the companies after the battle.",
    # SE12: the two Cota spellings are close (< the 60-word "tight" radius).
    "SE12": "Norman Cota walked the beach at Vierville. Norman D. Cota then led the men inland.",
    "SE13": "Henry L. Stimson testified; Henry Stimson later met Sam Rayburn and Samuel Rayburn.",
    # SE14: a pair that WOULD group, suppressed by PEOPLE_EXCLUSIONS (proves exclusions load).
    "SE14": "Courtney H. Hodges took First Army; Courtney Hodges moved his command post forward.",
}

# Sub-event id strings must be ULID-shaped for the matchers; map the readable keys onto stable ids
# (numbered from the key, not its sort position, so adding sub-events does not renumber others).
_SEID = {k: _id("SE", int(k[2:])) for k in _SUB_EVENTS}
_EVENT_ID = _id("EV", 1)


def _stable_n(*parts: Any) -> int:
    """Deterministic small int from parts (built-in hash() is randomised per process)."""
    import hashlib

    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 10000


def _mentions(seids: List[str], **fields: Any) -> List[Dict[str, Any]]:
    return [
        {
            "MentionID": _id("MN", _stable_n(k, sorted(fields.items()))),
            "Event_Name": "Test Event",
            "EventID": _EVENT_ID,
            "Sub_event_Name": f"Sub-event {k}",
            "Sub_eventID": _SEID[k],
            "book": "TestBook",
            **fields,
        }
        for k in seids
    ]


def _person(
    n: int,
    name: str,
    nat: str,
    rank: str,
    seids: List[str],
    position: str = "",
    bio: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "name": name,
        "nationality": nat,
        "PersonID": _id("PE", n),
        "event_mentions": _mentions(
            seids, nationality=nat, rank=rank, position_at_event=position or None
        ),
    }
    if rank:
        rec["rank"] = rank
    if bio:
        rec["biographical_profile"] = bio
    return rec


_COTA_DIV = _id(
    "GR", 90
)  # the 29th Infantry Division GroupID both Cota records reference


PEOPLE = {
    "omar_n_bradley.json": _person(
        1,
        "Omar N. Bradley",
        "USA",
        "Lieutenant General",
        ["SE1", "SE2"],
        "Commanding General, First Army",
    ),
    "omar_bradley.json": _person(2, "Omar Bradley", "USA", "General", ["SE1"]),
    "general_bradley.json": _person(3, "General Bradley", "USA", "General", ["SE2"]),
    "bradley.json": _person(4, "Bradley", "USA", "", ["SE1"]),
    "walter_bedell_smith.json": _person(
        5,
        "Walter Bedell Smith",
        "USA",
        "Lieutenant General",
        ["SE2", "SE3"],
        "Chief of Staff, SHAEF",
    ),
    "walter_b_smith.json": _person(
        6, "Walter B. Smith", "USA", "Lieutenant General", ["SE3"]
    ),
    "paul_hausser.json": _person(
        7, "Paul Hausser", "Germany", "General der Waffen-SS", ["SE4", "SE5"]
    ),
    "paul_hauser.json": _person(8, "Paul Hauser", "Germany", "General", ["SE4"]),
    "dietrich_von_choltitz.json": _person(
        9, "Dietrich von Choltitz", "Germany", "General der Infanterie", ["SE4"]
    ),
    "gunther_von_kluge.json": _person(
        10, "Günther von Kluge", "Germany", "Generalfeldmarschall", ["SE5"]
    ),
    "guenther_von_kluge.json": _person(
        11, "Guenther von Kluge", "Germany", "Field Marshal", ["SE5"]
    ),
    "hans_von_kluge.json": _person(
        12, "Hans von Kluge", "Germany", "Generalfeldmarschall", ["SE5"]
    ),
    "john_smith.json": _person(13, "John Smith", "USA", "Private", ["SE6", "SE3"]),
    "james_smith.json": _person(14, "James Smith", "USA", "Sergeant", ["SE7"]),
    "erich_brandenberger.json": _person(
        15, "Erich Brandenberger", "Germany", "General der Panzertruppe", ["SE8"]
    ),
    "leonard_t_gerow.json": _person(
        16,
        "Leonard T. Gerow",
        "USA",
        "Major General",
        ["SE1", "SE9"],
        "Commanding General, V Corps",
    ),
    "leonard_gerow.json": _person(
        17, "Leonard Gerow", "USA", "Lieutenant General", ["SE9"]
    ),
    "lt_gen_gerow.json": _person(18, "Lt. Gen. Gerow", "USA", "", ["SE1"]),
    # Biographical-profile signals: exact birth-date match (Marshall), same nationality + birth
    # year with GroupID- and designation-matched units at different echelons (Cota), and civilian
    # group affiliations, verified (Stimson) vs date-unverified (Rayburn). Several single-name
    # "Marshall" records exercise the ambiguous-single-name limit.
    "george_c_marshall.json": _person(
        19,
        "George C. Marshall",
        "USA",
        "General of the Army",
        ["SE10"],
        "Chief of Staff of the Army",
        bio={
            "birth_date": "1880-12-31",
            "nationality": "USA",
            "units_served": [
                {"designation": "War Department General Staff", "echelon": "command"}
            ],
        },
    ),
    "george_marshall.json": _person(
        20,
        "George Marshall",
        "USA",
        "General",
        ["SE10"],
        bio={"birth_date": "1880-12-31", "nationality": "USA"},
    ),
    "sla_marshall.json": _person(
        21,
        "S.L.A. Marshall",
        "USA",
        "Lieutenant Colonel",
        ["SE11"],
        "Combat historian",
        bio={"birth_date": "1900-07-18", "nationality": "USA"},
    ),
    "marshall.json": _person(22, "Marshall", "USA", "", ["SE10"]),
    "norman_cota.json": _person(
        23,
        "Norman Cota",
        "USA",
        "Brigadier General",
        ["SE12"],
        "Assistant Division Commander",
        bio={
            "birth_date": "1893-05-30",
            "nationality": "USA",
            "units_served": [
                {
                    "GroupID": _COTA_DIV,
                    "designation": "29th Infantry Division",
                    "echelon": "division",
                },
                {"designation": "116th Infantry Regiment", "echelon": "regiment"},
            ],
        },
    ),
    "norman_d_cota.json": _person(
        24,
        "Norman D. Cota",
        "USA",
        "Brigadier General",
        ["SE12"],
        bio={
            "birth_date": "1893",
            "nationality": "USA",
            "units_served": [
                {
                    "GroupID": _COTA_DIV,
                    "designation": "29th Infantry Division",
                    "echelon": "division",
                },
                {"designation": "Company A, 116th Infantry", "echelon": "company"},
                {"unit": "116th Infantry Regiment"},
            ],
        },
    ),
    "henry_l_stimson.json": _person(
        25,
        "Henry L. Stimson",
        "USA",
        "",
        ["SE13"],
        "Secretary of War",
        bio={
            "group_affiliations": [
                {
                    "group": "War Department",
                    "group_kind": "executive",
                    "date_verified": True,
                }
            ]
        },
    ),
    "henry_stimson.json": _person(
        26,
        "Henry Stimson",
        "USA",
        "",
        ["SE13"],
        bio={
            "group_affiliations": [
                {
                    "group": "War Department",
                    "group_kind": "executive",
                    "date_verified": True,
                }
            ]
        },
    ),
    "sam_rayburn.json": _person(
        27,
        "Sam Rayburn",
        "USA",
        "",
        ["SE13"],
        "Speaker of the House",
        bio={
            "group_affiliations": [
                {"group": "House of Representatives", "group_kind": "legislature"}
            ]
        },
    ),
    "samuel_rayburn.json": _person(
        28,
        "Samuel Rayburn",
        "USA",
        "",
        ["SE13"],
        bio={
            "group_affiliations": [
                {"group": "House of Representatives", "group_kind": "legislature"}
            ]
        },
    ),
    "courtney_h_hodges.json": _person(
        29, "Courtney H. Hodges", "USA", "Lieutenant General", ["SE14"]
    ),
    "courtney_hodges.json": _person(
        30, "Courtney Hodges", "USA", "Lieutenant General", ["SE14"]
    ),
}
# An index-only (non-local) entry, as left by an earlier run on S3.
PEOPLE_INDEX = {"O. N. Bradley": "o_n_bradley.json"}
# A curated "not the same person" decision that must suppress an otherwise-matching pair. The
# Hodges pair groups when this file is absent (verified when the fixture was written), so its
# absence from the golden proves the exclusion store is loaded.
EXCLUDED_PAIR = ("courtney_h_hodges.json", "courtney_hodges.json")
PEOPLE_EXCLUSIONS = {
    "exclusions": [{"person1": EXCLUDED_PAIR[0], "person2": EXCLUDED_PAIR[1]}]
}


def _group(
    n: int, name: str, nat: str, seids: Optional[List[str]] = None
) -> Dict[str, Any]:
    return {
        "name": name,
        "group_name": name,
        "group_type": "military_unit",
        "nationality": nat,
        "GroupID": _id("GR", n),
        "event_mentions": _mentions(seids or [], nationality=nat),
    }


GROUPS = {
    "1st_infantry_division.json": _group(1, "1st Infantry Division", "USA", ["SE6"]),
    "1st_infantry.json": _group(2, "1st Infantry", "USA", ["SE6"]),
    "1st_inf_div.json": _group(3, "1st Inf Div", "USA"),
    "16th_infantry_regiment.json": _group(4, "16th Infantry Regiment", "USA", ["SE6"]),
    "16th_infantry.json": _group(5, "16th Infantry", "USA", ["SE6"]),
    "2nd_panzer_division.json": _group(6, "2nd Panzer Division", "Germany"),
    "2d_panzer_division.json": _group(7, "2d Panzer Division", "Germany"),
    "2nd_armored_division.json": _group(8, "2nd Armored Division", "USA"),
    "panzer_lehr_division.json": _group(9, "Panzer Lehr Division", "Germany", ["SE4"]),
    "panzer_lehr.json": _group(10, "Panzer Lehr", "Germany", ["SE4"]),
    "v_corps.json": _group(11, "V Corps", "USA", ["SE1", "SE9"]),
    "5th_corps.json": _group(12, "5th Corps", "USA", ["SE9"]),
    "first_army.json": _group(13, "First Army", "USA", ["SE1"]),
    "first_us_army.json": _group(14, "First U.S. Army", "USA", ["SE1"]),
    "seventh_army.json": _group(15, "Seventh Army", "Germany", ["SE4", "SE8"]),
    "7th_army.json": _group(16, "7th Army", "USA"),
    "352d_division.json": _group(17, "352d Division", "Germany", ["SE4"]),
    "352nd_infantry_division.json": _group(
        18, "352nd Infantry Division", "Germany", ["SE4"]
    ),
    "4th_cavalry_group.json": _group(19, "4th Cavalry Group", "USA"),
    "4th_cav_gp.json": _group(20, "4th Cav Gp", "USA"),
}


def _place(
    n: int, name: str, lat: Optional[float], lon: Optional[float]
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"current_name": name, "PlaceID": _id("PL", n)}
    if lat is not None and lon is not None:
        rec["coordinates"] = {"latitude": lat, "longitude": lon}
    return rec


PLACES = {
    "caen.json": _place(1, "Caen", 49.1829, -0.3707),
    "caen_france.json": _place(2, "Caen, France", 49.1830, -0.3700),
    "saint-lo.json": _place(3, "Saint-Lô", 49.1157, -1.0906),
    "st_lo.json": _place(4, "St. Lo", 49.1160, -1.0910),
    "st-lo.json": _place(5, "St-Lô", 49.1150, -1.0890),
    "marigny_manche.json": _place(6, "Marigny", 49.0994, -1.2489),
    "marigny_burgundy.json": _place(7, "Marigny", 46.79, 4.27),
    "avranches.json": _place(8, "Avranches", 48.6844, -1.3569),
    "avranche.json": _place(9, "Avranche", 48.6840, -1.3570),
    "mortain.json": _place(10, "Mortain", 48.6480, -0.9400),
    "vire.json": _place(11, "Vire", 48.8383, -0.8892),
    "vire_river.json": _place(12, "Vire River", None, None),
    "coutances.json": _place(13, "Coutances", 49.0475, -1.4453),
    "cherbourg-octeville.json": _place(14, "Cherbourg-Octeville", 49.6350, -1.6250),
}
PLACES_INDEX = {"Cherbourg": "cherbourg.json"}
PLACES_COORDS = {
    "cherbourg.json": {"lat": 49.6337, "lon": -1.6222, "PlaceID": _id("PL", 99)}
}


def _equip(n: int, name: str, category: str, country: str) -> Dict[str, Any]:
    return {
        "EquipmentID": _id("EQ", n),
        "common_name": name,
        "category": category,
        "country_of_origin": country,
        "event_mentions": [],
    }


EQUIPMENT = {
    "m4_sherman.json": _equip(1, "M4 Sherman", "tank", "USA"),
    "sherman.json": _equip(2, "Sherman", "tank", "USA"),
    "sherman_tank.json": _equip(3, "Sherman tank", "tank", "USA"),
    "m4a3_sherman.json": _equip(4, "M4A3 Sherman", "tank", "USA"),
    "sherman_firefly.json": _equip(5, "Sherman Firefly", "tank", "UK"),
    "panther.json": _equip(6, "Panther", "tank", "Germany"),
    "panzer_v_panther.json": _equip(7, "Panzer V Panther", "tank", "Germany"),
    "tiger_i.json": _equip(8, "Tiger I", "tank", "Germany"),
    "tiger.json": _equip(9, "Tiger", "tank", "Germany"),
    "50_caliber_mg.json": _equip(10, ".50-caliber machine gun", "small_arms", "USA"),
    "50_cal_mg.json": _equip(11, "50 cal machine gun", "small_arms", "USA"),
    "155mm_howitzer_a.json": _equip(12, "155-mm howitzer", "artillery", "USA"),
    "155mm_howitzer_b.json": _equip(13, "155mm howitzer", "artillery", "USA"),
    "88mm_gun.json": _equip(14, "88-mm gun", "artillery", "Germany"),
    "88mm_flak.json": _equip(15, "88mm Flak", "artillery", "Germany"),
    "m1_garand.json": _equip(16, "M1 Garand", "small_arms", "USA"),
    "garand.json": _equip(17, "Garand", "small_arms", "USA"),
    "thompson.json": _equip(18, "Thompson", "small_arms", "USA"),
    "tommy_gun.json": _equip(19, "Tommy gun", "small_arms", "USA"),
    "churchill_tank.json": _equip(20, "Churchill tank", "tank", "UK"),
}

# Pure-classifier inputs for reclassify_military_units (names that may be mis-filed as places).
RECLASSIFY_NAMES = [
    "Caen",
    "1st Infantry Division",
    "V Corps",
    "Panzer Lehr",
    "Hill 192",
    "16th Infantry Regiment",
    "Omaha Beach",
    "Seventh Army",
    "Combat Command B",
    "Task Force Butler",
]

# --------------------------------------------------------------------------- helpers


def _write_dir(base: Path, files: Dict[str, Any]) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (base / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return base


def _canon(obj: Any) -> Any:
    """Order-insensitive, float-stable canonical form (detectors iterate unsorted glob order)."""
    if isinstance(obj, dict):
        return {k: _canon(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        items = [_canon(x) for x in obj]
        return sorted(
            items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)
        )
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No AWS, no Grok, built-in dedup defaults, full comparison, repo-root cwd."""

    def _no_aws(*_a, **_k):
        raise AssertionError("dedup golden test must not touch AWS")

    def _no_network(*_a, **_k):
        raise AssertionError("dedup golden test must not open network connections")

    # NOTE: these patches only bite while the detectors look the names up at call time
    # (module attribute access). A top-level `from x import load_config` would bypass them.
    monkeypatch.setattr(
        "src.utils.config.load_config", lambda *a, **k: {"aws": {"enabled": False}}
    )
    monkeypatch.setattr(
        "src.dedup.incremental.get_last_dedup_run", lambda *a, **k: None
    )
    monkeypatch.setattr("boto3.resource", _no_aws)
    monkeypatch.setattr("boto3.client", _no_aws)
    # Enforce "no network" rather than assume it (Grok/HTTP clients all bottom out in sockets).
    monkeypatch.setattr("socket.socket.connect", _no_network)
    monkeypatch.setattr("socket.create_connection", _no_network)
    for var in (
        "NOTIFICATION_TOPIC_ARN",
        "GROK_API_KEY",
        "UNIT_NICKNAMES_PATH",
        "CACHE_TABLE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(REPO_ROOT)
    return tmp_path


def _build_corpus(root: Path) -> Dict[str, Path]:
    event_dir = root / "content" / "TestBook"
    event_dir.mkdir(parents=True)
    (event_dir / "chapter1-event.json").write_text(
        json.dumps(
            {
                "Event": {
                    "EventID": _EVENT_ID,
                    "Sub-events": [
                        {"Sub-eventID": _SEID[k], "Sub-event_fulltext": {"p1": text}}
                        for k, text in sorted(_SUB_EVENTS.items())
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    people = _write_dir(root / "people", PEOPLE)
    (people / "index.json").write_text(json.dumps(PEOPLE_INDEX), encoding="utf-8")
    (people / "not_duplicates.json").write_text(
        json.dumps(PEOPLE_EXCLUSIONS), encoding="utf-8"
    )
    groups = _write_dir(root / "people_groups", GROUPS)
    places = _write_dir(root / "places", PLACES)
    (places / "index.json").write_text(json.dumps(PLACES_INDEX), encoding="utf-8")
    (places / "coords.json").write_text(json.dumps(PLACES_COORDS), encoding="utf-8")
    equipment = _write_dir(root / "equipment", EQUIPMENT)
    return {
        "people": people,
        "groups": groups,
        "places": places,
        "equipment": equipment,
    }


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_all(root: Path) -> Dict[str, Any]:
    dirs = _build_corpus(root)
    out = root / "_reports"
    out.mkdir()

    dup_people.generate_duplicate_report(
        dirs["people"], out / "people.json", output_root=root, grok_client=None
    )
    dup_places.generate_duplicate_report(dirs["places"], out / "places.json")
    dup_groups.generate_duplicate_report(dirs["groups"], out / "groups.json")
    dup_equipment.generate_duplicate_report(dirs["equipment"], out / "equipment.json")
    # generate_related_groups_report always enables LLM verification; call the core directly.
    related = related_groups.find_related_groups(
        dirs["groups"], use_llm_verification=False
    )

    # cleanup_index: a stale entry (missing file) and a mismatched name are pruned.
    idx_dir = root / "idx_people"
    _write_dir(idx_dir, {"real_person.json": {"name": "Real Person"}})
    (idx_dir / "index.json").write_text(
        json.dumps({"Real Person": "real_person.json", "Ghost Person": "ghost.json"}),
        encoding="utf-8",
    )
    cleanup_result = cleanup_indexes.cleanup_index(idx_dir, "name", dry_run=False)

    reclassify_run = _run_reclassify(root / "reclass_root")

    return {
        "people": _read(out / "people.json"),
        "places": _read(out / "places.json"),
        "groups": _read(out / "groups.json"),
        "equipment": _read(out / "equipment.json"),
        "related_groups": related,
        "cleanup_index": {
            "result": cleanup_result,
            "index_after": _read(idx_dir / "index.json"),
        },
        "reclassify_is_military_unit": {
            n: reclassify.is_military_unit(n) for n in RECLASSIFY_NAMES
        },
        "reclassify_transform": reclassify.transform_place_to_group(
            {
                "current_name": "1st Infantry Division",
                "PlaceID": _id("PL", 50),
                "event_mentions": [],
            }
        ),
        "reclassify_run": reclassify_run,
    }


def _run_reclassify(reclass_root: Path) -> Dict[str, Any]:
    """End-to-end reclassify(): dry run must not touch disk; real run moves units only."""
    places = _write_dir(
        reclass_root / "places",
        {
            "caen.json": {"PlaceID": _id("PL", 60), "current_name": "Caen"},
            "1st_infantry_division.json": {
                "PlaceID": _id("PL", 61),
                "current_name": "1st Infantry Division",
                "country_of_origin": "USA",
                "event_mentions": [{"EventID": _EVENT_ID}],
                "historical_names": ["Big Red One"],
            },
            "panzer_lehr.json": {"PlaceID": _id("PL", 62), "name": "Panzer Lehr"},
            # SKIP_FILES entry with a unit-like name must never be moved.
            "index.json": {"current_name": "V Corps"},
        },
    )
    (places / "corrupt.json").write_text("{not json", encoding="utf-8")

    def _tree() -> Dict[str, Any]:
        return {
            sub: {
                p.name: (
                    p.read_text(encoding="utf-8")
                    if p.name == "corrupt.json"
                    else _read(p)
                )
                for p in sorted((reclass_root / sub).glob("*.json"))
            }
            for sub in ("places", "people_groups")
        }

    before = _tree()
    dry_count = reclassify.reclassify(reclass_root, dry_run=True)
    assert _tree() == before, "reclassify dry run modified files"
    count = reclassify.reclassify(reclass_root, dry_run=False)
    return {"dry_run_count": dry_count, "count": count, "tree_after": _tree()}


# --------------------------------------------------------------------------- the test


def test_dedup_outputs_match_golden(isolated):
    actual = _canon(_run_all(isolated))

    if os.environ.get("UPDATE_DEDUP_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(
            f"golden regenerated at {GOLDEN.relative_to(REPO_ROOT)}; review the diff"
        )

    assert (
        GOLDEN.exists()
    ), f"missing golden file {GOLDEN}; generate with UPDATE_DEDUP_GOLDEN=1 and review it"
    expected = _read(GOLDEN)
    for section in expected:
        assert actual.get(section) == expected[section], (
            f"dedup output changed in section '{section}'. If intended, regenerate with "
            "UPDATE_DEDUP_GOLDEN=1 and review the JSON diff in the same commit."
        )
    assert set(actual) == set(expected), "report sections added/removed"


def test_golden_corpus_actually_finds_duplicates():
    """Guard against a vacuous golden: the corpus must exercise every detector."""
    if os.environ.get("UPDATE_DEDUP_GOLDEN") == "1" or not GOLDEN.exists():
        pytest.skip("golden being regenerated or not yet generated")
    expected = _read(GOLDEN)
    assert expected["people"]["duplicate_groups"] > 0
    assert not any(
        set(EXCLUDED_PAIR) <= {p["filename"] for p in g["people"]}
        for g in expected["people"]["duplicates"]
    ), "excluded pair grouped: not_duplicates.json was not honoured"
    assert expected["related_groups"], "related-groups golden is empty"
    assert expected["places"]["duplicates"], "places golden is empty"
    assert expected["groups"]["duplicates"], "groups golden is empty"
    assert expected["equipment"]["duplicates"], "equipment golden is empty"
    assert any(expected["reclassify_is_military_unit"].values())
    assert not all(expected["reclassify_is_military_unit"].values())
    assert expected["reclassify_run"]["count"] > 0
    assert expected["reclassify_run"]["tree_after"][
        "places"
    ], "reclassify moved everything"
