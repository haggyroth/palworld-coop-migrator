"""
palmigrate -- migrate a Palworld co-op world onto a dedicated server.

Handles the modern ``PlM`` (Oodle) save container that the older community
tools cannot read, and refuses to perform unsafe byte-level GUID rewrites.
"""

from __future__ import annotations

from .container import (
    MAGIC_PLM,
    MAGIC_PLZ,
    TYPE_DOUBLE,
    TYPE_SINGLE,
    SavContainer,
    decode,
    encode,
    read,
    write,
)
from .errors import (
    ContainerError,
    GvasError,
    OodleUnavailableError,
    PalMigrateError,
    UnsupportedCompressionError,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "SavContainer",
    "decode",
    "encode",
    "read",
    "write",
    "MAGIC_PLZ",
    "MAGIC_PLM",
    "TYPE_SINGLE",
    "TYPE_DOUBLE",
    "PalMigrateError",
    "ContainerError",
    "UnsupportedCompressionError",
    "OodleUnavailableError",
    "GvasError",
]
