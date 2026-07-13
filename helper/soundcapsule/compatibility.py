from __future__ import annotations

from dataclasses import dataclass

from .flp import FLPUnsupportedError


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    name: str
    minimum_version: str
    maximum_version_exclusive: str
    description: str


# Rules are ordered so a narrower range can override a broad major-version rule.
# Each major still fails closed before the earliest verified build and at the
# next major version.
PROFILES = (
    CompatibilityProfile(
        "fl25",
        "25.2.5.5055",
        "26.0.0.0",
        "FL Studio 25 mutation layout",
    ),
    CompatibilityProfile(
        "fl26",
        "26.1.0.5294",
        "27.0.0.0",
        "FL Studio 26 mutation layout",
    ),
)


def _version_key(version: str) -> tuple[int, int, int, int] | None:
    try:
        parts = [int(part) for part in version.split(".")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 4 or any(part < 0 for part in parts):
        return None
    return tuple((parts + [0] * 4)[:4])


def require_mutation_profile(version: str) -> CompatibilityProfile:
    version_key = _version_key(version)
    for profile in PROFILES:
        if version_key is not None and (
            _version_key(profile.minimum_version)
            <= version_key
            < _version_key(profile.maximum_version_exclusive)
        ):
            return profile
    displayed = version or "unknown"
    raise FLPUnsupportedError(
        f"FL Studio {displayed} has no enabled mutation profile; the capsule library remains available read-only"
    )
