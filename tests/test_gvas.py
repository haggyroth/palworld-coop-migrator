"""GVAS reader tests, including the property-tag layout regression."""

from __future__ import annotations

import struct

import pytest

from palmigrate.errors import GvasError
from palmigrate.gvas import Reader, parse, read_header, read_properties

from .conftest import NONE, fstring, gvas_header, prop_int


class TestHeader:
    def test_parses_palworld_header(self, minimal_gvas):
        header, _ = parse(minimal_gvas)
        assert header["save_game_class_name"] == "/Script/Pal.PalWorldOptionSaveGame"
        assert header["engine_version_major"] == 5
        assert header["engine_version_minor"] == 1
        assert header["engine_version_patch"] == 1
        assert header["engine_version_branch"] == "++UE5+Release-5.1"

    def test_rejects_non_gvas(self):
        with pytest.raises(GvasError, match="does not start with GVAS"):
            parse(b"NOPE" + b"\x00" * 100)

    def test_rejects_implausible_custom_version_count(self):
        blob = bytearray(gvas_header())
        # custom version count sits immediately after the 4-byte format field
        offset = blob.index(b"++UE5+Release-5.1") + len("++UE5+Release-5.1") + 1 + 4
        blob[offset : offset + 4] = struct.pack("<i", 999_999)
        with pytest.raises(GvasError, match="implausible custom version count"):
            parse(bytes(blob))


class TestPropertyTagLayout:
    """
    Palworld's tag is: name, type, int64 size, [tag data], uint8 guid flag.

    There is NO ArrayIndex field. Assuming the stock UE layout makes the very
    first property parse with a nonsense array index and then read a multi-
    gigabyte string length, so this is worth pinning down explicitly.
    """

    def test_int_property_value(self, minimal_gvas):
        _, props = parse(minimal_gvas)
        assert props["Version"] == 101

    def test_no_array_index_field(self):
        """
        Hand-build a property with exactly 4 value bytes and no array index.
        If the reader expected an ArrayIndex it would consume the value as one.
        """
        payload = gvas_header() + prop_int("Answer", 42) + NONE
        _, props = parse(payload)
        assert props["Answer"] == 42

    def test_size_is_int64_not_int32(self):
        """An int32 size read would leave 4 stray bytes and desync the walk."""
        payload = gvas_header() + prop_int("A", 1) + prop_int("B", 2) + NONE
        _, props = parse(payload)
        assert props == {"A": 1, "B": 2}


class TestPropertyTypes:
    def test_reads_all_scalar_types(self, world_option_gvas):
        _, props = parse(world_option_gvas)
        settings = props["OptionWorldData"]["Settings"]
        assert settings["ExpRate"] == pytest.approx(1.5)
        assert settings["PalCaptureRate"] == pytest.approx(2.0)
        assert settings["bIsMultiplay"] is True
        assert settings["DropItemMaxNum"] == 3000
        assert settings["ServerName"] == "My Co-op World"

    def test_enum_keeps_qualified_name(self, world_option_gvas):
        _, props = parse(world_option_gvas)
        settings = props["OptionWorldData"]["Settings"]
        assert settings["Difficulty"] == "EPalOptionWorldDifficulty::Normal"

    def test_bool_false_is_read_from_tag(self):
        from .conftest import prop_bool

        payload = gvas_header() + prop_bool("bOff", False) + prop_int("After", 7) + NONE
        _, props = parse(payload)
        assert props["bOff"] is False
        assert props["After"] == 7  # proves the walk stayed aligned

    def test_nested_structs_recurse(self, world_option_gvas):
        _, props = parse(world_option_gvas)
        assert isinstance(props["OptionWorldData"], dict)
        assert isinstance(props["OptionWorldData"]["Settings"], dict)


class TestRemainingPropertyTypes:
    """
    Types the WorldOption fixture never exercises but a real Level.sav does.
    Each must round-trip its value *and* leave the cursor aligned, which the
    trailing sentinel property checks.
    """

    def _parse(self, prop: bytes):
        from .conftest import prop_int as _int

        payload = gvas_header() + prop + _int("Sentinel", 999) + NONE
        _, props = parse(payload)
        assert props["Sentinel"] == 999, "walk desynchronised after the property"
        return props

    def test_int64(self):
        from .conftest import prop_int64

        props = self._parse(prop_int64("Big", -2_000_000_000_000))
        assert props["Big"] == -2_000_000_000_000

    def test_double(self):
        from .conftest import prop_double

        props = self._parse(prop_double("Precise", 1234.5678))
        assert props["Precise"] == pytest.approx(1234.5678)

    def test_byte_property_with_enum_name(self):
        from .conftest import prop_byte_enum

        props = self._parse(prop_byte_enum("Mode", "EPalMode", "EPalMode::Hard"))
        assert props["Mode"] == "EPalMode::Hard"

    def test_byte_property_raw(self):
        from .conftest import prop_byte_raw

        props = self._parse(prop_byte_raw("Flag", 7))
        assert props["Flag"] == 7

    def test_array_of_strings(self):
        from .conftest import prop_array_str

        props = self._parse(prop_array_str("Names", ["alpha", "beta", "gamma"]))
        assert props["Names"] == ["alpha", "beta", "gamma"]

    def test_array_of_enums(self):
        from .conftest import prop_array_str

        values = ["EPalAllowConnectPlatform::Steam", "EPalAllowConnectPlatform::Xbox"]
        props = self._parse(prop_array_str("Platforms", values, inner="EnumProperty"))
        assert props["Platforms"] == values

    def test_opaque_array_is_skipped_not_parsed(self):
        """
        Palworld stores custom binary in ArrayProperty<ByteProperty>. The reader
        must not try to interpret it, but must still land on the next property.
        """
        from .conftest import prop_array_bytes

        props = self._parse(prop_array_bytes("RawData", 4, b"\xde\xad\xbe\xef"))
        assert props["RawData"]["__array_of__"] == "ByteProperty"
        assert props["RawData"]["__count__"] == 4

    def test_binary_struct_is_returned_raw(self):
        from .conftest import prop_struct_binary

        raw = bytes(range(16))
        props = self._parse(prop_struct_binary("Id", "Guid", raw))
        assert props["Id"]["__struct_type__"] == "Guid"
        assert props["Id"]["__raw__"] == raw

    def test_datetime_struct_is_binary(self):
        from .conftest import prop_struct_binary

        props = self._parse(prop_struct_binary("Timestamp", "DateTime", b"\x01" * 8))
        assert props["Timestamp"]["__struct_type__"] == "DateTime"

    def test_unknown_property_type_skips_its_declared_size(self):
        from .conftest import prop_unknown

        props = self._parse(prop_unknown("Weird", "SomeFutureProperty", b"\x00" * 12))
        assert props["Weird"]["__unparsed__"] == "SomeFutureProperty"
        assert props["Weird"]["__size__"] == 12


class TestDesyncDetection:
    def test_raises_when_type_is_not_a_property(self):
        payload = gvas_header() + fstring("Broken") + fstring("NotAType") + NONE
        with pytest.raises(GvasError, match="desynchronised"):
            parse(payload)

    def test_raises_on_read_past_end(self):
        payload = gvas_header() + fstring("Truncated")
        with pytest.raises(GvasError, match="read past end"):
            parse(payload)

    def test_negative_property_size_rejected(self):
        payload = (
            gvas_header()
            + fstring("Bad")
            + fstring("IntProperty")
            + struct.pack("<q", -8)
            + b"\x00"
        )
        with pytest.raises(GvasError, match="negative property size"):
            parse(payload)


class TestReader:
    def test_fstring_handles_utf16(self):
        text = "café"
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        buf = struct.pack("<i", -(len(text) + 1)) + encoded
        assert Reader(buf).fstring() == text

    def test_fstring_empty(self):
        assert Reader(struct.pack("<i", 0)).fstring() == ""

    def test_properties_terminate_on_none(self):
        r = Reader(NONE)
        assert read_properties(r) == {}

    def test_header_then_properties_are_separable(self, minimal_gvas):
        r = Reader(minimal_gvas)
        read_header(r)
        assert read_properties(r) == {"Version": 101}
