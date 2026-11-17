"""Tests for src/utils/chunked_extract.py (recursive halving)."""

# pylint: disable=missing-function-docstring

import pytest

from src.grok_client import GrokTruncationError, BatchModeCollecting
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
        assert len(result) == 4 and "a" in result and "d" in result

    def test_recursive_halving_recovers_all_items(self):
        """THE regression: a chunk that truncates at every size > 1 must keep halving down to
        size-1 and recover ALL items — the old (non-recursive) code dropped a whole half.
        """

        def extract_fn(chunk):
            if len(chunk) > 1:
                raise GrokTruncationError("still truncated")
            return {chunk[0]: chunk[0].upper()}

        chunks = [["a", "b", "c", "d", "e", "f", "g", "h"]]
        result = extract_with_chunk_halving(chunks, extract_fn, "test")
        assert result == {c: c.upper() for c in "abcdefgh"}  # ZERO items lost

    def test_single_item_that_always_truncates_drops_only_itself(self):
        """A single item that still truncates is dropped ALONE (not raised, not a half) so the rest
        of the batch survives."""

        def extract_fn(chunk):
            if "huge" in chunk:  # the one oversized item always truncates, even alone
                raise GrokTruncationError("too long even at size 1")
            return {item: item.upper() for item in chunk}

        chunks = [["a", "huge", "b", "c"]]
        result = extract_with_chunk_halving(chunks, extract_fn, "test")
        assert result == {"a": "A", "b": "B", "c": "C"}  # only 'huge' dropped
        assert "huge" not in result

    def test_batchmodecollecting_propagates_not_swallowed(self):
        """The batch-COLLECT signal must NEVER be swallowed (optional-entity-zero bug), even nested
        inside the recursive halving."""

        def extract_fn(chunk):
            if len(chunk) > 1:
                raise GrokTruncationError("truncate to force a split")
            raise BatchModeCollecting("queued")

        with pytest.raises(BatchModeCollecting):
            extract_with_chunk_halving([["a", "b", "c", "d"]], extract_fn, "test")

    def test_non_truncation_half_error_skips_gracefully(self):
        """A NON-truncation error on a sub-chunk is skipped (not propagated, not recursed) so one
        bad chunk can't abort the batch — but BatchModeCollecting still propagates (above).
        """
        call_count = {"n": 0}

        def extract_fn(chunk):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise GrokTruncationError("truncated")  # force the split
            if call_count["n"] == 2:
                raise RuntimeError("first half fails hard")
            return {item: item.upper() for item in chunk}

        result = extract_with_chunk_halving([["a", "b", "c", "d"]], extract_fn, "test")
        assert "c" in result or "d" in result  # second half recovered; no raise

    def test_empty_chunks(self):
        assert extract_with_chunk_halving([], lambda c: {}, "test") == {}

    def test_every_chunk_is_asked_in_one_batch_round(self):
        """A queued chunk (BatchModeCollecting) must not stop the others: all are queued in the same
        round, then the signal propagates. Stopping at the first queued one asked chapter 8's
        weather one chunk per round, for six rounds (2026-11-13)."""
        asked = []

        def extract_fn(chunk):
            asked.append(chunk[0])
            raise BatchModeCollecting("queued")

        with pytest.raises(BatchModeCollecting):
            extract_with_chunk_halving([["a"], ["b"], ["c"]], extract_fn, "test")
        assert asked == ["a", "b", "c"]

    def test_answered_chunks_are_kept_when_another_is_queued(self):
        out = {}

        def extract_fn(chunk):
            if chunk[0] == "b":
                raise BatchModeCollecting("queued")
            return {chunk[0]: 1}

        from src.utils.chunked_extract import ask_all, _extract_one_chunk_recursive

        with pytest.raises(BatchModeCollecting):
            ask_all(
                [["a"], ["b"], ["c"]],
                lambda c: _extract_one_chunk_recursive(c, extract_fn, "t", out),
            )
        assert out == {"a": 1, "c": 1}

    def test_both_halves_of_a_split_are_asked_in_one_round(self):
        asked = []

        def extract_fn(chunk):
            if len(chunk) > 1:
                raise GrokTruncationError("split")
            asked.append(chunk[0])
            raise BatchModeCollecting("queued")

        with pytest.raises(BatchModeCollecting):
            extract_with_chunk_halving([["a", "b"]], extract_fn, "test")
        assert asked == ["a", "b"]

    def test_merges_across_chunks(self):
        chunks = [["x"], ["y"], ["z"]]
        result = extract_with_chunk_halving(chunks, lambda c: {c[0]: len(c)}, "test")
        assert result == {"x": 1, "y": 1, "z": 1}
