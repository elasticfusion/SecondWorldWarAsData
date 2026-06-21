#!/usr/bin/env python3
"""Debug NARA search — test with known high-probability citations.

Usage:
    source .venv/bin/activate
    python scripts/debug_nara_search.py

Requires: GROK_API_KEY env var (or in .env), nara_api_key in config.yaml
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.utils.config import load_config
from src.grok_client import GrokClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LOG_FILE = Path("logs/debug_nara.log")

# Test citations — diverse NARA-likely documents
TEST_CITATIONS = [
    {
        "type": "SHAEF Cable",
        "verbatim": "Cbl, Smith to Marshall, 17 May 44. SHAEF SGS file 373.24",
        "expected_rg": "RG 331",
    },
    {
        "type": "Unit Journal",
        "verbatim": "101st Inf Jnl, 14 Jul 44",
        "expected_rg": "RG 407",
    },
    {
        "type": "After Action Report",
        "verbatim": "104th Div, Annex 2, Intel Annex to AAR, 23-31 Oct 44, dtd 5 Nov 44",
        "expected_rg": "RG 407",
    },
    {
        "type": "Combat Interview",
        "verbatim": "Hist Div Combat Interviews, 2d Armored Division",
        "expected_rg": "RG 407",
    },
    {
        "type": "Field Order",
        "verbatim": "102d Div FO 4, 29 Nov, XIII Corps G-3 Jnl file, 29-30 Nov 44",
        "expected_rg": "RG 407",
    },
    {
        "type": "G-3 Journal",
        "verbatim": "30th Div G-3 Jnl File, 2-4 Jul 44",
        "expected_rg": "RG 407",
    },
    {
        "type": "12th Army Group",
        "verbatim": "12th AGp Ltr of Instrs 5, 17 Aug 44",
        "expected_rg": "RG 331",
    },
]


def main():
    config = load_config()
    nara_key = config.get("api", {}).get("nara_api_key", "")

    # Tee output to log file
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    import io

    class Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
        def flush(self):
            for s in self.streams:
                s.flush()

    log_fh = open(LOG_FILE, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_fh)

    cache_dir = Path("cache/api")
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir)

    print("=" * 70)
    print("NARA Search Debug")
    print("=" * 70)
    print(f"NARA API key: {'set' if nara_key else 'MISSING'}")
    print(f"Grok API key: {'set' if os.environ.get('GROK_API_KEY') else 'MISSING'}")
    print()

    # Step 1: Test Grok Record Group identification
    print("--- Step 1: Grok Record Group Identification ---")
    from src.utils.prompt_loader import render_prompt

    for citation in TEST_CITATIONS:
        prompt = render_prompt("nara_identify", verbatim=citation["verbatim"])
        try:
            response = grok_client.chat_completion(
                prompt=prompt,
                system_prompt="You are a NARA archivist specializing in WWII military records.",
                temperature=0.0,
                use_cache=True,
                cache_type="bibliography_nara",
            )
            ref = response.strip().strip('"')
            found = ref and ref != "UNKNOWN" and "RG" in ref
            status = "✅" if found else "❌"
            match = "MATCH" if citation["expected_rg"] in ref else "MISMATCH" if found else ""
            print(f"  {status} {citation['type']:20s} → {ref[:50]:50s} (expected {citation['expected_rg']}) {match}")
            print(f"     Raw response: {response.strip()[:120]}")
        except Exception as e:
            print(f"  ❌ {citation['type']:20s} → ERROR: {e}")

    print()

    # Step 2: Test NARA Catalog API directly
    if not nara_key:
        print("--- Step 2: NARA Catalog API --- SKIPPED (no API key)")
        return

    print("--- Step 2: NARA Catalog API Direct Search ---")
    import requests

    test_queries = [
        "104th Infantry Division After Action Report October 1944",
        "30th Division G-3 Journal July 1944",
        "SHAEF SGS file 373.24",
        "Combat Interviews 2d Armored Division",
    ]

    for query in test_queries:
        time.sleep(6)  # Rate limit: ~10 requests/min
        try:
            resp = requests.get(
                "https://catalog.archives.gov/api/v2/records/search",
                params={"q": query, "limit": "3"},
                headers={"x-api-key": nara_key, "Content-Type": "application/json"},
                timeout=30,
            )
            print(f"  Query: {query[:60]}")
            print(f"    Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("body", {}).get("hits", {}).get("hits", [])
                total = data.get("body", {}).get("hits", {}).get("total", {}).get("value", 0)
                print(f"    Results: {len(hits)} (total available: {total})")
                for hit in hits[:2]:
                    source = hit.get("_source", {})
                    record = source.get("record", source)
                    title = record.get("title", source.get("title", ""))[:80]
                    naId = source.get("naId", record.get("naId", ""))
                    print(f"      → [{naId}] {title}")
                if not hits:
                    print(f"    Full response: {json.dumps(data, indent=2)[:500]}")
            else:
                print(f"    Error: {resp.text[:300]}")
        except Exception as e:
            print(f"    ERROR: {e}")
        print()


if __name__ == "__main__":
    main()
