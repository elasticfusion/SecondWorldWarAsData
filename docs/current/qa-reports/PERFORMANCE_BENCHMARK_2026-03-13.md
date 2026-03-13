# Performance Benchmark Report

**Date:** 2026-03-13 12:36:30  
**Python Version:** 3.14.3  
**Platform:** darwin

---

## Executive Summary

| Metric | Value | Status |
|--------|-------|--------|
| Chapters Parsed | 0 | ✅ |
| Total Entities Extracted | 2238 | ✅ |
| Cache Size | 0.1 MB | ✅ |
| Memory Usage | 21.12 MB | ✅ |

---

## Phase 1: Parsing Performance

| Metric | Value |
|--------|-------|
| Chapters Processed | 0 |
| Total Size | 0 KB |
| Average Size/Chapter | 0 KB |
| Total Paragraphs | 0 |
| Average Paragraphs/Chapter | 0 |

---

## Phase 2: Extraction Performance

### Entity Counts

| Entity Type | Files | Entities |
|-------------|-------|----------|
| Events | 0 | 0 |
| Dates | 375 | 375 |
| Places | 1063 | 1063 |
| People | 361 | 361 |
| People Groups | 439 | 439 |
| **Total** | **2238** | **2238** |

---

## Cache Performance

### Cache Statistics

| Cache Type | Files | Size (MB) |
|------------|-------|-----------|
| supplemental | 1 | 0.07 |
| supplemental_narrative | 1 | 0.03 |
| **Total** | **2** | **0.1** |

**Cache Efficiency:**
- Total cached responses: 2
- Storage used: 0.1 MB
- Estimated API calls saved: 2

---

## Memory Usage

| Metric | Value |
|--------|-------|
| RSS (Resident Set Size) | 21.12 MB |
| VMS (Virtual Memory Size) | 401629.92 MB |

---

## Recommendations

### Performance
- ✅ Cache is working effectively (2 cached responses)
- ✅ Memory usage is within acceptable limits
- ℹ️ Consider batch processing for large datasets

### Optimization Opportunities
- Enable parallel processing for Phase 2 (3-5x speedup)
- Implement progressive caching strategy
- Monitor API response times for bottlenecks

---

**Report Generated:** 2026-03-13 12:36:30  
**Tool:** scripts/benchmark_performance.py
