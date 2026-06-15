"""Tests for src/utils/chunked_extract.py."""

# pylint: disable=missing-function-docstring

from src.grok_client import GrokTruncationError
from src.utils.chunked_extract import extract_with_chunk_halving


class TestExtractWithChunkHalving:
    def test_normal_extraction(self):
        chunks = [["a", "b"], ["c", "d"]]
        extract_fn = lambda chunk: {item: item.upper() for item in chunk}
        result = extract_with_chunk_halving(chunks, extract_fn, "test")
        assert result == {"a": "A", "b": "B", "c": "C", "d": "D"}

    def test_truncation_halves_and_retries(self):
        call_count = {"n": 0}

        def extract_fn(chunk):
            call_count["n"] += 1
            if call_count["n"] == 1 and len(chunk) > 2:
                raise GrokTruncationError("truncated")
            return {item: item.upper() for item in chunk}

        chunks = [["a", "b", "c", "d"]]
        result = extract_with_chunk_halving(chunks, extract_fn, "test")
        assert "a" in result
        assert "d" in result
        assert len(result) == 4

    def test_single_item_chunk_raises_on_truncation(self):
        def extract_fn(chunk):
            raise GrokTruncationError("too long")

        chunks = [["single"]]
        try:
            extract_with_chunk_halving(chunks, extract_fn, "test")
            assert False, "Should have raised"
        except GrokTruncationError:
            pass

    def test_half_chunk_failure_skips_gracefully(self):
        call_count = {"n": 0}

        def extract_fn(chunk):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise GrokTruncationError("truncated")
            if call_count["n"] == 2:
                raise RuntimeError("half also fails")
            return {item: item.upper() for item in chunk}

        chunks = [["a", "b", "c", "d"]]
        result = extract_with_chunk_halving(chunks, extract_fn, "test")
        # First half fails, second half succeeds
        assert "c" in result or "d" in result

    def test_empty_chunks(self):
        result = extract_with_chunk_halving([], lambda c: {}, "test")
        assert result == {}

    def test_merges_across_chunks(self):
        chunks = [["x"], ["y"], ["z"]]
        result = extract_with_chunk_halving(chunks, lambda c: {c[0]: len(c)}, "test")
        assert result == {"x": 1, "y": 1, "z": 1}
