#!/usr/bin/env python3
"""Analyze profile stats to identify bottlenecks."""

import pstats
from pathlib import Path

stats_file = Path(__file__).parent / "logs" / "profile_stats.prof"
ps = pstats.Stats(str(stats_file))

print("=" * 80)
print("BOTTLENECK ANALYSIS - Top Functions by Cumulative Time")
print("=" * 80)
print("\nTop 40 functions:\n")
ps.sort_stats("cumulative").print_stats(40)

print("\n" + "=" * 80)
print("BOTTLENECK ANALYSIS - Top Functions by Total Time (self)")
print("=" * 80)
print("\nTop 30 functions:\n")
ps.sort_stats("tottime").print_stats(30)

print("\n" + "=" * 80)
print("BOTTLENECK ANALYSIS - Grok API Calls")
print("=" * 80)
ps.print_stats("grok_client")

print("\n" + "=" * 80)
print("BOTTLENECK ANALYSIS - Extraction Functions")
print("=" * 80)
ps.print_stats("extraction")

print("\n" + "=" * 80)
print("BOTTLENECK ANALYSIS - JSON Operations")
print("=" * 80)
ps.print_stats("json")
