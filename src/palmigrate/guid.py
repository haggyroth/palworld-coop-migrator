"""
Palworld player identifiers.

A PlayerUId is a UE ``FGuid``: four little-endian ``uint32`` fields. Its text
form -- and the name Palworld gives the file in ``Players/`` -- is those four
fields printed as 8 hex digits each, concatenated into 32 characters.

The co-op host is always ``00000000000000000000000000000001``. That is
*structurally awkward*: in memory it is twelve zero bytes followed by
``01 00 00 00``, which is indistinguishable from ordinary zero padding
followed by an ``int32`` of 1. See :mod:`palmigrate.scan` for why that rules
out byte-level search-and-replace.
"""

from __future__ import annotations

import re
import struct
from typing import Final

#: The PlayerUId every Palworld co-op host is assigned.
COOP_HOST_GUID: Final = "00000000000000000000000000000001"

_HEX32 = re.compile(r"\A[0-9a-fA-F]{32}\Z")


def is_valid(text: str) -> bool:
    """True if ``text`` is 32 hex characters."""
    return bool(_HEX32.match(text))


def normalise(text: str) -> str:
    """Strip dashes/whitespace and lowercase a GUID string."""
    cleaned = text.strip().replace("-", "").replace(" ", "")
    if not is_valid(cleaned):
        raise ValueError(
            f"{text!r} is not a Palworld PlayerUId. Expected 32 hex characters, "
            f"e.g. {COOP_HOST_GUID}"
        )
    return cleaned.lower()


def to_bytes(text: str) -> bytes:
    """Convert the 32-char text form to its 16 little-endian bytes."""
    cleaned = normalise(text)
    fields = [int(cleaned[i : i + 8], 16) for i in range(0, 32, 8)]
    return struct.pack("<IIII", *fields)


def from_bytes(raw: bytes) -> str:
    """Convert 16 little-endian bytes back to the 32-char text form."""
    if len(raw) != 16:
        raise ValueError(f"a GUID is 16 bytes, got {len(raw)}")
    fields = struct.unpack("<IIII", raw)
    return "".join(f"{f:08x}" for f in fields)


def is_coop_host(text: str) -> bool:
    """True if this is the hardcoded co-op host id."""
    return normalise(text) == COOP_HOST_GUID


def entropy_warning(text: str) -> str | None:
    """
    Return a warning if this GUID's byte pattern is likely to collide with
    ordinary save data, or ``None`` if it is high-entropy and safe to match on.
    """
    raw = to_bytes(text)
    zero_run = len(raw) - len(raw.rstrip(b"\x00")) if raw.rstrip(b"\x00") else len(raw)
    leading_zeros = len(raw) - len(raw.lstrip(b"\x00"))
    if leading_zeros >= 8 or zero_run >= 8:
        return (
            f"GUID {normalise(text)} contains a long run of zero bytes. Its raw "
            "form collides with ordinary padding, so it must never be located "
            "by byte-pattern search alone."
        )
    return None
