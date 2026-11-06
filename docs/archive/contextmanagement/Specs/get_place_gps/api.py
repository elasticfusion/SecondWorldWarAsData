"""Grok API client for GPS geocoding."""

import json
import re
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

def _failed_parse_response() -> dict:
    """Return a failed parse response."""
    return {
        "latitude": None,
        "longitude": None,
        "confidence": 0.0,
        "notes": "Parsing failed – invalid model output format"
    }

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def call_grok(prompt: str) -> dict:
    """Call Grok API with retry."""
    from config import (
        API_KEY, MODEL, API_ENDPOINT, API_TIMEOUT, API_TEMPERATURE, API_MAX_TOKENS
    )

    if not isinstance(prompt, str):
        raise TypeError(f"prompt must be str, got {type(prompt).__name__}")

    logger.debug("Calling Grok API with model %s", MODEL)
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": API_TEMPERATURE,
        "max_tokens": API_MAX_TOKENS,
    }
    try:
        resp = requests.post(
            API_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=API_TIMEOUT
        )
        resp.raise_for_status()
        result = resp.json()
        logger.debug("API call successful")
        return result
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error("API call failed: %s", e)
        raise

def parse_gps_response(content: str) -> dict:
    """Parse GPS response from content."""
    if not isinstance(content, str):
        raise TypeError(f"content must be str, got {type(content).__name__}")

    # Try pure JSON first
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass

    # Look for JSON inside code blocks
    json_block_pattern = r'```(?:json)?\s*(.+?)\s*```'
    match = re.search(json_block_pattern, content, re.DOTALL)
    if match:
        try:
            json_str = match.group(1).strip()
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Fallback: try to find the first { ... } block with proper nesting
    first_brace = content.find('{')
    if first_brace == -1:
        logger.warning("Could not extract valid JSON from model response")
        logger.debug("Raw model content (first 300 chars): %s...", content[:300])
        return _failed_parse_response()

    brace_count = 0
    for i, char in enumerate(content[first_brace:], start=first_brace):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                json_str = content[first_brace:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    break

    logger.warning("Could not extract valid JSON from model response")
    logger.debug("Raw model content (first 300 chars): %s...", content[:300])
    return _failed_parse_response()
