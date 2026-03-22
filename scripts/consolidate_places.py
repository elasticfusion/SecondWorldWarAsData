#!/usr/bin/env python3
"""Apply place_aliases.yaml hierarchy and aliases to existing place files."""

import json
from pathlib import Path

import yaml


def load_config():
    """Load place_aliases.yaml."""
    with open("config/place_aliases.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_hierarchy_map(config):
    """Build name -> {continent, country, region, parent_place_id} from hierarchies."""
    result = {}
    for entry in config.get("hierarchies", []):
        path = entry.get("path", "")
        parts = [p.strip() for p in path.split(">")]
        if len(parts) < 2:
            continue
        name = parts[-1]
        hierarchy = {"continent": parts[0]}
        if len(parts) >= 3:
            hierarchy["country"] = parts[1]
        if len(parts) >= 4:
            hierarchy["region"] = parts[2]
        if entry.get("parent_id"):
            hierarchy["parent_place_id"] = entry["parent_id"]
        result[name.lower()] = hierarchy
    return result


def _build_alias_map(config):
    """Build alias -> canonical name from aliases section."""
    result = {}
    for group in config.get("aliases", []):
        canonical = group.get("canonical", "")
        for alias in group.get("aliases", []):
            result[alias.lower()] = canonical
        # Also build historical_names from name_changes
        name_changes = group.get("name_changes", [])
        if name_changes:
            result[f"_hist_{canonical.lower()}"] = name_changes
    for feat in config.get("geographic_features", []):
        canonical = feat.get("canonical", "")
        for alias in feat.get("aliases", []):
            result[alias.lower()] = canonical
    return result


def _apply_hierarchy(data, key, hierarchy_map):
    """Apply hierarchy from YAML if missing. Returns True if changed."""
    if key in hierarchy_map and not data.get("hierarchy"):
        data["hierarchy"] = hierarchy_map[key]
        return True
    return False


def _apply_aliases(data, key, alias_map):
    """Apply canonical alias if found. Returns True if changed."""
    val = alias_map.get(key)
    if not isinstance(val, str):
        return False
    aliases = data.get("aliases", [])
    if val not in aliases and val.lower() != key:
        aliases.append(val)
        data["aliases"] = aliases
        return True
    return False


def _apply_historical_names(data, key, alias_map):
    """Apply historical names from name_changes. Returns True if changed."""
    hist_key = f"_hist_{key}"
    if hist_key not in alias_map:
        return False
    existing = {h["name"] for h in data.get("historical_names", [])}
    changed = False
    for nc in alias_map[hist_key]:
        if isinstance(nc, dict) and nc.get("name") not in existing:
            entry = {"name": nc["name"], "language": "Other"}
            if nc.get("period"):
                entry["date_range"] = nc["period"]
            data.setdefault("historical_names", []).append(entry)
            changed = True
    return changed


def consolidate(places_dir, config):
    """Apply hierarchy and aliases to place files."""
    hierarchy_map = _build_hierarchy_map(config)
    alias_map = _build_alias_map(config)

    skip = {"index.json", "duplicate_report.json", ".processed_events.json"}
    updated = 0

    for f in places_dir.glob("*.json"):
        if f.name in skip:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        name = data.get("current_name") or data.get("name", "")
        key = name.lower()

        c1 = _apply_hierarchy(data, key, hierarchy_map)
        c2 = _apply_aliases(data, key, alias_map)
        c3 = _apply_historical_names(data, key, alias_map)

        if c1 or c2 or c3:
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            updated += 1

    return updated


def main():
    """Main entry point."""
    config = load_config()
    places_dir = Path("output/places")
    updated = consolidate(places_dir, config)
    print(f"Updated {updated} place files from place_aliases.yaml")


if __name__ == "__main__":
    main()
