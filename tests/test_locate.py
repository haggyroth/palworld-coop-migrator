"""
Structural location of player ids.

The property under test throughout: a reference is reported only when the
parser identified the field as a ``Guid`` struct. Bytes that merely *look* like
the host id are never counted, because the host id is twelve zero bytes plus
``int32`` 1 and that pattern is ordinary padding.
"""

from __future__ import annotations

import struct

import pytest

from palmigrate import guid, locate
from palmigrate.errors import PalMigrateError

from .conftest import (
    NONE,
    fstring,
    group_raw_data,
    gvas_header,
    map_entries,
    prop_byte_array,
    prop_guid,
    prop_guid_array,
    prop_int,
    prop_str,
)

HOST = guid.to_bytes(guid.COOP_HOST_GUID)
OTHER = guid.to_bytes("a1b2c3d4000000000000000000000000")
INSTANCE = bytes(range(16))


def build_world(map_name: str, entries: bytes, count: int, key_type: str = "StructProperty"):
    """A payload holding a single map, plus the parsed world dict."""
    from palmigrate import gvas

    payload = gvas_header() + map_entries(entries, count, key_type) + NONE
    _, props = gvas.parse(payload)
    return payload, {map_name: props["TheMap"]}


def character_entry(player_uid: bytes, raw_inner: bytes = b"", *, is_player: bool = True) -> bytes:
    """
    A CharacterSaveParameterMap entry: property-list key, RawData value.

    Defaults to a *player* entry. An entry with no readable
    ``SaveParameter.IsPlayer`` cannot be classified, and its key is then
    withheld from ``refs`` on purpose — so tests about ordinary references need
    a classifiable entry to be testing what they think they are.
    """
    from .conftest import prop_bool, prop_struct

    key = (
        prop_guid("PlayerUId", player_uid)
        + prop_guid("InstanceId", INSTANCE)
        + prop_str("DebugName", "")
        + NONE
    )
    params = raw_inner + prop_int("Level", 5)
    if is_player:
        params = prop_bool("IsPlayer", True) + params
    inner = (
        prop_struct("SaveParameter", "PalIndividualCharacterSaveParameter", params + NONE) + NONE
    )
    value = prop_byte_array("RawData", inner) + NONE
    return key + value


class TestCollectFindsOnlyRealFields:
    def test_finds_a_guid_field_with_its_offset(self):
        payload, world = build_world("CharacterSaveParameterMap", character_entry(HOST), 1)
        result = locate.walk(payload, world)
        refs = result.matching(guid.COOP_HOST_GUID)
        assert len(refs) == 1
        assert refs[0].path.endswith("key.PlayerUId")
        # the recorded offset must actually hold those bytes
        off = refs[0].offset
        assert payload[off : off + 16] == HOST

    def test_padding_that_looks_like_the_host_id_is_not_reported(self):
        """
        The whole design rests on this. A run of zero bytes followed by an
        int32 of 1 is byte-identical to the co-op host id.
        """
        decoy = prop_byte_array("RawData", b"\x00" * 12 + b"\x01\x00\x00\x00" + NONE)
        key = prop_guid("PlayerUId", OTHER) + prop_str("DebugName", "") + NONE
        payload, world = build_world("CharacterSaveParameterMap", key + decoy + NONE, 1)
        result = locate.walk(payload, world)
        assert result.matching(guid.COOP_HOST_GUID) == []

    def test_offsets_inside_raw_data_are_absolute(self):
        """
        RawData is parsed from a slice. Without a base offset every reference
        inside it would point at the wrong place in the file.
        """
        inner = prop_guid("OwnerPlayerUId", HOST) + NONE
        payload, world = build_world("CharacterSaveParameterMap", character_entry(OTHER, inner), 1)
        refs = locate.walk(payload, world).matching(guid.COOP_HOST_GUID)
        assert len(refs) == 1
        off = refs[0].offset
        assert payload[off : off + 16] == HOST

    def test_guid_arrays_are_walked(self):
        inner = prop_guid_array("OldOwnerPlayerUIds", [OTHER, HOST]) + NONE
        payload, world = build_world("CharacterSaveParameterMap", character_entry(OTHER, inner), 1)
        refs = locate.walk(payload, world).matching(guid.COOP_HOST_GUID)
        assert len(refs) == 1
        assert "OldOwnerPlayerUIds[1]" in refs[0].path
        assert payload[refs[0].offset : refs[0].offset + 16] == HOST

    def test_distinct_values_counts_every_guid(self):
        entries = character_entry(HOST) + character_entry(HOST) + character_entry(OTHER)
        payload, world = build_world("CharacterSaveParameterMap", entries, 3)
        counts = locate.walk(payload, world).distinct_values()
        assert counts[guid.COOP_HOST_GUID] == 2
        assert counts["a1b2c3d4000000000000000000000000"] == 1


class TestEntryKeyEncodings:
    def test_property_list_key(self):
        payload, world = build_world("CharacterSaveParameterMap", character_entry(HOST), 1)
        refs = locate.walk(payload, world).matching(guid.COOP_HOST_GUID)
        assert refs and refs[0].path.endswith("key.PlayerUId")

    def test_bare_guid_key(self):
        """GroupSaveDataMap keys are a raw 16-byte Guid with no wrapper."""
        raw = group_raw_data(OTHER, [(HOST, INSTANCE)])
        entry = OTHER + prop_byte_array("RawData", raw) + NONE
        payload, world = build_world("GroupSaveDataMap", entry, 1)
        result = locate.walk(payload, world)
        assert result.matching(guid.COOP_HOST_GUID)


class TestIntegrityCheck:
    def test_misread_layout_is_refused_not_trusted(self):
        """
        If the entries do not consume the map body exactly we have
        misunderstood the layout, and patching would hit the wrong bytes.
        """
        payload, world = build_world("CharacterSaveParameterMap", character_entry(HOST), 1)
        info = dict(world["CharacterSaveParameterMap"])
        info["__body_length__"] += 8  # pretend the body is longer
        with pytest.raises(PalMigrateError, match="refusing to trust"):
            list(locate.iter_map_entries(payload, info))

    def test_walk_records_the_failure_rather_than_dropping_it(self):
        payload, world = build_world("CharacterSaveParameterMap", character_entry(HOST), 1)
        info = dict(world["CharacterSaveParameterMap"])
        info["__body_length__"] += 8
        result = locate.walk(payload, {"CharacterSaveParameterMap": info})
        assert result.undecoded
        assert "CharacterSaveParameterMap" in result.undecoded[0]

    def test_absent_map_is_reported(self):
        result = locate.walk(b"", {})
        assert len(result.undecoded) == len(locate.PLAYER_BEARING_MAPS)


class TestGroupRawData:
    def test_decodes_handles_and_returns_tail_offset(self):
        raw = group_raw_data(OTHER, [(HOST, INSTANCE), (OTHER, INSTANCE)], tail=b"\xaa" * 13)
        refs, tail = locate.decode_group_raw_data(raw, base=1000)
        assert [r.value for r in refs] == [
            guid.COOP_HOST_GUID,
            "a1b2c3d4000000000000000000000000",
        ]
        assert refs[0].offset == 1000 + 24  # group_id 16 + name 4 + count 4
        assert tail == len(raw) - 13

    def test_zero_handles(self):
        raw = group_raw_data(OTHER, [], tail=b"\x00" * 13)
        refs, tail = locate.decode_group_raw_data(raw, base=0)
        assert refs == []
        assert tail == 24

    def test_rejects_a_blob_that_is_too_short(self):
        with pytest.raises(PalMigrateError, match="only"):
            locate.decode_group_raw_data(b"\x00" * 8, base=0)

    def test_rejects_an_implausible_handle_count(self):
        raw = OTHER + struct.pack("<i", 0) + struct.pack("<i", 9999)
        with pytest.raises(PalMigrateError, match="do not fit"):
            locate.decode_group_raw_data(raw, base=0)

    def test_tail_is_recorded_as_opaque_not_ignored(self):
        """
        The guild name, admin id and member list live in the tail. Its layout
        varies by group type, so it must be surfaced rather than silently
        dropped -- a partial remap looks fine until pals go idle.
        """
        raw = group_raw_data(OTHER, [(HOST, INSTANCE)], tail=b"\xbb" * 40)
        entry = OTHER + prop_byte_array("RawData", raw) + NONE
        payload, world = build_world("GroupSaveDataMap", entry, 1)
        result = locate.walk(payload, world)
        assert len(result.opaque) == 1
        region = result.opaque[0]
        assert region.length == 40
        assert payload[region.offset : region.offset + region.length] == b"\xbb" * 40


class TestNonPlayerBlobs:
    def test_custom_version_data_is_skipped(self):
        """
        CustomVersionData holds engine version stamps, which are Guid-shaped
        but never player ids. Verified across all 69 character entries of a
        real save: none contains the host pattern.
        """
        key = prop_guid("PlayerUId", OTHER) + prop_str("DebugName", "") + NONE
        cvd = prop_byte_array(
            "CustomVersionData", struct.pack("<i", 1) + HOST + b"\x01\x00\x00\x00"
        )
        payload, world = build_world("CharacterSaveParameterMap", key + cvd + NONE, 1)
        result = locate.walk(payload, world)
        assert result.matching(guid.COOP_HOST_GUID) == []
        # skipped outright, so it must not be reported as undecoded either
        assert not any("CustomVersionData" in u for u in result.undecoded)


class TestFindReferences:
    def test_filters_to_the_requested_guid(self):
        entries = character_entry(HOST) + character_entry(OTHER)
        payload, world = build_world("CharacterSaveParameterMap", entries, 2)
        result = locate.find_references(payload, world, guid.COOP_HOST_GUID)
        assert len(result.refs) == 1
        assert result.refs[0].value == guid.COOP_HOST_GUID

    def test_normalises_the_query(self):
        payload, world = build_world("CharacterSaveParameterMap", character_entry(HOST), 1)
        upper = guid.COOP_HOST_GUID.upper()
        assert locate.find_references(payload, world, upper).refs

    def test_carries_undecoded_through(self):
        payload, world = build_world("CharacterSaveParameterMap", character_entry(HOST), 1)
        result = locate.find_references(payload, world, guid.COOP_HOST_GUID)
        assert isinstance(result.undecoded, list)


class TestRawDataThatIsNotAPropertyList:
    def test_is_reported_rather_than_silently_skipped(self):
        """
        Real saves contain these: three ItemContainerSaveData entries and both
        BaseCampSaveData entries hold RawData that is not a property list. They
        must be surfaced, because silently skipping a blob is how a remap ends
        up partial.
        """
        junk = prop_byte_array("RawData", b"\x7f\x7f\x7f\x7f" + b"\xfe" * 20)
        key = prop_guid("PlayerUId", OTHER) + prop_str("DebugName", "") + NONE
        payload, world = build_world("ItemContainerSaveData", key + junk + NONE, 1)
        result = locate.walk(payload, world)
        reported = [u for u in result.undecoded if "ItemContainerSaveData" in u]
        assert reported, f"blob was dropped silently; undecoded={result.undecoded}"


class TestGuidRefRepr:
    def test_repr_is_useful(self):
        ref = locate.GuidRef("a.b", 42, guid.COOP_HOST_GUID)
        assert "a.b" in repr(ref)
        assert "42" in repr(ref)


def test_fstring_helper_is_consistent():
    """Guard the fixture helper itself, since every test depends on it."""
    assert fstring("ab") == struct.pack("<i", 3) + b"ab\x00"
