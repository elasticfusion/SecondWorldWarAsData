#!/usr/bin/env python3
"""Extract Eisenhower Presidential Library oral histories with corrected digital detection."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.eisenhowerlibrary.gov/research/oral-histories"
OUTPUT_CSV = PROJECT_ROOT / "contentrepository/indexes/eisenhower_oral_histories_wwii_participants.csv"


def fetch_html() -> str:
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
        },
    )
    with urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_rows(html: str) -> list[dict[str, str]]:
    rows = re.findall(r'<div class="views-row">(.*?)</div></div></div>', html, re.DOTALL)
    entries: list[dict[str, str]] = []

    for row in rows:
        name_match = re.search(r"<strong>([^<]+)</strong>", row)
        if not name_match:
            continue

        name = re.sub(r"\s+", " ", name_match.group(1).strip())
        oh_match = re.search(r"\(OH-(\d+)\)", row)
        oh_id = f"OH-{oh_match.group(1)}" if oh_match else ""

        content = re.sub(r"<[^>]+>", " ", row)
        content = re.sub(r"\s+", " ", content).strip()

        source_match = re.search(r"[\[(](EL|COHP|OTHER)[\])]", content)
        source = source_match.group(1) if source_match else ""

        year_match = re.search(
            r"[\[(](?:EL|COHP|OTHER)[\])]\s*(\d{4}(?:-\d{2,4})?)\.\s*(\d+)\s*pages",
            content,
        )
        if not year_match:
            year_match = re.search(r"(\d{4}(?:-\d{2,4})?)\.\s*(\d+)\s*pages", content)
        interview_year = year_match.group(1) if year_match else ""
        pages = year_match.group(2) if year_match else ""

        bio = ""
        if oh_id:
            bio_match = re.search(
                r"\(OH-\d+\)\s*(.*?)\s*[\[(](?:EL|COHP|OTHER)[\])]",
                content,
            )
            if bio_match:
                bio = bio_match.group(1).strip()
        else:
            bio_match = re.search(
                r"[\[(](?:EL|COHP|OTHER)[\])]\s*\d{4}(?:-\d{2,4})?\.\s*\d+\s*pages\.\s*(.*?)(?:<br>|Finding Aid|Transcript)",
                row,
                re.DOTALL,
            )
            if bio_match:
                bio = re.sub(r"<[^>]+>", " ", bio_match.group(1))
                bio = re.sub(r"\s+", " ", bio).strip()
                bio = bio.replace("&ldquo;", '"').replace("&rdquo;", '"').replace("&nbsp;", " ")

        finding_aid_match = re.search(
            r'href="([^"]*oral-history-finding-aids[^"]*)"', row
        )
        finding_aid = finding_aid_match.group(1) if finding_aid_match else ""
        if finding_aid:
            finding_aid = urljoin(SOURCE_URL, finding_aid)

        transcript_pdf, access_notes, digital = detect_digital_transcript(row)

        entries.append(
            {
                "name": name,
                "oh_id": oh_id,
                "bio": bio,
                "source": source,
                "interview_year": interview_year,
                "pages": pages,
                "finding_aid": finding_aid,
                "transcript_pdf": transcript_pdf,
                "digital_artifact": digital,
                "access_notes": access_notes,
            }
        )

    return entries


def detect_digital_transcript(row: str) -> tuple[str, str, str]:
    """Return (transcript_pdf_url, access_notes, digital_artifact yes/no)."""
    transcript_links = re.findall(
        r'<a[^>]+href="([^"]+)"[^>]*>\s*Transcript\s*</a>',
        row,
        flags=re.I,
    )
    pdf_links = [
        link
        for link in dict.fromkeys(transcript_links)
        if re.search(r"\.pdf(?:\?|#|$)", link, re.I) or "/file/" in link
    ]

    if pdf_links:
        transcript_pdf = urljoin(SOURCE_URL, pdf_links[0])
        return (
            transcript_pdf,
            "PDF transcript linked on Eisenhower Library website",
            "yes",
        )

    plain = re.sub(r"<[^>]+>", " ", row)
    plain = re.sub(r"\s+", " ", plain)

    if "Transcript available in research room only" in plain:
        return "", "Transcript available in research room only", "no"
    if "EL research room (in person)" in plain and "COHP website" in plain:
        return "", "EL research room (in person) or Columbia Oral History Project", "no"
    if re.search(r"(?:&bull;|•)\s*Transcript\s*</p>", row):
        return "", "Transcript not digitized on website", "no"

    return "", "No online transcript PDF", "no"


def classify_wwii(entry: dict[str, str]) -> tuple[bool, list[str]]:
    bio = entry["bio"]
    plain = bio.lower()
    name = entry["name"]
    full = f"{name} {bio}".lower()
    reasons: list[str] = []

    if re.search(r"^eisenhower,\s*dwight d\.?$", name.strip(), re.I):
        return True, [
            "Supreme Allied Commander, European Theater; General of the Army"
        ]

    checks = [
        (r"world war ii|second world war", "Explicit World War II service/role in catalog description"),
        (r"during world war ii", "Explicitly served/acted during World War II"),
        (r"\bshaef\b|\bcossac\b|\bafhq\b", "Allied high-command headquarters (SHAEF/COSSAC/AFHQ)"),
        (r"los alamos|manhattan", "Manhattan Project / Los Alamos"),
        (r"paratrooper|101st airborne|airborne division", "Combat paratrooper"),
        (r"canadian army officer", "Canadian Army officer during WWII"),
        (r"red cross worker.*world war ii", "Red Cross worker during WWII"),
        (r"invasion in italy|5th army", "Fifth Army, Italian campaign"),
        (r"9th army|6th army group|north african campaign", "U.S./Allied Army command in European/North African campaigns"),
        (r"airplane repair management specialist during world war ii", "Aircraft repair management during WWII"),
        (r"commanding general, canadian pacific command, 1942-45", "Canadian Pacific Command, 1942-45"),
        (r"driver for general eisenhower during the north african campaign", "Driver for Eisenhower, North African Campaign"),
        (r"world war ii corps commander", "World War II corps commander"),
        (r"u\.s\. forces in the british isles", "U.S. Forces in the British Isles (ETO buildup)"),
        (r"military police.*european|european theatre.*world war", "U.S. Army service in European Theater"),
        (r"jeep driver and bodyguard for general mark clark", "Service with General Mark Clark, 1944-45"),
        (r"war department official", "War Department official (wartime service; dates not specified in catalog)"),
        (r"personal representative in french north africa|chief civil affairs officer", "North Africa civil affairs / diplomatic mission, 1940-43"),
        (r"shaef staff, 1944", "SHAEF staff, 1944"),
        (r"ambassador to the dominican republic, 1944-45", "Wartime diplomatic post, 1944-45"),
        (
            r"military associate with\s+eisenhower.*during world war ii|military associate of general eisenhower during world war ii|world war ii military associate",
            "Documented Eisenhower military associate during World War II",
        ),
        (
            r"senior staff officer under eisenhower during world war ii",
            "Senior staff officer under Eisenhower during World War II",
        ),
    ]
    for pattern, reason in checks:
        if re.search(pattern, plain):
            reasons.append(reason)

    if re.search(r"\boss\b", plain) and re.search(r"194[0-6]", bio):
        reasons.append("Office of Strategic Services during war years")
    if re.search(r"assistant secretary of war|under secretary of war|secretary of war", plain) and re.search(
        r"194[0-5]", bio
    ):
        reasons.append("Senior War Department leadership, 1941-45")
    if re.search(r"war plans division|war department general staff|operations war department", plain) and re.search(
        r"194[0-5]", bio
    ):
        reasons.append("War Department planning/operations staff, 1940-45")
    if re.search(r"commanding general|commander, u\.s\. forces|deputy supreme commander|chief of staff under", plain) and re.search(
        r"194[0-5]", bio
    ):
        reasons.append("Field/command staff assignment, 1940-45")
    if re.search(r"european theater|european theatre|north african theater|mediterranean theater", plain) and re.search(
        r"194[0-5]", bio
    ):
        reasons.append("Named wartime theater assignment")
    if re.search(r"\bwac\b", plain) and re.search(r"194[0-5]", bio):
        reasons.append("Women's Army Corps / SHAEF staff")
    if re.search(r"naval reserve.*194[0-5]|commander, united states naval reserve", plain):
        reasons.append("U.S. Naval Reserve command during WWII")
    if re.search(r"intelligence officer", plain) and re.search(r"194[0-5]", bio):
        reasons.append("Military intelligence officer, 1940-45")
    if re.search(r"pilot for (?:general )?dwight d\. eisenhower|pilot for dwight d\. eisenhower", plain) and re.search(
        r"1945", bio
    ):
        reasons.append("Eisenhower's pilot in 1945")
    if re.search(r"commander, 9th armored division, 1942-45", plain):
        reasons.append("Commander, 9th Armored Division, 1942-45")
    if re.search(r"chief of staff v corps.*1943-45", plain):
        reasons.append("Chief of Staff, V Corps, 1943-45")
    if re.search(r"u\.s\. joint chiefs of staff, 1944-46", plain):
        reasons.append("Joint Chiefs of Staff staff, 1944-46")
    if re.search(r"war department official", plain):
        reasons.append("War Department official (wartime service; dates not specified in catalog)")
    if re.search(r"military associate of general eisenhower, 1945-59", plain) and "clay" in full:
        reasons.append("Military associate of Eisenhower from 1945 (occupation/post-hostilities command)")
    if re.search(r"ft\. lewis.*1940.*war plans division.*1941-42", plain):
        reasons.append("Eisenhower military associate, Ft. Lewis 1940 and War Plans Division 1941-42")

    if re.search(r"attorney general of the united states, 1945-1949", plain):
        return False, []

    reasons = list(dict.fromkeys(reasons))
    return (True, reasons) if reasons else (False, [])


SUPPLEMENTS = {
    ("Nevins, Arthur", "OH-380"): "Military associate with Eisenhower during World War II (see OH-119)",
    ("Quesada, Elwood Richard", "OH-476"): "Military associate of Eisenhower during World War II (see OH-308)",
    ("Clay, Gen. Lucius D.", "OH-285"): "Military associate of Eisenhower from 1945 (see OH-56); occupation-era command",
    ("Clay, Gen. Lucius D.", "OH-526"): "Military associate of Eisenhower from 1945 (see OH-56); occupation-era command",
}


def main() -> None:
    html = fetch_html()
    entries = parse_rows(html)

    wwii_entries: list[dict[str, str]] = []
    for entry in entries:
        if not entry["oh_id"] and entry["name"] != "Eisenhower, Dwight D.":
            continue
        participated, reasons = classify_wwii(entry)
        if participated:
            row = dict(entry)
            row["wwii_participation_reason"] = "; ".join(reasons)
            row["source_page"] = SOURCE_URL
            wwii_entries.append(row)

    existing = {(row["name"], row["oh_id"]) for row in wwii_entries}
    for entry in entries:
        key = (entry["name"], entry["oh_id"])
        if key in SUPPLEMENTS and key not in existing:
            row = dict(entry)
            row["wwii_participation_reason"] = SUPPLEMENTS[key]
            row["source_page"] = SOURCE_URL
            wwii_entries.append(row)

    wwii_entries.sort(key=lambda row: (row["digital_artifact"] == "no", row["name"], row["oh_id"]))

    fields = [
        "name",
        "oh_id",
        "bio",
        "wwii_participation_reason",
        "digital_artifact",
        "access_notes",
        "transcript_pdf",
        "finding_aid",
        "source",
        "interview_year",
        "pages",
        "source_page",
    ]
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(wwii_entries)

    digital = sum(1 for row in wwii_entries if row["digital_artifact"] == "yes")
    print(f"Wrote {len(wwii_entries)} WWII participants to {OUTPUT_CSV}")
    print(f"Digital transcript links: {digital}")
    print(f"No digital transcript: {len(wwii_entries) - digital}")


if __name__ == "__main__":
    main()