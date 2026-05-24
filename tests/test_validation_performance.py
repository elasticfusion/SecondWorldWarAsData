"""Performance tests for validation features."""

import json
import tempfile
import time
from pathlib import Path

import pytest
import ulid

from src.json_schemas import PEOPLE_SCHEMA
from src.utils.json_validator import (
    register_post_validation_hook,
    register_pre_validation_hook,
    validate_directory,
    validate_json,
)
from src.utils.schema_registry import get_registry


class TestBatchValidationPerformance:
    """Performance tests for batch validation."""

    def test_batch_vs_individual_10_files(self, tmp_path):
        """Compare batch vs individual validation for 10 files."""
        # Create test files
        files = []
        for i in range(10):
            data = {
                "people": [
                    {
                        "PersonID": str(ulid.new()),
                        "name": f"Person {i}",
                        "events": [],
                    }
                ]
            }
            filepath = tmp_path / f"person_{i}.json"
            filepath.write_text(json.dumps(data))
            files.append(filepath)

        # Test individual validation
        start = time.perf_counter()
        for filepath in files:
            with open(filepath) as f:
                data = json.load(f)
            validate_json(data, PEOPLE_SCHEMA)
        individual_time = time.perf_counter() - start

        # Test batch validation
        start = time.perf_counter()
        validate_directory(tmp_path, PEOPLE_SCHEMA)
        batch_time = time.perf_counter() - start

        speedup = individual_time / batch_time
        print(f"\n  Individual: {individual_time*1000:.2f}ms")
        print(f"  Batch: {batch_time*1000:.2f}ms")
        print(f"  Speedup: {speedup:.2f}x")

        # Batch should not be significantly slower (allow 50% margin for timing noise)
        assert batch_time < individual_time * 1.5

    def test_batch_throughput_100_files(self, tmp_path):
        """Measure throughput for 100 files."""
        # Create 100 test files
        for i in range(100):
            data = {
                "people": [
                    {
                        "PersonID": str(ulid.new()),
                        "name": f"Person {i}",
                        "events": [],
                    }
                ]
            }
            filepath = tmp_path / f"person_{i}.json"
            filepath.write_text(json.dumps(data))

        # Measure batch validation
        start = time.perf_counter()
        results = validate_directory(tmp_path, PEOPLE_SCHEMA)
        elapsed = time.perf_counter() - start

        throughput = results["total"] / elapsed
        print(f"\n  Files: {results['total']}")
        print(f"  Time: {elapsed*1000:.2f}ms")
        print(f"  Throughput: {throughput:.0f} files/second")

        # Should process at least 100 files/second
        assert throughput > 100
        assert results["valid"] == 100

    def test_batch_throughput_1000_files(self, tmp_path):
        """Measure throughput for 1000 files."""
        # Create 1000 test files
        for i in range(1000):
            data = {
                "people": [
                    {
                        "PersonID": str(ulid.new()),
                        "name": f"Person {i}",
                        "events": [],
                    }
                ]
            }
            filepath = tmp_path / f"person_{i:04d}.json"
            filepath.write_text(json.dumps(data))

        # Measure batch validation
        start = time.perf_counter()
        results = validate_directory(tmp_path, PEOPLE_SCHEMA)
        elapsed = time.perf_counter() - start

        throughput = results["total"] / elapsed
        print(f"\n  Files: {results['total']}")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Throughput: {throughput:.0f} files/second")

        # Should process at least 500 files/second
        assert throughput > 500
        assert results["valid"] == 1000

    def test_batch_with_errors(self, tmp_path):
        """Measure performance with invalid files."""
        # Create mix of valid and invalid files
        for i in range(100):
            if i % 10 == 0:
                # Invalid: missing required field
                data = {"people": [{"PersonID": str(ulid.new())}]}
            else:
                # Valid
                data = {
                    "people": [
                        {
                            "PersonID": str(ulid.new()),
                            "name": f"Person {i}",
                            "events": [],
                        }
                    ]
                }
            filepath = tmp_path / f"person_{i}.json"
            filepath.write_text(json.dumps(data))

        start = time.perf_counter()
        results = validate_directory(tmp_path, PEOPLE_SCHEMA)
        elapsed = time.perf_counter() - start

        throughput = results["total"] / elapsed
        print(f"\n  Files: {results['total']}")
        print(f"  Valid: {results['valid']}")
        print(f"  Invalid: {results['invalid']}")
        print(f"  Time: {elapsed*1000:.2f}ms")
        print(f"  Throughput: {throughput:.0f} files/second")

        assert results["total"] == 100
        assert results["invalid"] == 10


class TestHookPerformance:
    """Performance tests for validation hooks."""

    def test_hook_overhead_single_validation(self):
        """Measure overhead of hooks on single validation."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }

        # Baseline: validation without hooks
        start = time.perf_counter()
        for _ in range(100):
            validate_json(data, PEOPLE_SCHEMA)
        baseline_time = time.perf_counter() - start

        # With hooks
        def dummy_pre_hook(data):
            pass

        def dummy_post_hook(data, is_valid):
            pass

        register_pre_validation_hook(dummy_pre_hook)
        register_post_validation_hook(dummy_post_hook)

        start = time.perf_counter()
        for _ in range(100):
            validate_json(data, PEOPLE_SCHEMA)
        with_hooks_time = time.perf_counter() - start

        overhead = (with_hooks_time - baseline_time) / 100 * 1000  # ms per validation
        print(f"\n  Baseline: {baseline_time*1000:.2f}ms (100 validations)")
        print(f"  With hooks: {with_hooks_time*1000:.2f}ms (100 validations)")
        print(f"  Overhead per validation: {overhead:.3f}ms")

        # Overhead should be less than 1ms per validation
        assert overhead < 1.0

    def test_hook_overhead_batch_validation(self, tmp_path):
        """Measure hook overhead on batch validation."""
        # Create 100 test files
        for i in range(100):
            data = {
                "people": [
                    {
                        "PersonID": str(ulid.new()),
                        "name": f"Person {i}",
                        "events": [],
                    }
                ]
            }
            filepath = tmp_path / f"person_{i}.json"
            filepath.write_text(json.dumps(data))

        # Baseline: batch validation without hooks
        start = time.perf_counter()
        validate_directory(tmp_path, PEOPLE_SCHEMA)
        baseline_time = time.perf_counter() - start

        # With hooks
        hook_calls = []

        def counting_pre_hook(data):
            hook_calls.append("pre")

        def counting_post_hook(data, is_valid):
            hook_calls.append("post")

        register_pre_validation_hook(counting_pre_hook)
        register_post_validation_hook(counting_post_hook)

        hook_calls.clear()
        start = time.perf_counter()
        results = validate_directory(tmp_path, PEOPLE_SCHEMA)
        with_hooks_time = time.perf_counter() - start

        overhead_total = (with_hooks_time - baseline_time) * 1000  # ms
        overhead_per_file = overhead_total / results["total"]

        print(f"\n  Files: {results['total']}")
        print(f"  Baseline: {baseline_time*1000:.2f}ms")
        print(f"  With hooks: {with_hooks_time*1000:.2f}ms")
        print(f"  Total overhead: {overhead_total:.2f}ms")
        print(f"  Overhead per file: {overhead_per_file:.3f}ms")
        print(f"  Hook calls: {len(hook_calls)}")

        # Overhead should be reasonable
        assert overhead_per_file < 1.0
        # Hooks may not be called in batch validation (implementation detail)

    def test_multiple_hooks_overhead(self):
        """Measure overhead with multiple hooks."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }

        # Register multiple hooks
        for i in range(5):

            def pre_hook(data, i=i):
                pass

            def post_hook(data, is_valid, i=i):
                pass

            register_pre_validation_hook(pre_hook)
            register_post_validation_hook(post_hook)

        start = time.perf_counter()
        for _ in range(100):
            validate_json(data, PEOPLE_SCHEMA)
        elapsed = time.perf_counter() - start

        time_per_validation = elapsed / 100 * 1000  # ms
        print(f"\n  Hooks: 10 (5 pre + 5 post)")
        print(f"  Validations: 100")
        print(f"  Total time: {elapsed*1000:.2f}ms")
        print(f"  Time per validation: {time_per_validation:.3f}ms")

        # Even with multiple hooks, should be fast
        assert time_per_validation < 5.0


class TestSchemaRegistryPerformance:
    """Performance tests for schema registry."""

    def test_first_load_vs_cached(self):
        """Compare first load vs cached validator access."""
        registry = get_registry()

        # First load (includes compilation)
        start = time.perf_counter()
        validator1 = registry.get_validator("people")
        first_load_time = time.perf_counter() - start

        # Cached access
        start = time.perf_counter()
        for _ in range(1000):
            validator2 = registry.get_validator("people")
        cached_time = time.perf_counter() - start
        cached_per_access = cached_time / 1000 * 1000000  # microseconds

        print(f"\n  First load: {first_load_time*1000:.3f}ms")
        print(f"  Cached (1000 accesses): {cached_time*1000:.3f}ms")
        print(f"  Cached per access: {cached_per_access:.3f}µs")

        # Cached access should be very fast
        assert cached_per_access < 100  # Less than 100 microseconds

        # Should return same object (cached)
        assert validator1 is validator2

    def test_registry_lookup_performance(self):
        """Measure registry lookup performance."""
        registry = get_registry()

        # Warm up cache
        for schema_name in registry.list_schemas():
            registry.get_validator(schema_name)

        # Measure lookup performance
        start = time.perf_counter()
        for _ in range(10000):
            registry.get_validator("people")
        elapsed = time.perf_counter() - start

        time_per_lookup = elapsed / 10000 * 1000000  # microseconds
        print(f"\n  Lookups: 10,000")
        print(f"  Total time: {elapsed*1000:.3f}ms")
        print(f"  Time per lookup: {time_per_lookup:.3f}µs")

        # Should be very fast (O(1))
        assert time_per_lookup < 10  # Less than 10 microseconds


class TestValidationPerformanceSummary:
    """Summary performance test."""

    def test_performance_summary(self, tmp_path):
        """Generate performance summary report."""
        print("\n" + "=" * 60)
        print("VALIDATION PERFORMANCE SUMMARY")
        print("=" * 60)

        # 1. Batch validation throughput
        for i in range(100):
            data = {
                "people": [
                    {
                        "PersonID": str(ulid.new()),
                        "name": f"Person {i}",
                        "events": [],
                    }
                ]
            }
            (tmp_path / f"person_{i}.json").write_text(json.dumps(data))

        start = time.perf_counter()
        results = validate_directory(tmp_path, PEOPLE_SCHEMA)
        elapsed = time.perf_counter() - start
        throughput = results["total"] / elapsed

        print(f"\n1. Batch Validation (100 files)")
        print(f"   Time: {elapsed*1000:.2f}ms")
        print(f"   Throughput: {throughput:.0f} files/second")

        # 2. Hook overhead
        data = {"people": [{"PersonID": str(ulid.new()), "name": "Test", "events": []}]}

        start = time.perf_counter()
        for _ in range(100):
            validate_json(data, PEOPLE_SCHEMA)
        baseline = time.perf_counter() - start

        def hook(data):
            pass

        register_pre_validation_hook(hook)

        start = time.perf_counter()
        for _ in range(100):
            validate_json(data, PEOPLE_SCHEMA)
        with_hook = time.perf_counter() - start

        overhead = (with_hook - baseline) / 100 * 1000

        print(f"\n2. Hook Overhead (per validation)")
        print(f"   Overhead: {overhead:.3f}ms")

        # 3. Schema registry
        registry = get_registry()
        start = time.perf_counter()
        for _ in range(1000):
            registry.get_validator("people")
        elapsed = time.perf_counter() - start
        per_lookup = elapsed / 1000 * 1000000

        print(f"\n3. Schema Registry (cached lookup)")
        print(f"   Time per lookup: {per_lookup:.3f}µs")

        print("\n" + "=" * 60)

        # Verify performance targets
        assert throughput > 500  # At least 500 files/second
        assert overhead < 1.0  # Less than 1ms overhead
        assert per_lookup < 10  # Less than 10µs per lookup
