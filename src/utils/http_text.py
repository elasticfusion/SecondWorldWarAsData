"""Decode an HTTP response body correctly.

``requests`` decodes a ``text/*`` response WITHOUT a charset header as ISO-8859-1 (RFC 2616's
default), so a UTF-8 page's "Dönitz" becomes "DÃ¶nitz" and its en dash mojibake. Grokipedia serves
UTF-8 that way: no accented person or place ever matched (XChannel-8 E2E, 2026-11-07).
"""

from __future__ import annotations


def response_text(resp, default_encoding: str = "utf-8") -> str:
    """The response body as text: the charset the server DECLARED, else ``default_encoding``.
    An unknown declared charset falls back to the default; undecodable bytes are replaced (never
    raises)."""
    headers = getattr(resp, "headers", None) or {}
    content_type = str(headers.get("content-type", "") or "").lower()
    declared = getattr(resp, "encoding", None) if "charset" in content_type else None
    body = getattr(resp, "content", b"") or b""
    for encoding in (declared, default_encoding):
        if not encoding:
            continue
        try:
            return body.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            continue
    return body.decode("utf-8", errors="replace")
