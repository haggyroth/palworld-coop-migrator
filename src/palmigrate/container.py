"""
Palworld ``.sav`` container codec.

A ``.sav`` file is a 12-byte header followed by a compressed UE5 GVAS blob::

    offset  size  field
    0       4     uint32  uncompressed length
    4       4     uint32  compressed length
    8       3     bytes   magic: b"PlZ" (zlib era) or b"PlM" (Oodle era)
    11      1     uint8   save type: 0x31 single stage, 0x32 double stage
    12      ..    bytes   compressed payload

``PlZ`` containers use zlib and are what every pre-existing community tool
expects. Current Palworld builds write ``PlM`` containers compressed with
Oodle Kraken.

There is no open-source Oodle *compressor*. The ``ooz`` bindings decompress
only. That would normally make round-tripping a modern save impossible.

It is not, because of one useful property of the game's loader: **the server
still accepts ``PlZ`` containers and rewrites them as ``PlM`` on its next
save.** So the supported pipeline is:

    read PlM (Oodle)  ->  modify GVAS  ->  write PlZ (zlib)  ->  server upgrades

See ``docs/save-format.md`` for the experiment that established this.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .errors import ContainerError, OodleUnavailableError, UnsupportedCompressionError

MAGIC_PLZ: Final = b"PlZ"
MAGIC_PLM: Final = b"PlM"
TYPE_SINGLE: Final = 0x31
TYPE_DOUBLE: Final = 0x32
HEADER_LEN: Final = 12
GVAS_MAGIC: Final = b"GVAS"

#: Largest payload we will allocate for, as a guard against a corrupt header
#: claiming a multi-gigabyte uncompressed size. Real payloads are well under
#: this; the largest observed in the wild is a ~74 MB Pal-storage file.
MAX_PAYLOAD_BYTES: Final = 512 * 1024 * 1024


@dataclass(frozen=True)
class SavContainer:
    """A decoded ``.sav``: its header fields plus the raw GVAS payload."""

    uncompressed_length: int
    compressed_length: int
    magic: bytes
    save_type: int
    payload: bytes

    @property
    def format_name(self) -> str:
        """e.g. ``"PlM1"`` / ``"PlZ2"``."""
        return f"{self.magic.decode('ascii')}{chr(self.save_type)}"

    @property
    def is_oodle(self) -> bool:
        return self.magic == MAGIC_PLM

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SavContainer({self.format_name}, "
            f"compressed={self.compressed_length:,}, "
            f"uncompressed={self.uncompressed_length:,})"
        )


def _zlib_decompress(data: bytes, limit: int = MAX_PAYLOAD_BYTES) -> bytes:
    """
    Decompress with a hard output cap.

    ``zlib.decompress`` has no output limit, so a small file can expand without
    bound before any size check runs. The header's declared length is no
    defence: it is attacker-controlled and independent of the real expansion --
    a crafted 81 KB file declaring 1,000 bytes expanded to 84 MB, and the
    mismatch was only noticed after the allocation. Cap it during, not after.
    """
    engine = zlib.decompressobj()
    out = engine.decompress(data, limit + 1)
    if len(out) > limit:
        raise ContainerError(
            f"decompressed output exceeded the {limit:,} byte guard; "
            f"refusing to continue (possible decompression bomb)"
        )
    out += engine.flush()
    if len(out) > limit:
        raise ContainerError(f"decompressed output exceeded the {limit:,} byte guard after flush")
    return out


def _oodle_decompress(body: bytes, expected_length: int) -> bytes:
    try:
        import ooz  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OodleUnavailableError() from exc

    # ooz signals failure with a bare RuntimeError. Wrap it so callers only
    # ever have to catch PalMigrateError.
    try:
        out = ooz.decompress(body, expected_length)
    except Exception as exc:  # noqa: BLE001 - third-party raises bare RuntimeError
        raise ContainerError(
            f"Oodle decompression failed ({exc}). The file may be truncated, "
            f"corrupt, or not actually Oodle-compressed."
        ) from exc

    if out is None:
        raise ContainerError(
            "Oodle decompression returned no data. The file may be truncated or corrupt."
        )
    return bytes(out)


def decode(raw: bytes) -> SavContainer:
    """Decode ``.sav`` bytes into a :class:`SavContainer`. Never mutates input."""
    if len(raw) < HEADER_LEN:
        raise ContainerError(f"file is only {len(raw)} bytes; too short to be a .sav")

    uncompressed_length, compressed_length = struct.unpack_from("<II", raw, 0)
    magic = raw[8:11]
    save_type = raw[11]
    body = raw[HEADER_LEN:]

    if magic not in (MAGIC_PLZ, MAGIC_PLM):
        raise UnsupportedCompressionError(
            f"unrecognised container magic {magic!r}; expected PlZ or PlM"
        )
    if uncompressed_length > MAX_PAYLOAD_BYTES:
        raise ContainerError(
            f"header claims {uncompressed_length:,} uncompressed bytes, above the "
            f"{MAX_PAYLOAD_BYTES:,} guard. Refusing to allocate."
        )

    # For a single-stage container the body is exactly compressed_length bytes.
    # Double-stage zlib records the *inner* stage length instead, so only check
    # the single-stage case.
    single_stage = not (magic == MAGIC_PLZ and save_type == TYPE_DOUBLE)
    if single_stage and len(body) != compressed_length:
        raise ContainerError(
            f"body is {len(body):,} bytes but header declares "
            f"{compressed_length:,}; file is truncated or padded"
        )

    if magic == MAGIC_PLZ:
        try:
            if save_type == TYPE_SINGLE:
                payload = _zlib_decompress(body)
            elif save_type == TYPE_DOUBLE:
                payload = _zlib_decompress(_zlib_decompress(body))
            else:
                raise UnsupportedCompressionError(f"unknown PlZ save type 0x{save_type:02X}")
        except zlib.error as exc:
            raise ContainerError(f"zlib decompression failed: {exc}") from exc
    else:
        if save_type != TYPE_SINGLE:
            raise UnsupportedCompressionError(
                f"unknown PlM save type 0x{save_type:02X}; only 0x31 is known"
            )
        payload = _oodle_decompress(body, uncompressed_length)

    if len(payload) != uncompressed_length:
        raise ContainerError(
            f"decompressed to {len(payload):,} bytes but header declares {uncompressed_length:,}"
        )
    if payload[:4] != GVAS_MAGIC:
        raise ContainerError(
            f"payload does not start with GVAS (found {payload[:4]!r}); "
            "this may not be a Palworld save"
        )

    return SavContainer(
        uncompressed_length=uncompressed_length,
        compressed_length=compressed_length,
        magic=magic,
        save_type=save_type,
        payload=payload,
    )


def encode(
    payload: bytes,
    *,
    magic: bytes = MAGIC_PLZ,
    save_type: int = TYPE_DOUBLE,
    level: int = 6,
) -> bytes:
    """
    Encode a GVAS payload into ``.sav`` bytes.

    Only ``PlZ`` output is supported. Writing ``PlM`` would need an Oodle
    compressor, which is proprietary and has no open-source implementation.
    The game upgrades ``PlZ`` to ``PlM`` on its next save, so this is not a
    limitation in practice.
    """
    if magic == MAGIC_PLM:
        raise UnsupportedCompressionError(
            "Cannot write PlM: no open-source Oodle compressor exists. Write "
            "PlZ instead - the game accepts it and re-saves as PlM."
        )
    if magic != MAGIC_PLZ:
        raise UnsupportedCompressionError(f"unsupported output magic {magic!r}")
    if payload[:4] != GVAS_MAGIC:
        raise ContainerError("refusing to encode a payload that is not GVAS")

    uncompressed_length = len(payload)
    if save_type == TYPE_SINGLE:
        body = zlib.compress(payload, level)
        compressed_length = len(body)
    elif save_type == TYPE_DOUBLE:
        inner = zlib.compress(payload, level)
        # The header records the INNER stage length for double-compressed files.
        compressed_length = len(inner)
        body = zlib.compress(inner, level)
    else:
        raise UnsupportedCompressionError(f"unknown save type 0x{save_type:02X}")

    header = struct.pack("<II", uncompressed_length, compressed_length)
    return header + magic + bytes([save_type]) + body


def read(path: str | Path) -> SavContainer:
    """Read and decode a ``.sav`` from disk."""
    return decode(Path(path).read_bytes())


def write(
    path: str | Path,
    payload: bytes,
    *,
    magic: bytes = MAGIC_PLZ,
    save_type: int = TYPE_DOUBLE,
) -> int:
    """Encode ``payload`` and write it to ``path``. Returns bytes written."""
    blob = encode(payload, magic=magic, save_type=save_type)
    Path(path).write_bytes(blob)
    return len(blob)
