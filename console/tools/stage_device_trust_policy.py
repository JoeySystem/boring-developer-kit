from __future__ import annotations

import argparse
from pathlib import Path

from controller_config.packaging import stage_production_trust_policy


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate BORING production roots and stage the fixed package policy"
    )
    parser.add_argument("roots", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    stage_production_trust_policy(args.roots, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
