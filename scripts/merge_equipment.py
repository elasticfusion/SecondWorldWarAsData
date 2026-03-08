#!/usr/bin/env python3
"""Manual merge tool for incorrectly split equipment files."""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List

def load_equipment(file_path: Path) -> Dict[str, Any]:
    """Load equipment JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_equipment(file_path: Path, data: Dict[str, Any]) -> None:
    """Save equipment JSON file."""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def merge_equipment(source_path: Path, target_path: Path, keep_source: bool = False) -> None:
    """Merge source equipment into target equipment."""
    source = load_equipment(source_path)
    target = load_equipment(target_path)
    
    # Merge mentions (deduplicate by MentionID)
    existing_mention_ids = {m['MentionID'] for m in target.get('mentions', [])}
    new_mentions = [m for m in source.get('mentions', []) if m['MentionID'] not in existing_mention_ids]
    target.setdefault('mentions', []).extend(new_mentions)
    
    # Merge media (deduplicate by URL)
    existing_urls = {m['url'] for m in target.get('media', [])}
    new_media = [m for m in source.get('media', []) if m['url'] not in existing_urls]
    target.setdefault('media', []).extend(new_media)
    
    # Merge alternate names
    existing_names = set(target.get('alternate_names', []))
    new_names = [n for n in source.get('alternate_names', []) if n not in existing_names]
    target.setdefault('alternate_names', []).extend(new_names)
    
    # Merge variants (deduplicate by variant_name)
    existing_variants = {v['variant_name'] for v in target.get('variants', [])}
    new_variants = [v for v in source.get('variants', []) if v['variant_name'] not in existing_variants]
    target.setdefault('variants', []).extend(new_variants)
    
    # Save merged target
    save_equipment(target_path, target)
    
    # Delete source if requested
    if not keep_source:
        source_path.unlink()
        print(f"✅ Merged and deleted: {source_path.name}")
    else:
        print(f"✅ Merged (kept source): {source_path.name}")
    
    print(f"   → {target_path.name}")
    print(f"   Added: {len(new_mentions)} mentions, {len(new_media)} media, {len(new_names)} names, {len(new_variants)} variants")

def list_equipment() -> None:
    """List all equipment files."""
    equipment_dir = Path('output/equipment')
    files = sorted(equipment_dir.glob('*.json'))
    files = [f for f in files if f.name not in ['index.json', '.processed_events.json']]
    
    print(f"\n📦 Equipment Files ({len(files)}):\n")
    for i, file in enumerate(files, 1):
        data = load_equipment(file)
        mentions = len(data.get('mentions', []))
        media = len(data.get('media', []))
        print(f"{i:2}. {file.stem:40} | {mentions:2} mentions | {media:2} media")

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 scripts/merge_equipment.py list")
        print("  python3 scripts/merge_equipment.py <source_file> <target_file> [--keep-source]")
        print("\nExamples:")
        print("  python3 scripts/merge_equipment.py list")
        print("  python3 scripts/merge_equipment.py aircraft_01KJX915.json combat_aircraft_01KJX906.json")
        print("  python3 scripts/merge_equipment.py Tank_01KJX9DV.json Sherman_tank_01KJX9P1.json --keep-source")
        sys.exit(1)
    
    if sys.argv[1] == 'list':
        list_equipment()
        return
    
    equipment_dir = Path('output/equipment')
    source_file = sys.argv[1]
    target_file = sys.argv[2]
    keep_source = '--keep-source' in sys.argv
    
    source_path = equipment_dir / source_file
    target_path = equipment_dir / target_file
    
    if not source_path.exists():
        print(f"❌ Source not found: {source_path}")
        sys.exit(1)
    
    if not target_path.exists():
        print(f"❌ Target not found: {target_path}")
        sys.exit(1)
    
    print(f"\n🔀 Merging Equipment:")
    print(f"   Source: {source_path.name}")
    print(f"   Target: {target_path.name}\n")
    
    merge_equipment(source_path, target_path, keep_source)

if __name__ == '__main__':
    main()
