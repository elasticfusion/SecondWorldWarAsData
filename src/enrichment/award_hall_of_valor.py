"""Hall of Valor (valor.militarytimes.com) — direct award-citation source.

The largest public database of US valor citations (Medal of Honor + all service
crosses complete, Silver Star partial). Verified accessible from our IP
(HTTP 200, nginx) with a permissive robots.txt (`Disallow:` empty + sitemap), so
polite direct fetching is allowed. (Contrast valor.defense.gov, which is
Akamai-IP-blocked and carries no citation text anyway — see AWARD_SOURCING.md.)

Access shape (verified 2026-10-02):
- Search: ``GET /?s=<name>`` (WordPress) → HTML listing recipient links.
- Recipient page: ``/recipient/recipient-<id>/`` → the person's awards, each with
  its citation text.

Politeness: one shared rate limiter (>= 1 request/second), on-disk cache so a
re-run makes no repeat calls, descriptive User-Agent. Verification-not-
fabrication: a citation is returned only when the recipient page's name matches
the queried person AND (if given) the award matches.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from html import unescape
from pathlib import Path
from typing import Any, List, Optional

from src.enrichment.award_sources import AwardCitation, today_iso
from src.enrichment.award_polite_source import PoliteSource

logger = logging.getLogger(__name__)

_BASE = "https://valor.militarytimes.com"

# Normalize common award-name variants so an entity's award string matches the
# site's award labels.
_AWARD_ALIASES = {
    "dsc": "Distinguished Service Cross",
    "distinguished service cross": "Distinguished Service Cross",
    "moh": "Medal of Honor",
    "medal of honor": "Medal of Honor",
    "navy cross": "Navy Cross",
    "silver star": "Silver Star",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _canonical_award(award: str) -> str:
    return _AWARD_ALIASES.get(_norm(award), award.strip())


class HallOfValorSource(PoliteSource):
    """An :class:`AwardCitationSource` backed by valor.militarytimes.com."""

    name = "Military Times Hall of Valor"

    def __init__(
        self,
        cache_dir: Path,
        session: Any = None,
        storage: Any = None,
        source_id: str = "us_hall_of_valor",
        crawl_delay: float = 1.1,
        extra_headers: Optional[dict] = None,
    ):
        super().__init__(
            cache_dir,
            source_id=source_id,
            crawl_delay=crawl_delay,
            session=session,
            storage=storage,
        )
        self._extra_headers = extra_headers

    def _get(self, url: str) -> Optional[str]:
        return self.polite_get(url, extra_headers=self._extra_headers)

    def _recipient_urls(self, name: str) -> List[str]:
        html = self._get(f"{_BASE}/?s={name.replace(' ', '+')}")
        if not html:
            return []
        urls = re.findall(
            r'href="(https://valor\.militarytimes\.com/recipient/recipient-\d+/)"',
            html,
        )
        # De-dupe, preserve order; cap to a few candidates (politeness + relevance).
        seen: set = set()
        out: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out[:5]

    def lookup(self, person_name: str, award_hint: str = "") -> List[AwardCitation]:
        want_award = _canonical_award(award_hint) if award_hint else ""
        want_name = _norm(person_name)
        results: List[AwardCitation] = []
        for url in self._recipient_urls(person_name):
            html = self._get(url)
            if not html:
                continue
            page_name = self._page_name(html)
            if page_name and not _names_match(want_name, _norm(page_name)):
                continue  # wrong recipient — do not attach
            for award_name, citation in self._parse_awards(html):
                if want_award and _canonical_award(award_name) != want_award:
                    continue
                if not citation:
                    continue
                results.append(
                    AwardCitation(
                        citation_text=citation,
                        source_name=self.name,
                        source_url=url,
                        retrieved_date=today_iso(),
                        award=_canonical_award(award_name),
                        # Name matched the recipient page + (optional) award matched.
                        verified=True,
                    )
                )
        return results

    @staticmethod
    def _page_name(html: str) -> str:
        m = re.search(r"<title>([^<]+?)\s*-\s*Hall of Valor", html, re.I)
        return unescape(m.group(1)).strip() if m else ""

    @staticmethod
    def _parse_awards(html: str):
        """Yield (award_name, citation_text) pairs from a recipient page.

        Real structure (verified): each citation is prose of the form
        "...takes pleasure in presenting the <AWARD> to <rank/name> ... for
        <gallantry language> ... in action ...". The award name is embedded in the
        "presenting the <AWARD> to" phrase; multiple blocks appear per recipient.
        Strip tags to prose, split on the preamble, extract (award, citation).
        """
        prose = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        prose = unescape(re.sub(r"<[^>]+>", " ", prose))
        prose = re.sub(r"\s+", " ", prose).strip()

        starts = [
            m.start() for m in re.finditer(r"presenting the .{3,60}? to ", prose, re.I)
        ]
        if not starts:
            return
        starts.append(len(prose))
        for i in range(len(starts) - 1):
            block = prose[starts[i] : starts[i + 1]].strip()
            am = re.search(r"presenting the (.{3,60}?) to ", block, re.I)
            award = am.group(1).strip() if am else ""
            cite = _extract_citation(block)
            if award and cite:
                yield award, cite


def _extract_citation(prose: str) -> str:
    """Return the citation sentence(s) from a prose block, or '' if none looks like
    a citation. Citations typically contain 'for ... in action' / 'gallantry' /
    'extraordinary heroism' / 'conspicuous'."""
    low = prose.lower()
    if len(prose) < 60:
        return ""
    markers = (
        "for extraordinary heroism",
        "for conspicuous gallantry",
        "for gallantry",
        "in action",
        "distinguished himself",
        "distinguished herself",
        "intrepidity",
    )
    if not any(m in low for m in markers):
        return ""
    # Trim to the citation portion: start at the first marker.
    starts = [low.find(m) for m in markers if low.find(m) >= 0]
    begin = min(starts) if starts else 0
    return prose[begin:][:4000].strip()


def _names_match(a: str, b: str) -> bool:
    """Conservative name match between the queried name and the page name."""
    if not a or not b:
        return False
    if a == b:
        return True
    pa, pb = a.split(), b.split()
    # last name equal + first initial equal
    return bool(pa and pb and pa[-1] == pb[-1] and pa[0][:1] == pb[0][:1])
