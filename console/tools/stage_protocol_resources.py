from __future__ import annotations

import argparse
from pathlib import Path

from controller_config.packaging import stage_protocol_resources


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage the authoritative BORING protocol assets for packaging"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    stage_protocol_resources(args.source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
