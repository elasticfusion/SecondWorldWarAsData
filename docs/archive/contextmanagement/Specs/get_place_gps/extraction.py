"""Place extraction from event data."""

from typing import List

# Fallback key for places (seen in some event structures)
FALLBACK_PLACES_KEY = "|"

def _extract_places_from_list(lst: list) -> set:
    """Extract and normalize places from a list."""
    return {s.strip() for s in lst if s.strip()}

def extract_places(event: dict) -> List[str]:
    """Extract places from event dict."""
    places = set()
    # Primary key
    if lst := event.get("Sub-Event-Places"):
        places.update(_extract_places_from_list(lst))
    # Fallback key seen in first sub-event
    if lst := event.get(FALLBACK_PLACES_KEY):
        places.update(_extract_places_from_list(lst))
    return sorted(places)
