#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

try:
    from .package_release import copy_setup_payload
except ImportError:  # Direct script execution places scripts/ on sys.path.
    from package_release import copy_setup_payload


def stage_macos_setup(app: Path, helper_bundle: Path) -> Path:
    """Embed the signed-later helper payload in a relocatable macOS app."""
    app = app.resolve()
    helper_bundle = helper_bundle.resolve()
    if not app.is_dir() or app.suffix.casefold() != ".app":
        raise FileNotFoundError(f"macOS app bundle is missing: {app}")
    destination = app / "Contents" / "Resources" / "Setup"
    if destination.exists():
        shutil.rmtree(destination)
    copy_setup_payload(destination, "macos", helper_bundle)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Embed the self-contained helper in a macOS app before signing"
    )
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--helper", type=Path, required=True)
    args = parser.parse_args()
    print(stage_macos_setup(args.app, args.helper))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
