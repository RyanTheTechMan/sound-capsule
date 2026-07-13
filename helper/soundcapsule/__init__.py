"""FL Studio Sound Capsule helper."""

from .capsule import Capsule, CapsuleManifest
from .flp import FLPFile, FLPFormatError, FLPUnsupportedError

__all__ = [
    "Capsule",
    "CapsuleManifest",
    "FLPFile",
    "FLPFormatError",
    "FLPUnsupportedError",
]

__version__ = "0.3.0"
