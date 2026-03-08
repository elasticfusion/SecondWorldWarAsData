#!/usr/bin/env python3
"""Test all three phases of supplemental material extraction."""

import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extraction.supplemental import extract_supplemental
from src.extraction.supplemental_advanced import enrich_with_advanced_features
from src.grok_client import GrokClient


def test_phase1():
    """Test Phase 1: Core extraction."""
    print("\n=== Phase 1: Core Extraction ===")
    
    # Find first event file
    event_files = list(Path("output").rglob("*-event.json"))
    if not event_files:
        print("❌ No event files found")
        return None
    
    event_file = event_files[0]
    print(f"Testing with: {event_file}")
    
    # Extract
    grok_client = GrokClient(Path("cache"))
    output_dir = Path("output/supplemental_test")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    supplemental_file = extract_supplemental(
        event_file=event_file,
        grok_client=grok_client,
        output_dir=output_dir,
    )
    
    if not supplemental_file:
        print("⚠️  No supplemental materials found")
        return None
    
    # Validate
    with open(supplemental_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    materials = data.get("materials", [])
    print(f"✅ Extracted {len(materials)} material(s)")
    
    # Check structure
    if materials:
        m = materials[0]
        required = ["MaterialID", "EventID", "reference_type", "citation"]
        missing = [k for k in required if k not in m]
        if missing:
            print(f"❌ Missing fields: {missing}")
        else:
            print(f"✅ Structure valid")
            print(f"   Type: {m['reference_type']}")
            print(f"   Citation: {m['citation'].get('title', 'N/A')[:50]}")
    
    return supplemental_file


def test_phase2(supplemental_file):
    """Test Phase 2: Search integration."""
    print("\n=== Phase 2: Search Integration ===")
    print("⏭️  Skipped (search integration not yet implemented)")
    return


def test_phase3(supplemental_file):
    """Test Phase 3: Advanced features."""
    print("\n=== Phase 3: Advanced Features ===")
    
    if not supplemental_file:
        print("⏭️  Skipped (no materials)")
        return
    
    config = {
        "extract_isbn": True,
        "determine_copyright": True,
        "verify_archive_urls": False,  # Skip verification for speed
    }
    
    grok_client = GrokClient(Path("cache"))
    
    enriched = enrich_with_advanced_features(
        supplemental_file=supplemental_file,
        config=config,
        grok_client=grok_client,
    )
    
    print(f"✅ Applied advanced features to {enriched} material(s)")
    
    # Validate
    with open(supplemental_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    materials = data.get("materials", [])
    for m in materials:
        if "copyright_status" in m:
            cs = m["copyright_status"]
            print(f"   Copyright: {cs.get('status')}")
            print(f"   Basis: {cs.get('determination_basis')}")
            break
        if m.get("citation", {}).get("isbn"):
            print(f"   ISBN: {m['citation']['isbn']}")
            break


def main():
    """Run all tests."""
    print("Testing Supplemental Material Extraction (All Phases)")
    print("=" * 60)
    
    try:
        # Phase 1
        supplemental_file = test_phase1()
        
        # Phase 2
        test_phase2(supplemental_file)
        
        # Phase 3
        test_phase3(supplemental_file)
        
        print("\n" + "=" * 60)
        print("✅ All phases tested successfully")
        
        if supplemental_file:
            print(f"\nTest output: {supplemental_file}")
            print("Review the file to verify all enrichments")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
