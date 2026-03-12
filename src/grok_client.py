"""Grok API client with retry logic and caching."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from diskcache import Cache
from dotenv import load_dotenv
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.http_pool import get_session

load_dotenv()
logger = logging.getLogger(__name__)


class GrokAPIError(Exception):
    """Grok API error."""


class GrokClient:
    """Client for Grok API with caching and retry logic."""

    def __init__(self, cache_dir: Path, api_key: Optional[str] = None):
        """Initialize Grok client."""
        self.api_key = api_key or os.getenv("GROK_API_KEY")
        if not self.api_key:
            raise ValueError("GROK_API_KEY not found in environment")

        self.base_url = os.getenv(
            "GROK_API_BASE_URL", "https://api.x.ai/v1/chat/completions"
        )
        self.model = os.getenv("GROK_MODEL", "grok-beta")
        self.cache_dir = cache_dir
        self.caches: Dict[str, Cache] = {}  # Cache per extraction type
        self.timeout = 600.0  # 10 minutes for large chapters

        # Load config for debug preview settings
        from src.utils.config import load_config

        config = load_config()
        self.debug_msg_chars = config.get("logging", {}).get(
            "debug_message_preview_chars", 500
        )
        self.debug_resp_chars = config.get("logging", {}).get(
            "debug_response_preview_chars", 500
        )

        # API cache types for entity extraction
        self.CACHE_TYPES = [
            "events",
            "dates",
            "places",
            "people",
            "peoplegroups",
            "weather",
            "supplemental",
        ]

    def _get_cache(self, cache_type: str = "default") -> Cache:
        """Get or create cache for specific type."""
        if cache_type not in self.caches:
            type_cache_dir = self.cache_dir / cache_type
            type_cache_dir.mkdir(parents=True, exist_ok=True)
            self.caches[cache_type] = Cache(str(type_cache_dir))
        return self.caches[cache_type]

    def _make_cache_key(self, prompt: str, temperature: float) -> str:
        """Create cache key from prompt and parameters."""
        import hashlib

        content = f"{prompt}:{temperature}:{self.model}"
        return hashlib.sha256(content.encode()).hexdigest()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type(requests.HTTPError),
        reraise=True,
    )
    def _call_api(self, messages: list, temperature: float = 0.1) -> Dict[str, Any]:
        """Make API call with retry logic."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 131072,  # Grok API maximum
            "stream": False,  # Explicitly disable streaming
        }

        # Log request details at DEBUG level
        logger.debug(f"API Request: POST {self.base_url}")
        logger.debug(f"Model: {self.model}, Temperature: {temperature}")
        logger.debug(f"Messages: {len(messages)} message(s)")
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            logger.debug(f"  Message {i+1} [{role}] ({len(content)} chars):")
            preview = content[: self.debug_msg_chars]
            if len(content) > self.debug_msg_chars:
                preview += "..."
            logger.debug(f"    {preview}")

        with get_session() as session:
            # Note: timeout set in get_session(), not on session object
            response = session.post(
                self.base_url, headers=headers, json=payload, timeout=self.timeout
            )

            logger.debug(f"API Response: {response.status_code}")

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning(f"Rate limit hit, waiting {retry_after}s")
                import time

                time.sleep(retry_after)
                response.raise_for_status()

            if response.status_code >= 500:
                # Retry on 5xx errors
                response.raise_for_status()

            if response.status_code != 200:
                raise GrokAPIError(f"API error {response.status_code}: {response.text}")

            result = response.json()

            # Log full response structure at DEBUG
            logger.debug(f"Full API response keys: {result.keys()}")

            usage = result.get("usage", {})
            logger.info(
                f"Response tokens - prompt: {usage.get('prompt_tokens', 0)}, "
                f"completion: {usage.get('completion_tokens', 0)}, "
                f"total: {usage.get('total_tokens', 0)}"
            )

            # Log response content preview
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0].get("message", {}).get("content", "")
                finish_reason = result["choices"][0].get("finish_reason", "unknown")
                logger.info(
                    f"Response: {len(content)} chars, finish_reason: {finish_reason}"
                )

                # Warn if truncated or suspiciously short
                if finish_reason == "length":
                    logger.error(f"API response truncated due to max_tokens limit!")
                elif finish_reason != "stop":
                    logger.warning(f"Unexpected finish_reason: {finish_reason}")

                # Warn if response is suspiciously short
                if len(content) < 200:
                    logger.warning(
                        f"API returned very short response: {len(content)} chars"
                    )
                    logger.warning(f"Content: {content}")

                preview = content[: self.debug_resp_chars]
                if len(content) > self.debug_resp_chars:
                    preview += "..."
                logger.debug(f"  {preview}")

            return result

    def chat_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
    ) -> str:
        """
        Get chat completion from Grok API.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0-1)
            use_cache: Whether to use cache
            cache_type: Cache type (events, dates, places, people, etc.)

        Returns:
            Response text from API
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache
        cache_key = self._make_cache_key(prompt, temperature)

        # Log to file
        from datetime import datetime
        from pathlib import Path

        prompt_log = Path("logs") / "api_prompts.log"
        prompt_log.parent.mkdir(parents=True, exist_ok=True)

        if use_cache and cache_key in cache:
            with open(prompt_log, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                f.write(f"Cache Type: {cache_type}\n")
                f.write(f"Cache Key: {cache_key}\n")
                f.write("Status: CACHE HIT\n")
                f.write("=" * 80 + "\n\n")
            return cache[cache_key]

        # Log API call
        with open(prompt_log, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Cache Type: {cache_type}\n")
            f.write(f"Cache Key: {cache_key}\n")
            f.write(f"Temperature: {temperature}\n")
            f.write("Status: API CALL\n")
            if system_prompt:
                f.write(f"System Prompt: {system_prompt}\n")
            f.write("=" * 80 + "\n")
            f.write(prompt)
            f.write(f"\n{'='*80}\n\n")

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Call API
        response = self._call_api(messages, temperature)

        # Extract content
        content = response["choices"][0]["message"]["content"]

        # Cache result
        if use_cache:
            cache[cache_key] = content

        return content

    def extract_json_with_image_base64(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
    ) -> Dict[str, Any]:
        """
        Get JSON response from Grok API with base64 image input (vision).

        Args:
            prompt: User prompt
            image_base64: Base64-encoded image data
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            use_cache: Whether to use cache
            cache_type: Cache type

        Returns:
            Parsed JSON response
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache (use hash of image data in cache key)
        import hashlib

        image_hash = hashlib.sha256(image_base64.encode()).hexdigest()[:16]
        cache_key = self._make_cache_key(f"{prompt}:img_{image_hash}", temperature)

        if use_cache and cache_key in cache:
            logger.info(f"Cache hit for image analysis")
            return cache[cache_key]

        # Detect image type from base64 header
        if image_base64.startswith("/9j/"):
            content_type = "image/jpeg"
        elif image_base64.startswith("iVBORw"):
            content_type = "image/png"
        elif image_base64.startswith("R0lGOD"):
            content_type = "image/gif"
        else:
            content_type = "image/jpeg"  # default

        image_data_url = f"data:{content_type};base64,{image_base64}"

        # Build messages with image
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append(
            {
                "role": "user",
                "content": [  # type: ignore[dict-item]
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        )

        # Call API
        response = self._call_api(messages, temperature)

        # Extract content
        content = response["choices"][0]["message"]["content"]

        # Parse JSON
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Sanitize control characters
        import re

        content = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", content)
        content = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            raise GrokAPIError(
                f"Failed to parse JSON response: {e}\nContent: {content[:500]}"
            )

        # Cache result
        if use_cache:
            cache[cache_key] = result

        return result

    def extract_json_with_image(
        self,
        prompt: str,
        image_url: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
        image_timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Get JSON response from Grok API with image input (vision).

        Args:
            prompt: User prompt
            image_url: URL of image to analyze (will be downloaded and sent as base64)
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            use_cache: Whether to use cache
            cache_type: Cache type

        Returns:
            Parsed JSON response
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache (include image URL in cache key)
        cache_key = self._make_cache_key(f"{prompt}:{image_url}", temperature)

        if use_cache and cache_key in cache:
            logger.info(f"Cache hit for image analysis")
            return cache[cache_key]

        # Download image and convert to base64
        import base64

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; WWII-Data-Extractor/1.0)"
            }
            session = get_session()
            img_response = session.get(
                image_url, timeout=image_timeout, headers=headers, allow_redirects=True
            )
            img_response.raise_for_status()

            # Get content type
            content_type = img_response.headers.get("content-type", "image/jpeg")

            # Encode to base64
            img_base64 = base64.b64encode(img_response.content).decode("utf-8")
            image_data_url = f"data:{content_type};base64,{img_base64}"

            logger.debug(
                f"Downloaded image: {len(img_response.content)} bytes, type: {content_type}"
            )

        except Exception as e:
            raise GrokAPIError(f"Failed to download image from {image_url}: {e}")

        # Build messages with image
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # User message with image (base64 data URL)
        messages.append(
            {
                "role": "user",
                "content": [  # type: ignore[dict-item]
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        )

        # Call API
        response = self._call_api(messages, temperature)

        # Extract content
        content = response["choices"][0]["message"]["content"]

        # Parse JSON
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Sanitize control characters
        import re

        content = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", content)
        content = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            raise GrokAPIError(
                f"Failed to parse JSON response: {e}\nContent: {content[:500]}"
            )

        # Cache result
        if use_cache:
            cache[cache_key] = result

        return result

    def extract_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
    ) -> Dict[str, Any]:
        """
        Get JSON response from Grok API.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            use_cache: Whether to use cache
            cache_type: Cache type (events, dates, places, people, etc.)

        Returns:
            Parsed JSON response
        """
        response = self.chat_completion(
            prompt, system_prompt, temperature, use_cache, cache_type
        )

        # Extract JSON from response (may be wrapped in markdown)
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]

        response = response.strip()

        # Pre-process: sanitize BEFORE first parse attempt
        import re

        # Remove control characters (0x00-0x1f except whitespace)
        response = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", response)
        # Fix invalid escape sequences (valid JSON escapes: " \ / b f n r t u)
        response = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", response)

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            error_msg = str(e)

            # Check if response is suspiciously short (likely API error, not truncation)
            if len(response) < 500:
                logger.error(
                    "API returned short/invalid response (%d chars)", len(response)
                )
                logger.debug("Response: %s", response)
                raise GrokAPIError(
                    f"API returned invalid response: {error_msg}. "
                    f"Response: {response[:200]}"
                ) from e

            # Check if response appears truncated (unterminated string/array/object)
            if "Unterminated string" in error_msg or "Expecting" in error_msg:
                logger.error(
                    "Response truncated at %d chars - API hit token limit",
                    len(response),
                )
                logger.error("JSON error: %s", error_msg)
                logger.debug("Last 200 chars: ...%s", response[-200:])
                raise GrokAPIError(
                    f"API response truncated (likely hit max_tokens limit). "
                    f"Response length: {len(response)} chars. "
                    f"Consider splitting this chapter into smaller sections."
                ) from e

            # Try to fix common issues
            repaired = response

            # Fix 1: Invalid escape sequences
            if "Invalid" in error_msg and "escape" in error_msg:
                logger.debug("Attempting to fix invalid escape sequences...")
                # Find and fix common invalid escapes in strings
                # More aggressive fix: replace ANY backslash not followed by valid escape
                # Valid JSON escapes: " \ / b f n r t u
                repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", repaired)

                # Also fix single backslashes at end of strings
                repaired = re.sub(r'\\(?=["}\]])', r"\\\\", repaired)

                try:
                    result = json.loads(repaired)
                    logger.info("✓ JSON repaired successfully (invalid escapes fixed)")
                    return result
                except json.JSONDecodeError as e2:
                    logger.debug("Escape fix didn't work: %s", e2)

                    # Try more aggressive: remove all problematic backslashes
                    try:
                        # Replace backslash followed by anything except valid escapes
                        ultra_clean = re.sub(r'\\([^"\\/bfnrtu])', r"\1", response)
                        result = json.loads(ultra_clean)
                        logger.info("✓ JSON repaired (removed invalid backslashes)")
                        return result
                    except json.JSONDecodeError as e3:
                        logger.debug("Ultra clean didn't work: %s", e3)

            # Fix 2: Try removing escaped brackets (sometimes Grok over-escapes)
            try:
                cleaned = repaired.replace(r"\[", "[").replace(r"\]", "]")
                return json.loads(cleaned)
            except json.JSONDecodeError:
                raise GrokAPIError(
                    f"Failed to parse JSON response: {e}\n"
                    f"Response length: {len(response)} chars\n"
                    f"First 500 chars: {response[:500]}\n"
                    f"Last 500 chars: {response[-500:]}"
                ) from e

    def extract_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: Optional[str] = None,
        use_cache: bool = True,
        cache_type: str = "default",
    ) -> BaseModel:
        """
        Get structured output from Grok API.

        Falls back to JSON parsing with schema validation since xAI SDK
        structured outputs may not be fully reliable yet.

        Args:
            prompt: User prompt
            schema: Pydantic model class defining the output structure
            system_prompt: Optional system prompt
            use_cache: Whether to use cache
            cache_type: Cache type (events, dates, places, people, etc.)

        Returns:
            Pydantic model instance matching the schema
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache
        cache_key = self._make_cache_key(f"{prompt}:{schema.__name__}", 0.1)

        if use_cache and cache_key in cache:
            logger.info("Cache hit for %s", schema.__name__)
            cached_data = cache[cache_key]
            return schema.model_validate(cached_data)

        logger.info("Calling Grok API for %s", schema.__name__)

        # Use regular JSON extraction with higher token limit
        json_response = self.extract_json(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            use_cache=False,  # We handle caching here
            cache_type=cache_type,
        )

        # Validate and parse with Pydantic
        parsed = schema.model_validate(json_response)

        # Cache the result
        cache[cache_key] = parsed.model_dump()

        return parsed

    def clear_cache(self, cache_type: Optional[str] = None):
        """
        Clear the cache.

        Args:
            cache_type: Specific cache type to clear, or None for all
        """
        if cache_type:
            if cache_type in self.caches:
                self.caches[cache_type].clear()
        else:
            for cache in self.caches.values():
                cache.clear()
