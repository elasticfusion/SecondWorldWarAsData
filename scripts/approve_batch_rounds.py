#!/usr/bin/env python3
"""Approve extra batch rounds for a run that stopped at its round cap (soft cap, owner 2026-11-20).

    AWS_PROFILE=wwii CACHE_TABLE=dev-wwii-api-cache \\
        python3 scripts/approve_batch_rounds.py --phase phase3 --scope USA-E-XChannel --extra 4

The phase and scope are in the "batch rounds over cap" alert. The approval lasts 7 days and applies
to the next run of that phase and scope (see ``src/utils/round_approval``).
"""

import argparse
import getpass
import logging
import sys

from src.utils import round_approval


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--phase", required=True, choices=["phase2", "phase3"])
    ap.add_argument("--scope", required=True, help="the book(s) named in the alert")
    ap.add_argument("--extra", required=True, type=int, help="rounds to add (1-100)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    extra = round_approval.approve(
        args.phase, args.scope, args.extra, who=getpass.getuser()
    )
    print(
        f"Approved {extra} extra round(s) for {args.phase} / {args.scope} "
        f"(valid {round_approval.APPROVAL_TTL_S // 86400} days). Re-run the phase."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
