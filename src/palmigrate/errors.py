"""Exception types for palmigrate."""

from __future__ import annotations


class PalMigrateError(Exception):
    """Base class for every error this package raises."""


class ContainerError(PalMigrateError):
    """A .sav container could not be decoded or encoded."""


class UnsupportedCompressionError(ContainerError):
    """The container uses a compression scheme we cannot handle."""


class OodleUnavailableError(UnsupportedCompressionError):
    """A PlM (Oodle) container was found but the `ooz` binding is missing."""

    def __init__(self) -> None:
        super().__init__(
            "This save uses Oodle compression (PlM). Decoding it requires the "
            "'ooz' binding.\n"
            "    pip install pyooz\n"
            "Wheels exist for Windows, Linux (glibc and musl) and macOS on both "
            "x86-64 and ARM64, so no compiler should be needed."
        )


class GvasError(PalMigrateError):
    """The decompressed payload is not valid GVAS, or the walk desynchronised."""
