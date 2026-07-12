#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HEADING_PATTERN = re.compile(r"^## \[([^]]+)](?:\s+-\s+.*)?$", re.MULTILINE)


def notes_for_version(changelog: str, version: str) -> str:
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("version must use semantic versioning, for example 1.2.3")
    headings = list(HEADING_PATTERN.finditer(changelog))
    for index, heading in enumerate(headings):
        if heading.group(1) != version:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        notes = changelog[start:end].strip()
        if not notes:
            raise ValueError(f"CHANGELOG.md section {version} is empty")
        return notes + "\n"
    raise ValueError(f"CHANGELOG.md has no section for {version}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one version's GitHub release notes")
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path, default=Path("release-notes.md"))
    args = parser.parse_args()
    args.output.write_text(
        notes_for_version(args.changelog.read_text(encoding="utf-8"), args.version),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
