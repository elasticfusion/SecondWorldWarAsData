"""GUARD (anti-recurrence, NARROW/opt-in): a fixture line tagged with the marker
`# schema-current-fixture` MUST carry the CURRENT target _schema_version for its entity. Tag the
fixtures that are meant to represent a VALID/current record (e.g. the P3-A5 enriched-record tests)
so a schema bump that forgets to update them fails FAST + locally with the exact file/entity —
instead of only in CI. Fixtures WITHOUT the marker (deliberately-old-version upgrade/stamp tests)
are intentionally untouched.
"""

import re
from pathlib import Path

from src.schemas import ENTITY_SCHEMA_VERSIONS

_TESTS = Path(__file__).parent
_MARKER = "# schema-current-fixture"


def test_tagged_fixtures_match_current_target():
    stale = []
    for pf in _TESTS.rglob("*.py"):
        text = pf.read_text(encoding="utf-8", errors="ignore")
        if _MARKER not in text:
            continue
        for ln in text.splitlines():
            if _MARKER not in ln:
                continue
            vm = re.search(r'"_schema_version"\s*:\s*"(\d+\.\d+)"', ln)
            em = re.search(r"entity=(\w+)", ln)
            if not (vm and em):
                continue
            ent, ver = em.group(1), vm.group(1)
            target = ENTITY_SCHEMA_VERSIONS.get(ent)
            if target and ver != target:
                stale.append(
                    f"{pf.name}: {ent} tagged fixture _schema_version={ver} != target {target}"
                )
    assert not stale, "Stale current-fixtures (bump to target):\n" + "\n".join(stale)
