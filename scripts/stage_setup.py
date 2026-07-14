#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from package_release import copy_setup_payload, frozen_helper_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage Sound Capsule native setup payload")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--platform", choices=("macos", "windows"), required=True)
    parser.add_argument("--build", type=Path, default=Path("build"))
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    helper = frozen_helper_bundle(args.build.resolve(), args.platform)
    copy_setup_payload(destination, args.platform, helper)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
