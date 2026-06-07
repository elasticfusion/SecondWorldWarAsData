"""Prompt template loader — S3 override with local fallback."""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

LOCAL_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> Dict[str, Any]:
    """Load a prompt template by name. Checks S3 first, falls back to local.

    Args:
        name: Prompt name without extension (e.g., 'people', 'places')

    Returns:
        Dict with keys: prompt_template, schema, system_prompt, rules, etc.
    """
    # Try S3 override
    s3_content = _load_from_s3(name)
    if s3_content:
        logger.debug("Loaded prompt '%s' from S3", name)
        return s3_content

    # Fall back to local file
    local_path = LOCAL_PROMPTS_DIR / f"{name}.yaml"
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            logger.debug("Loaded prompt '%s' from %s", name, local_path)
            return yaml.safe_load(f)

    raise FileNotFoundError(f"Prompt template '{name}' not found in S3 or {local_path}")


def render_prompt(name: str, **kwargs: Any) -> str:
    """Load and render a prompt template with variables.

    Args:
        name: Prompt name (e.g., 'people')
        **kwargs: Variables to interpolate (book, author, text, etc.)

    Returns:
        Rendered prompt string
    """
    tmpl = load_prompt(name)
    prompt = tmpl["prompt_template"]

    # Inject schema if referenced
    if "{schema}" in prompt and "schema" in tmpl:
        kwargs.setdefault("schema", tmpl["schema"])

    # Append rules if present
    rules = tmpl.get("rules", [])
    if rules:
        rules_text = "\n".join(f"- {r}" for r in rules)
        prompt = prompt.rstrip() + "\n\n" + rules_text

    # Safe substitution: replace {var} without breaking JSON braces
    for key, value in kwargs.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt


def get_system_prompt(name: str) -> Optional[str]:
    """Get the system prompt for a template, if defined."""
    tmpl = load_prompt(name)
    return tmpl.get("system_prompt")


def _load_from_s3(name: str) -> Optional[Dict[str, Any]]:
    """Try to load prompt from S3. Returns None if not available."""
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return None

    try:
        from src.utils.config import load_config

        cfg = load_config()
        if not cfg.get("aws", {}).get("enabled"):
            return None

        import boto3

        region = cfg["aws"].get("region", "us-east-1")
        s3 = boto3.client("s3", region_name=region)
        key = f"prompts/{name}.yaml"
        resp = s3.get_object(Bucket=bucket, Key=key)
        content = resp["Body"].read().decode("utf-8")
        return yaml.safe_load(content)
    except Exception:
        return None


def clear_cache() -> None:
    """Clear the prompt cache (useful after S3 updates)."""
    load_prompt.cache_clear()
