# Archived Source Files

This directory contains deprecated or superseded source files that are no longer actively used in the pipeline.

## Files

### weather.py
- **Archived:** 2026-03-08
- **Reason:** Superseded by `weather_central.py`
- **Description:** Original single-file weather extraction system. Replaced by centralized weather repository system that deduplicates weather data across books.
- **Replacement:** Use `src/extraction/weather_central.py` instead
- **Last Modified:** 2026-02-21

## Why Archive Instead of Delete?

These files are archived rather than deleted to:
1. Preserve historical implementation approaches
2. Allow reference for future development
3. Enable rollback if needed
4. Document evolution of the codebase

## Restoration

If you need to restore an archived file:
```bash
mv src/archive/<filename> src/extraction/<filename>
```

Note: Restored files may require updates to work with current codebase.
