"""Search query template loader — externalizes third-party search patterns."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger(__name__)

SEARCH_QUERIES_DIR = Path(__file__).parent.parent.parent / "search_queries"


@lru_cache(maxsize=16)
def load_search_queries(name: str) -> dict:
    """Load search query templates by name (e.g., 'people', 'equipment').

    Returns dict mapping category → list of template strings.
    """
    path = SEARCH_QUERIES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Search queries '{name}' not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_search_queries(query_file: str, category: str, **kwargs) -> List[str]:
    """Load and render search query templates with variables.

    Args:
        query_file: Query file name (e.g., 'people', 'bibliography')
        category: Category within the file (e.g., 'portrait_images', 'gutenberg')
        **kwargs: Variables to substitute (e.g., name="Eisenhower")

    Returns:
        List of rendered query strings
    """
    templates = load_search_queries(query_file)
    query_templates = templates.get(category, [])
    if not query_templates:
        logger.warning("No search queries for %s/%s", query_file, category)
        return []
    rendered = []
    for tmpl in query_templates:
        query = tmpl
        for key, value in kwargs.items():
            query = query.replace("{" + key + "}", str(value))
        rendered.append(query)
    return rendered
