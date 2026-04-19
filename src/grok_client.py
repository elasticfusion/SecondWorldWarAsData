"""Grok API client with retry logic and caching."""

import contextvars
import json
import logging
import os
import threading
import time as _time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

# Context variable for per-book cache routing (thread/async safe)
current_book: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_book", default=None
)
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


class _RateLimiter:
    """Thread-safe token-bucket rate limiter.

    Enforces a minimum interval between API calls and backs off
    when 429 responses are received.
    """

    def __init__(self, calls_per_minute: int = 30):
        self._lock = threading.Lock()
        self._min_interval = 60.0 / max(calls_per_minute, 1)
        self._last_call = 0.0
        self._backoff_until = 0.0

    def wait(self) -> None:
        """Block until the next call is allowed."""
        with self._lock:
            now = _time.monotonic()
            earliest = max(self._last_call + self._min_interval, self._backoff_until)
            delay = earliest - now
            # Claim the slot before releasing lock
            self._last_call = max(now, earliest)
        if delay > 0:
            logger.debug("Rate limiter: waiting %.1fs", delay)
            _time.sleep(delay)

    def backoff(self, seconds: float) -> None:
        """Set a backoff period after a 429 response."""
        with self._lock:
            self._backoff_until = _time.monotonic() + seconds
            logger.info("Rate limiter: backing off %.0fs after 429", seconds)


class BatchModeCollecting(Exception):
    """Raised in batch mode when a request is queued instead of sent."""


class GrokClient:
    """Client for Grok API with caching and retry logic."""

    def __init__(
        self,
        cache_dir: "Path | CacheBackend",
        api_key: Optional[str] = None,
        batch_mode: bool = False,
    ):
        """Initialize Grok client.

        Args:
            cache_dir: Path for DiskCacheBackend (backwards-compatible) or a CacheBackend instance.
            api_key: Grok API key (falls back to GROK_API_KEY env var).
            batch_mode: If True, collect requests for xAI Batch API.
        """
        from src.utils.cache_backend import CacheBackend, DiskCacheBackend

        if isinstance(cache_dir, Path):
            self.cache_dir = cache_dir
            self._cache_backend = DiskCacheBackend(cache_dir)
        else:
            self.cache_dir = None
            self._cache_backend = cache_dir
        self.api_key = api_key or os.getenv("GROK_API_KEY")
        if not self.api_key:
            raise ValueError("GROK_API_KEY not found in environment")

        self.base_url = os.getenv(
            "GROK_API_BASE_URL", "https://api.x.ai/v1/chat/completions"
        )
        self.model = os.getenv("GROK_MODEL", "grok-beta")
        self.cache_dir = cache_dir
        self.caches: Dict[str, Any] = {}  # Cache per extraction type
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

        # Book-specific cache types (prompt includes chapter text)
        self.BOOK_CACHE_TYPES = {
            "events",
            "weather",
            "equipment",
            "logistics",
            "casualties",
            "supplemental",
            "supplemental_narrative",
            "supplemental_search",
            "supplemental_advanced",
        }

        # Rate limiter — shared across all threads
        calls_per_minute = config.get("api", {}).get("calls_per_minute", 30)
        self._rate_limiter = _RateLimiter(calls_per_minute)

        # Batch mode — collect requests instead of sending them
        self.batch_mode = batch_mode
        self._batch_collector = None
        self._batch_meta: Dict[str, str] = {}
        if batch_mode:
            from src.utils.batch_api import BatchCollector

            self._batch_collector = BatchCollector()

    def _get_cache(self, cache_type: str = "default") -> "CacheBackend":
        """Get or create cache for specific type.

        Book-specific types route to books/{book_name}/{cache_type}/
        when current_book context var is set. Global types always use {cache_type}/.
        """
        book_name = current_book.get()
        if book_name and cache_type in self.BOOK_CACHE_TYPES:
            cache_key = f"books/{book_name}/{cache_type}"
        else:
            cache_key = cache_type

        if cache_key not in self.caches:
            self.caches[cache_key] = self._cache_backend.get_sub_cache(cache_key)
        return self.caches[cache_key]

    def _make_cache_key(self, prompt: str, temperature: float) -> str:
        """Create cache key from prompt and parameters."""
        import hashlib

        content = f"{prompt}:{temperature}:{self.model}"
        return hashlib.sha256(content.encode()).hexdigest()

    def clear_cache_entry(
        self, prompt: str, cache_type: str = "default", temperature: float = 0.1
    ) -> bool:
        """Remove a single cache entry by prompt. Returns True if removed."""
        cache = self._get_cache(cache_type)
        key = self._make_cache_key(prompt, temperature)
        return cache.pop(key, None) is not None

    def submit_batch(self, batch_name: str = "pipeline") -> Optional[str]:
        """Submit collected batch requests to xAI Batch API.

        Returns batch_id, or None if no requests to submit.
        """
        if not self._batch_collector or len(self._batch_collector) == 0:
            logger.info("No batch requests to submit (all cached)")
            return None

        from src.utils.batch_api import submit_batch, poll_batch, retrieve_results

        # Write JSONL
        jsonl_path = self.cache_dir / "batch_requests.jsonl"
        count = self._batch_collector.write_jsonl(jsonl_path)
        logger.info("Wrote %d requests to %s", count, jsonl_path)

        # Save request metadata for cache population later
        self._batch_meta = {
            r.request_id: r.cache_type for r in self._batch_collector.requests
        }

        # Submit
        batch_id = submit_batch(self.api_key, jsonl_path, batch_name)

        # Poll
        logger.info("Waiting for batch to complete (may take minutes to hours)...")
        poll_batch(self.api_key, batch_id)

        # Retrieve and populate cache
        results = retrieve_results(self.api_key, batch_id)
        populated = 0
        for request_id, content in results.items():
            cache_type = self._batch_meta.get(request_id, "default")
            cache = self._get_cache(cache_type)
            import re

            content = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", content)
            content = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", content)
            cache[request_id] = content
            populated += 1

        logger.info("Populated %d cache entries from batch results", populated)

        # Clean up
        jsonl_path.unlink(missing_ok=True)
        self._batch_collector = None
        self.batch_mode = False

        return batch_id

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type(requests.HTTPError),
        reraise=True,
    )
    def _validate_input_size(self, messages: list) -> None:
        """Validate input size and warn if approaching context limit."""
        total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
        estimated_tokens = total_chars // 4

        if estimated_tokens > 100000:
            logger.warning(
                f"Large input: ~{estimated_tokens:,} tokens ({total_chars:,} chars). "
                f"May hit context limit."
            )

    def _log_api_request(self, messages: list, temperature: float) -> None:
        """Log API request details at DEBUG level."""
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

    def _log_api_response(self, result: Dict[str, Any]) -> None:
        """Log API response details."""
        logger.debug(f"Full API response keys: {result.keys()}")

        # Log token usage
        usage = result.get("usage", {})
        logger.info(
            f"Response tokens - prompt: {usage.get('prompt_tokens', 0)}, "
            f"completion: {usage.get('completion_tokens', 0)}, "
            f"total: {usage.get('total_tokens', 0)}"
        )

        # Log response content
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")
            finish_reason = result["choices"][0].get("finish_reason", "unknown")
            logger.info(
                f"Response: {len(content)} chars, finish_reason: {finish_reason}"
            )

            # Warn about issues
            if finish_reason == "length":
                logger.error("API response truncated due to max_tokens limit!")
            elif finish_reason != "stop":
                logger.warning(f"Unexpected finish_reason: {finish_reason}")

            if len(content) < 200:
                try:
                    json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        f"API returned very short response: {len(content)} chars"
                    )
                    logger.warning(f"Content: {content}")

            # Log preview
            preview = content[: self.debug_resp_chars]
            if len(content) > self.debug_resp_chars:
                preview += "..."
            logger.debug(f"  {preview}")

    def _handle_api_errors(self, response) -> None:
        """Handle API error responses."""
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            self._rate_limiter.backoff(retry_after)
            response.raise_for_status()

        if response.status_code >= 500:
            response.raise_for_status()

        if response.status_code != 200:
            raise GrokAPIError(f"API error {response.status_code}: {response.text}")

    def _post_with_deadline(self, session, headers, payload):
        """POST with hard wall-clock deadline to prevent indefinite hangs."""
        import concurrent.futures

        def _do_post():
            return session.post(
                self.base_url, headers=headers, json=payload, timeout=(10, self.timeout)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_post)
            try:
                return future.result(timeout=self.timeout)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise GrokAPIError(
                    f"API call exceeded {self.timeout}s wall-clock deadline"
                )

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type(requests.HTTPError),
        reraise=True,
    )
    def _call_api(self, messages: list, temperature: float = 0.1) -> Dict[str, Any]:
        """Make API call with retry logic."""
        self._rate_limiter.wait()
        self._validate_input_size(messages)
        self._log_api_request(messages, temperature)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 131072,
            "stream": False,
        }

        with get_session() as session:
            response = self._post_with_deadline(session, headers, payload)
            logger.debug(f"API Response: {response.status_code}")

            self._handle_api_errors(response)
            result = response.json()

            # Validate response structure
            if "choices" not in result or not result["choices"]:
                raise GrokAPIError(f"Invalid API response structure: {result}")

            content = result["choices"][0]["message"]["content"]

            # Reject truly empty responses, but allow valid short JSON ([], {})
            if not content or not content.strip():
                raise GrokAPIError("API returned empty response")

            self._log_api_response(result)

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

        if use_cache and cache_key in cache:
            logger.debug("[API] CACHE HIT | type=%s key=%s", cache_type, cache_key[:16])
            return cache[cache_key]

        # Batch mode: queue request instead of calling API
        if self.batch_mode:
            from src.utils.batch_api import BatchRequest

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            self._batch_collector.add(
                BatchRequest(
                    request_id=cache_key,
                    messages=messages,
                    model=self.model,
                    temperature=temperature,
                    cache_type=cache_type,
                )
            )
            raise BatchModeCollecting(cache_key)

        # Log API call
        logger.debug(
            "[API] CALL | type=%s key=%s temp=%.1f",
            cache_type,
            cache_key[:16],
            temperature,
        )
        if hasattr(logger, "trace"):
            logger.trace("[API] Prompt (%s): %s", cache_type, prompt[:500])  # type: ignore[attr-defined]

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Call API
        response = self._call_api(messages, temperature)

        # Extract content
        content = response["choices"][0]["message"]["content"]

        # Sanitize control characters and invalid escapes BEFORE caching
        import re

        # Remove control characters (0x00-0x1f except whitespace)
        content = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", content)
        # Fix invalid escape sequences - more aggressive approach
        # Valid JSON escapes: \" \\ \/ \b \f \n \r \t \uXXXX
        # Replace any backslash not followed by valid escape with double backslash
        content = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", content)

        # Cache result
        if use_cache:
            cache[cache_key] = content

        return content

    def _detect_image_type(self, image_base64: str) -> str:
        """Detect image MIME type from base64 header."""
        if image_base64.startswith("/9j/"):
            return "image/jpeg"
        elif image_base64.startswith("iVBORw"):
            return "image/png"
        elif image_base64.startswith("R0lGOD"):
            return "image/gif"
        return "image/jpeg"  # default

    def _build_vision_messages(
        self, prompt: str, image_data_url: str, system_prompt: Optional[str] = None
    ) -> list:
        """Build messages for vision API call."""
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
        return messages

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
            logger.info("Cache hit for image analysis")
            return cache[cache_key]

        # Build image data URL
        content_type = self._detect_image_type(image_base64)
        image_data_url = f"data:{content_type};base64,{image_base64}"

        # Build and send messages
        messages = self._build_vision_messages(prompt, image_data_url, system_prompt)
        response = self._call_api(messages, temperature)

        # Extract and parse content
        content = response["choices"][0]["message"]["content"]
        content = self._strip_markdown_wrapper(content)
        content = self._sanitize_json_response(content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            clear_cmd = self._make_cache_clear_command(cache_type, prompt, temperature)
            logger.error(f"💡 Clear cache: {clear_cmd}")
            raise GrokAPIError(
                f"Failed to parse JSON response: {e}\nContent: {content[:500]}"
            ) from e

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

        # Sanitize
        content = self._sanitize_json_response(content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            clear_cmd = self._make_cache_clear_command(cache_type, prompt, temperature)
            logger.error(f"💡 Clear cache: {clear_cmd}")
            raise GrokAPIError(
                f"Failed to parse JSON response: {e}\nContent: {content[:500]}"
            )

        # Cache result
        if use_cache:
            cache[cache_key] = result

        return result

    def _strip_markdown_wrapper(self, response: str) -> str:
        """Remove markdown code block wrapper from response."""
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        return response.strip()

    _VALID_ESCAPES = frozenset('"\\/bfnrt')
    _CTRL_ESCAPE = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}

    def _sanitize_json_response(self, response: str) -> str:
        """Sanitize JSON response by removing control chars and fixing invalid escapes."""
        import re

        # Remove non-whitespace control characters
        response = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", response)

        result = []
        i = 0
        in_string = False
        n = len(response)
        while i < n:
            ch = response[i]
            if not in_string:
                if ch == '"':
                    in_string = True
                result.append(ch)
                i += 1
            else:
                i, in_string = self._sanitize_string_char(
                    response, i, n, result, in_string
                )
        return "".join(result)

    def _sanitize_string_char(
        self, response: str, i: int, n: int, result: list, in_string: bool
    ) -> tuple:
        """Process one character inside a JSON string. Returns (new_i, in_string)."""
        ch = response[i]
        if ch == '"':
            result.append(ch)
            return i + 1, False
        if ch == "\\" and i + 1 < n:
            return self._sanitize_escape(response, i, n, result)
        if ch in self._CTRL_ESCAPE:
            result.append(self._CTRL_ESCAPE[ch])
        else:
            result.append(ch)
        return i + 1, in_string

    @staticmethod
    def _sanitize_escape(response: str, i: int, n: int, result: list) -> tuple:
        """Handle an escape sequence inside a JSON string. Returns (new_i, True)."""
        nxt = response[i + 1]
        if nxt in GrokClient._VALID_ESCAPES:
            result.append(response[i : i + 2])
            return i + 2, True
        if (
            nxt == "u"
            and i + 5 < n
            and all(c in "0123456789abcdefABCDEF" for c in response[i + 2 : i + 6])
        ):
            result.append(response[i : i + 6])
            return i + 6, True
        # Invalid escape — skip the backslash
        return i + 1, True

    def _make_cache_clear_command(
        self, cache_type: str, prompt: str, temperature: float
    ) -> str:
        """Generate cache clearing command for specific entry."""
        cache = self._get_cache(cache_type)
        cache_key = self._make_cache_key(prompt, temperature)
        return (
            f'python3 -c "from diskcache import Cache; '
            f"c=Cache('{cache.directory}'); "
            f"c.pop('{cache_key}', None); "
            f"print('Cache entry cleared')\""
        )

    def _handle_short_response_error(
        self,
        response: str,
        error_msg: str,
        cache_type: str,
        prompt: str,
        temperature: float,
    ) -> None:
        """Handle short/invalid response errors with auto-clear."""
        logger.error(
            "API returned short/invalid response (%d chars) — cache cleared, will retry",
            len(response),
        )
        logger.debug("Response: %s", response)

        # Auto-clear corrupted cache entry
        cache = self._get_cache(cache_type)
        cache_key = self._make_cache_key(prompt, temperature)
        if cache_key in cache:
            cache.pop(cache_key, None)

        raise GrokAPIError(
            f"API returned invalid response: {error_msg}. Response: {response[:200]}"
        )

    def _handle_truncation_error(
        self,
        response: str,
        error_msg: str,
        cache_type: str,
        prompt: str,
        temperature: float,
    ) -> None:
        """Handle truncated response errors with auto-clear."""
        response_len = len(response)
        if response_len > 100000:
            logger.error(
                "Response truncated at %d chars — likely hit max_tokens limit (manual split needed)",
                response_len,
            )
        else:
            logger.error(
                "Response truncated at %d chars — transient API error, cache cleared, will retry",
                response_len,
            )

            # Auto-clear corrupted cache entry
            cache = self._get_cache(cache_type)
            cache_key = self._make_cache_key(prompt, temperature)
            if cache_key in cache:
                cache.pop(cache_key, None)

        logger.debug("JSON error: %s", error_msg)
        logger.debug("Last 200 chars: ...%s", response[-200:])
        raise GrokAPIError(
            f"API response truncated at {response_len} chars. "
            f"{'Likely transient API error - retry may succeed.' if response_len < 100000 else 'Consider splitting chapter.'}"
        )

    def _try_repair_json(
        self, response: str, error_msg: str
    ) -> Optional[Dict[str, Any]]:
        """Attempt to repair malformed JSON response."""
        import re

        if "Invalid" not in error_msg or "escape" not in error_msg:
            return None

        logger.debug("Attempting to fix invalid escape sequences...")

        # Fix 1: Double-escape invalid backslashes
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", response)
        try:
            result = json.loads(repaired)
            logger.info("✓ JSON repaired (double-escaped invalid backslashes)")
            return result
        except json.JSONDecodeError:
            pass

        # Fix 2: Remove invalid backslashes entirely
        try:
            ultra_clean = re.sub(r'\\([^"\\/bfnrtu])', r"\1", response)
            result = json.loads(ultra_clean)
            logger.info("✓ JSON repaired (removed invalid backslashes)")
            return result
        except json.JSONDecodeError:
            pass

        # Fix 3: Remove escaped brackets
        try:
            cleaned = response.replace(r"\[", "[").replace(r"\]", "]")
            result = json.loads(cleaned)
            logger.info("✓ JSON repaired (removed escaped brackets)")
            return result
        except json.JSONDecodeError:
            pass

        # Fix 4: Nuclear - remove ALL backslashes except valid JSON escapes
        try:
            nuclear = re.sub(r'\\(?!["\\/bfnrtu])', "", response)
            result = json.loads(nuclear)
            logger.info("✓ JSON repaired (stripped all invalid backslashes)")
            return result
        except json.JSONDecodeError:
            pass

        return None

    def extract_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
        _retried: bool = False,
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

        # Auto-retry short responses, but accept valid short JSON ([], {}, "null")
        if not _retried and len(response) < 500:
            try:
                json.loads(response)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "Short response (%d chars), clearing cache and retrying",
                    len(response),
                )
                cache = self._get_cache(cache_type)
                cache_key = self._make_cache_key(prompt, temperature)
                cache.pop(cache_key, None)
                return self.extract_json(
                    prompt, system_prompt, temperature, False, cache_type, _retried=True
                )

        # Clean and sanitize response
        response = self._strip_markdown_wrapper(response)
        response = self._sanitize_json_response(response)

        # Try to parse
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            error_msg = str(e)

            # Handle short responses (auto-clear cache)
            if len(response) < 500:
                self._handle_short_response_error(
                    response, error_msg, cache_type, prompt, temperature
                )

            # Handle truncated responses (auto-clear cache)
            if "Unterminated string" in error_msg or "Expecting" in error_msg:
                self._handle_truncation_error(
                    response, error_msg, cache_type, prompt, temperature
                )

            # Try to repair JSON
            repaired = self._try_repair_json(response, error_msg)
            if repaired is not None:
                return repaired

            # All repair attempts failed — auto-clear so retry gets fresh response
            self.clear_cache_entry(prompt, cache_type, temperature)
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
