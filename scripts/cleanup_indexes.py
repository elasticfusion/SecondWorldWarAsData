#!/usr/bin/env python3
"""CLI wrapper. The implementation lives in src/dedup/cleanup_indexes.py
(docs/current/design/SRC_SCRIPTS_SEPARATION.md, PR 1). Import from src.dedup, not from here.

    python3 scripts/cleanup_indexes.py      # same as: python3 -m src.dedup.cleanup_indexes
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dedup.cleanup_indexes import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
