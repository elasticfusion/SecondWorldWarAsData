#!/usr/bin/env python3
"""Test if Grok API truncation is fixed."""

import json
from pathlib import Path
from src.grok_client import GrokClient

# Initialize client
cache_dir = Path("cache/api")
client = GrokClient(cache_dir)

# Simple test prompt
prompt = """Extract places from this text:

"The meeting was held in Washington, then moved to London, with representatives from Paris, Berlin, and Moscow attending."

Return JSON:
{
  "places": [
    {"name": "Washington", "country": "USA"},
    {"name": "London", "country": "UK"},
    {"name": "Paris", "country": "France"},
    {"name": "Berlin", "country": "Germany"},
    {"name": "Moscow", "country": "Russia"}
  ]
}
"""

print("Testing Grok API with max_tokens fix...")
print(f"Cache cleared: places")

try:
    response = client.extract_json(
        prompt=prompt,
        system_prompt="You are a helpful assistant. Return only valid JSON.",
        use_cache=False,
        cache_type="test",
    )

    print(f"✓ Success! Response: {json.dumps(response, indent=2)}")

except Exception as e:
    print(f"✗ Failed: {e}")
