from __future__ import annotations

from dataclasses import dataclass

from .flp import FLPUnsupportedError


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    name: str
    versions: tuple[str, ...]
    description: str


# Mutation stays fail-closed at the exact project-format builds exercised by
# the Mac and Windows host matrices. A future point release must be validated
# before it can rewrite a user's project.
PROFILES = (
    CompatibilityProfile(
        "fl25",
        ("25.2.5.5055", "25.2.5.5319"),
        "FL Studio 25 mutation layout on macOS and Windows",
    ),
    CompatibilityProfile(
        "fl26",
        ("26.1.0.5294", "26.1.0.5530"),
        "FL Studio 26 mutation layout on macOS and Windows",
    ),
)


def require_mutation_profile(version: str) -> CompatibilityProfile:
    for profile in PROFILES:
        if version in profile.versions:
            return profile
    displayed = version or "unknown"
    raise FLPUnsupportedError(
        f"FL Studio {displayed} has no enabled mutation profile; the capsule library remains available read-only"
    )
