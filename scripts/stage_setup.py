#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from package_release import copy_setup_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage Sound Capsule native setup payload")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--platform", choices=("macos", "windows"), required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    copy_setup_payload(destination, args.platform)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
