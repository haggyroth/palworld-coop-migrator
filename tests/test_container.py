"""Container codec tests. No Oodle dependency needed -- PlZ covers both paths."""

from __future__ import annotations

import struct
import zlib

import pytest

from palmigrate import container
from palmigrate.errors import ContainerError, UnsupportedCompressionError


def make_plz(payload: bytes, save_type: int = container.TYPE_DOUBLE) -> bytes:
    return container.encode(payload, save_type=save_type)


class TestRoundTrip:
    @pytest.mark.parametrize("save_type", [container.TYPE_SINGLE, container.TYPE_DOUBLE])
    def test_round_trip_is_lossless(self, minimal_gvas, save_type):
        blob = container.encode(minimal_gvas, save_type=save_type)
        assert container.decode(blob).payload == minimal_gvas

    def test_round_trip_preserves_declared_length(self, world_option_gvas):
        decoded = container.decode(make_plz(world_option_gvas))
        assert decoded.uncompressed_length == len(world_option_gvas)

    def test_double_header_records_inner_stage_length(self, minimal_gvas):
        """
        For PlZ2 the header's compressed length is the *inner* zlib stage,
        not the size of the bytes on disk. Getting this backwards is a classic
        way to write files the game rejects.
        """
        blob = container.encode(minimal_gvas, save_type=container.TYPE_DOUBLE)
        _, declared = struct.unpack_from("<II", blob, 0)
        on_disk = len(blob) - container.HEADER_LEN
        assert declared != on_disk
        inner = zlib.decompress(blob[container.HEADER_LEN :])
        assert declared == len(inner)

    def test_single_header_matches_body_length(self, minimal_gvas):
        blob = container.encode(minimal_gvas, save_type=container.TYPE_SINGLE)
        _, declared = struct.unpack_from("<II", blob, 0)
        assert declared == len(blob) - container.HEADER_LEN

    def test_file_round_trip(self, tmp_path, world_option_gvas):
        path = tmp_path / "Level.sav"
        written = container.write(path, world_option_gvas)
        assert written == path.stat().st_size
        assert container.read(path).payload == world_option_gvas


class TestHeaderParsing:
    def test_format_name(self, minimal_gvas):
        assert container.decode(make_plz(minimal_gvas)).format_name == "PlZ2"

    def test_is_oodle_false_for_plz(self, minimal_gvas):
        assert container.decode(make_plz(minimal_gvas)).is_oodle is False

    def test_plm_header_is_recognised_as_oodle(self, minimal_gvas):
        """A PlM header should be identified as Oodle, not rejected as unknown."""
        body = b"\x00" * 32
        blob = (
            struct.pack("<II", len(minimal_gvas), len(body))
            + container.MAGIC_PLM
            + bytes([container.TYPE_SINGLE])
            + body
        )
        # Without pyooz this raises OodleUnavailable; with it, decompression
        # fails on the junk body. Either way it must not be "unknown magic".
        with pytest.raises(ContainerError) as excinfo:
            container.decode(blob)
        assert "unrecognised container magic" not in str(excinfo.value)


class TestRejection:
    def test_rejects_short_file(self):
        with pytest.raises(ContainerError, match="too short"):
            container.decode(b"\x00" * 4)

    def test_rejects_unknown_magic(self, minimal_gvas):
        blob = bytearray(make_plz(minimal_gvas))
        blob[8:11] = b"XYZ"
        with pytest.raises(UnsupportedCompressionError, match="unrecognised"):
            container.decode(bytes(blob))

    def test_rejects_unknown_plz_save_type(self, minimal_gvas):
        blob = bytearray(container.encode(minimal_gvas, save_type=container.TYPE_SINGLE))
        blob[11] = 0x39
        with pytest.raises(UnsupportedCompressionError, match="unknown PlZ save type"):
            container.decode(bytes(blob))

    def test_rejects_truncated_body(self, minimal_gvas):
        blob = container.encode(minimal_gvas, save_type=container.TYPE_SINGLE)
        with pytest.raises(ContainerError, match="truncated"):
            container.decode(blob[:-5])

    def test_rejects_absurd_uncompressed_length(self, minimal_gvas):
        blob = bytearray(make_plz(minimal_gvas))
        blob[0:4] = struct.pack("<I", container.MAX_PAYLOAD_BYTES + 1)
        with pytest.raises(ContainerError, match="Refusing to allocate"):
            container.decode(bytes(blob))

    def test_rejects_length_mismatch(self, minimal_gvas):
        blob = bytearray(container.encode(minimal_gvas, save_type=container.TYPE_SINGLE))
        blob[0:4] = struct.pack("<I", len(minimal_gvas) + 99)
        with pytest.raises(ContainerError, match="declares"):
            container.decode(bytes(blob))

    def test_rejects_non_gvas_payload(self):
        payload = b"NOPE" + b"\x00" * 64
        body = zlib.compress(payload)
        blob = (
            struct.pack("<II", len(payload), len(body))
            + container.MAGIC_PLZ
            + bytes([container.TYPE_SINGLE])
            + body
        )
        with pytest.raises(ContainerError, match="GVAS"):
            container.decode(blob)


class TestEncodeGuards:
    def test_refuses_to_write_plm(self, minimal_gvas):
        with pytest.raises(UnsupportedCompressionError, match="no open-source Oodle"):
            container.encode(minimal_gvas, magic=container.MAGIC_PLM)

    def test_refuses_non_gvas_payload(self):
        with pytest.raises(ContainerError, match="not GVAS"):
            container.encode(b"garbage payload")

    def test_refuses_unknown_save_type(self, minimal_gvas):
        with pytest.raises(UnsupportedCompressionError, match="unknown save type"):
            container.encode(minimal_gvas, save_type=0x99)
