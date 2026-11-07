#!/usr/bin/env python3
"""CLI wrapper. The implementation lives in src/dedup/find_related_groups.py
(docs/current/design/SRC_SCRIPTS_SEPARATION.md, PR 1). Import from src.dedup, not from here.

    python3 scripts/find_related_groups.py      # same as: python3 -m src.dedup.find_related_groups
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dedup.find_related_groups import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
