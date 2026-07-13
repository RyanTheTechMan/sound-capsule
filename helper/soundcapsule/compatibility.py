from __future__ import annotations

from dataclasses import dataclass

from .flp import FLPUnsupportedError


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    version: str
    description: str


# Mutation is deliberately allowlisted. Adding a profile requires round-trip
# fixtures and the logical append/override acceptance suite for that FL line.
PROFILES = (
    CompatibilityProfile(
        "25.2.5.5055",
        "FL Studio 25.2.5 project layout; host-tested with FL Studio 26.1.0.5294 on macOS",
    ),
    CompatibilityProfile(
        "26.1.0.5294",
        "FL Studio 26.1.0 macOS event layout",
    ),
    CompatibilityProfile(
        "26.1.0.5530",
        "FL Studio 26.1.0 Windows event layout",
    ),
)


def require_mutation_profile(version: str) -> CompatibilityProfile:
    for profile in PROFILES:
        if version == profile.version:
            return profile
    displayed = version or "unknown"
    raise FLPUnsupportedError(
        f"FL Studio {displayed} has no enabled mutation profile; the capsule library remains available read-only"
    )
