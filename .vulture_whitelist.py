# Vulture whitelist for false positives

# Pydantic validators require 'cls' parameter (classmethod)
_.cls  # src/extraction/equipment.py

# API consistency - parameter kept for interface compatibility
_.parsed_dir  # src/extraction/maps.py
