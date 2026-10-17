"""HyperWar bibliographical-note resolution source (pluggable, multi-volume).

The US Army "green book" volumes transcribed at ibiblio.org/hyperwar each carry an authoritative
**Bibliographical Note** that explains where every class of cited source is held and how it is
catalogued. For a corpus extracted from one of these volumes (citation ``source_url`` points at
the volume), that note is a high-value resolution source: it maps a cryptic citation to its
holding institution + provenance, and — for the volume's "Published Works" — to a concrete book
that IS findable on Archive.org / Gutenberg.

Design (generalized across volumes):
- ANY ibiblio hyperwar volume is recognized; the volume key and its biblio-note URL are derived
  from the citation ``source_url`` (e.g. ``.../USA-E-Siegfried/USA-E-Siegfried-3.html`` ->
  volume ``USA-E-Siegfried`` -> note ``USA-E-Siegfried-Biblio.html``).
- Per-volume PUBLISHED-WORKS tables (``_VOLUME_PUBLISHED_WORKS``) carry the citable books each
  note lists (different per volume). Add a new volume by adding a table entry — no resolver change.
- A SHARED GENERIC archival-category layer (``_CATEGORIES``) classifies the universal source
  types (German MS/KTB, combat interviews, unit journals/AARs, letters/cables, personal diaries,
  staff/chiefs-of-staff files) that recur across all ETO volumes.

Sources:
  https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/USA-E-Siegfried-Biblio.html
  https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-Biblio.html
  https://www.ibiblio.org/hyperwar/USA/USA-E-Logistics2/USA-E-Logistics2-Biblio.html
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# Recognize any HyperWar volume + capture the base URL and the volume key from the source_url.
# e.g. https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/USA-E-Siegfried-3.html#fn1
_HYPERWAR_URL = re.compile(
    r"(?P<base>https?://[^\s]*?/hyperwar/[^\s]*?/(?P<vol>[A-Za-z0-9-]+))/"
    r"(?P=vol)-[^/\s]*?\.html",
    re.I,
)


def _parse_volume(source_url: str) -> Optional[Tuple[str, str]]:
    """From a citation source_url return (volume_key, biblio_note_url), or None if not a
    recognizable HyperWar volume page. The note URL is the volume's ``<vol>-Biblio.html``.
    """
    if not source_url:
        return None
    m = _HYPERWAR_URL.search(source_url)
    if not m:
        return None
    vol = m.group("vol")
    note_url = f"{m.group('base')}/{vol}-Biblio.html"
    return vol, note_url


def volume_matches(source_url: str) -> bool:
    """True if a citation's source_url belongs to a HyperWar green-book volume (any)."""
    return _parse_volume(source_url) is not None


def _pw(title, author, publisher, year, surname, keywords):
    return {
        "title": title,
        "author": author,
        "publisher": publisher,
        "year": year,
        "surname": surname,
        "keywords": keywords,
    }


# Per-volume "Published Works" — citable books with a stable identity that downstream
# Archive.org / Gutenberg search CAN find. Keyed by volume. Matched by author surname + a title
# keyword (or, for corporate/untitled-author works, all title keywords).
_VOLUME_PUBLISHED_WORKS: Dict[str, List[Dict[str, Any]]] = {
    "USA-E-Siegfried": [
        _pw(
            "A Soldier's Story",
            "Bradley, Omar N.",
            "Henry Holt and Co.",
            1951,
            "bradley",
            ("soldier",),
        ),
        _pw(
            "The Brereton Diaries",
            "Brereton, Lewis H.",
            "William Morrow and Co.",
            1946,
            "brereton",
            ("brereton", "diaries"),
        ),
        _pw(
            "Operation Victory",
            "de Guingand, Francis",
            "Charles Scribner's Sons",
            1947,
            "guingand",
            ("operation victory",),
        ),
        _pw(
            "Crusade in Europe",
            "Eisenhower, Dwight D.",
            "Doubleday and Co.",
            1948,
            "eisenhower",
            ("crusade",),
        ),
        _pw(
            "Normandy to the Baltic",
            "Montgomery, Bernard L.",
            "Houghton Mifflin",
            1948,
            "montgomery",
            ("normandy to the baltic",),
        ),
        _pw(
            "War As I Knew It",
            "Patton, George S.",
            "Houghton Mifflin Co.",
            1947,
            "patton",
            ("war as i knew it",),
        ),
        _pw(
            "The Canadian Army, 1939-1945",
            "Stacey, Charles P.",
            "King's Printer",
            1948,
            "stacey",
            ("canadian army",),
        ),
        _pw(
            "The Victory Campaign",
            "Stacey, Charles P.",
            "Queen's Printer",
            1960,
            "stacey",
            ("victory campaign",),
        ),
        _pw(
            "Conquer: The Story of Ninth Army",
            "Ninth Army",
            "Infantry Journal Press",
            1947,
            "",
            ("conquer", "ninth army"),
        ),
    ],
    "USA-E-XChannel": [
        _pw(
            "Crusade in Europe",
            "Eisenhower, Dwight D.",
            "Doubleday and Co.",
            1948,
            "eisenhower",
            ("crusade",),
        ),
        _pw(
            "My Three Years With Eisenhower",
            "Butcher, Harry C.",
            "Simon and Schuster",
            1946,
            "butcher",
            ("three years",),
        ),
        _pw(
            "Overture to Overlord",
            "Morgan, Frederick E.",
            "Doubleday",
            1950,
            "morgan",
            ("overture to overlord",),
        ),
        _pw(
            "Roosevelt and Hopkins: An Intimate History",
            "Sherwood, Robert E.",
            "Harper",
            1948,
            "sherwood",
            ("roosevelt and hopkins",),
        ),
        _pw(
            "On Active Service in Peace and War",
            "Stimson, Henry L.",
            "Harper",
            1948,
            "stimson",
            ("active service",),
        ),
        _pw(
            "The Grand Alliance",
            "Churchill, Winston S.",
            "Houghton Mifflin",
            1950,
            "churchill",
            ("grand alliance",),
        ),
        _pw(
            "The Battle of the Atlantic",
            "Morison, Samuel E.",
            "Little, Brown",
            1948,
            "morison",
            ("battle of the atlantic",),
        ),
        _pw(
            "The Army Air Forces in World War II",
            "Craven, Wesley F.",
            "University of Chicago Press",
            1948,
            "craven",
            ("army air forces",),
        ),
        _pw(
            "Omaha Beachhead",
            "Taylor, Charles H.",
            "Historical Division, War Department",
            1945,
            "taylor",
            ("omaha beachhead",),
        ),
        _pw(
            "Utah Beach to Cherbourg",
            "Ruppenthal, R. G.",
            "Historical Division, War Department",
            1947,
            "ruppenthal",
            ("utah beach",),
        ),
        _pw(
            "Rendezvous With Destiny",
            "Rapport, Leonard",
            "Infantry Journal Press",
            1948,
            "rapport",
            ("rendezvous with destiny",),
        ),
        _pw(
            "The Canadian Army, 1939-1945",
            "Stacey, Charles P.",
            "King's Printer",
            1948,
            "stacey",
            ("canadian army",),
        ),
    ],
    "USA-E-Logistics2": [
        _pw(
            "My Three Years With Eisenhower",
            "Butcher, Harry C.",
            "Simon and Schuster",
            1946,
            "butcher",
            ("three years",),
        ),
        _pw(
            "A Soldier's Story",
            "Bradley, Omar N.",
            "Henry Holt and Co.",
            1951,
            "bradley",
            ("soldier",),
        ),
        _pw(
            "Roosevelt and Hopkins: An Intimate History",
            "Sherwood, Robert E.",
            "Harper",
            1948,
            "sherwood",
            ("roosevelt and hopkins",),
        ),
        _pw(
            "Overture to Overlord",
            "Morgan, Frederick E.",
            "Doubleday",
            1950,
            "morgan",
            ("overture to overlord",),
        ),
        _pw(
            "The Army Air Forces in World War II",
            "Craven, Wesley F.",
            "University of Chicago Press",
            1948,
            "craven",
            ("army air forces",),
        ),
    ],
}

# SHARED GENERIC archival categories (apply to ALL volumes). Each is (regex, provenance). These
# are not online copies; they are the correct repository + description per the green-book notes,
# which is the authoritative answer for "where does this live".
_CATEGORIES: List[Tuple["re.Pattern[str]", Dict[str, str]]] = [
    (
        re.compile(r"\bsylvan\b.*diary|sylvan diary", re.I),
        {
            "holding": "Office of the Chief of Military History (OCMH)",
            "description": "Maj. William C. Sylvan's day-by-day diary of Gen. Hodges' activities, provided to OCMH.",
        },
    ),
    (
        re.compile(r"\bbutcher\b|Diary of CinC|My Three Years", re.I),
        {
            "holding": "Office of the Commander in Chief diary (kept by Capt. Harry C. Butcher)",
            "description": "MS diary of the Office of the CinC; edited excerpts published as 'My Three Years with Eisenhower'.",
        },
    ),
    (
        re.compile(r"\bMS\s*#?\s*[A-Z]\s*-?\s*\d|Foreign Military Studies", re.I),
        {
            "holding": "Office of the Chief of Military History (OCMH)",
            "description": "German officer post-war manuscript (Foreign Military Studies); catalogued in Guide to Foreign Military Studies 1945-54.",
        },
    ),
    (
        re.compile(r"\bKTB\b|Kriegstagebuch|Anlagen|OB WEST|Army Group B", re.I),
        {
            "holding": "World War II Records Division, National Archives (NARA)",
            "description": "Seized German war diary (Kriegstagebuch) / annexes.",
        },
    ),
    (
        re.compile(r"\binterv(iew)?\b", re.I),
        {
            "holding": "World War II Records Division / Historical Division, National Archives (NARA)",
            "description": "Combat/postwar interview conducted by ETO Historical Section / Historical Division officers.",
        },
    ),
    (
        # Joint/Combined/British chiefs-of-staff + SHAEF/COSSAC/OPD staff collections (Cross-Channel).
        re.compile(
            r"\bSHAEF\b|\bSGS\b|\bCOSSAC\b|\bCCS\b|\bJCS\b|\bJPS\b|\bJWPC\b|\bJIC\b|"
            r"\bCOS\b|\bOPD\b|Combined Chiefs|Joint Chiefs|Pre-Inv|C/S file|Eisenhower Personal",
            re.I,
        ),
        {
            "holding": "Historical Records Section, AGO / SHAEF collection, National Archives",
            "description": "Joint/Combined/SHAEF staff records (cables, memoranda, minutes) per the volume's bibliographical note.",
        },
    ),
    (
        re.compile(r"\bltr\b|letter|\bcable\b|\bmsg\b|message", re.I),
        {
            "holding": "Office of the Chief of Military History (OCMH) / unit files, National Archives",
            "description": "Wartime/postwar correspondence, cable, or message per the volume's bibliographical note.",
        },
    ),
    (
        re.compile(
            r"General Board( Report)?|Administrative and Logistical History|"
            r"Overseas Supply Policies|Historical Section, ETO|preliminary (study|histor)",
            re.I,
        ),
        {
            "holding": "Office of the Chief of Military History (OCMH) / ETO Administrative File, AGO",
            "description": "ETO Historical Section monograph / General Board report / preliminary administrative-logistical study.",
        },
    ),
    (
        re.compile(
            r"Military Review|Quartermaster Review|Royal United Service|Royal Engineers Journal|"
            r"Army Ordnance|Army Transportation Journal|\bjournal of the\b",
            re.I,
        ),
        {
            "holding": "Published service journal (library holdings)",
            "description": "Service-journal article (e.g. Military Review, The Quartermaster Review); a published periodical.",
        },
    ),
    (
        # Unit official records: AARs, journals, periodic/staff reports, army-group/air files.
        re.compile(
            r"\bAAR\b|after action|G-[1-4]\b|Per Rpt|periodic|journal|Opns? Memo|"
            r"Army Group|A Gp\b|Corps\b|Div\b|Report of Operations|Operations in the ETO|"
            r"\bAAF\b|USSTAF|\bETO file\b|\bFUSA\b",
            re.I,
        ),
        {
            "holding": "World War II Records Division / Historical Records Section (AGO), National Archives",
            "description": "US Army/Air unit official records (after action reports, journals, periodic/staff reports) per the volume's bibliographical note.",
        },
    ),
]


def _match_published_work(text: str, volume: str) -> Optional[Dict[str, Any]]:
    low = text.lower()
    for w in _VOLUME_PUBLISHED_WORKS.get(volume, []):
        sur = w["surname"]
        if sur and sur in low and any(k in low for k in w["keywords"]):
            return w
        if not sur and all(k in low for k in w["keywords"]):
            return w
    return None


def resolve_from_biblio_note(
    verbatim: str, source_url: str
) -> Optional[Dict[str, Any]]:
    """Resolve a citation against its green-book volume's bibliographical note.

    Returns a provenance dict (merged onto the entry's ``source_metadata``) or None. For a
    PUBLISHED WORK it returns a canonical title/author (so downstream Archive.org/Gutenberg search
    can find a copy); for an ARCHIVAL CATEGORY it returns the authoritative holding institution +
    description. Works for ANY recognized HyperWar volume. Never raises.
    """
    if not verbatim:
        return None
    parsed = _parse_volume(source_url)
    if not parsed:
        return None
    volume, note_url = parsed
    try:
        work = _match_published_work(verbatim, volume)
        if work:
            return {
                "biblio_note_url": note_url,
                "biblio_volume": volume,
                "biblio_category": "published_work",
                "canonical_title": work["title"],
                "canonical_author": work["author"],
                "publisher": work["publisher"],
                "year": work["year"],
            }
        for pattern, prov in _CATEGORIES:
            if pattern.search(verbatim):
                out = {
                    "biblio_note_url": note_url,
                    "biblio_volume": volume,
                    "biblio_category": "archival",
                }
                out.update(prov)
                return out
    except Exception:  # noqa: BLE001 - resolution helper must never raise
        return None
    return None
