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
