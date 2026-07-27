"""
Synthetic GVAS fixtures.

Real Palworld saves are never committed to this repository: they are personal
data (player names, Steam-derived ids) and redistributing game data is a
licensing question nobody needs. Everything here is built byte-by-byte to the
documented layout instead, which also makes the tests assert the format rather
than merely echo a captured file.
"""

from __future__ import annotations

import struct

import pytest


def fstring(text: str) -> bytes:
    """UE FString: int32 length then null-terminated ASCII."""
    encoded = text.encode("ascii") + b"\x00"
    return struct.pack("<i", len(encoded)) + encoded


def gvas_header(
    class_name: str = "/Script/Pal.PalWorldOptionSaveGame",
    custom_versions: int = 3,
) -> bytes:
    """A GVAS file header matching what Palworld writes (UE 5.1.1)."""
    out = b"GVAS"
    out += struct.pack("<iii", 3, 522, 1008)  # save ver, ue4 ver, ue5 ver
    out += struct.pack("<HHH", 5, 1, 1)  # engine 5.1.1
    out += struct.pack("<I", 0)  # changelist
    out += fstring("++UE5+Release-5.1")
    out += struct.pack("<i", 3)  # custom version format
    out += struct.pack("<i", custom_versions)
    out += b"\x00" * (custom_versions * 20)  # guid + int32 per entry
    out += fstring(class_name)
    return out


def prop_int(name: str, value: int) -> bytes:
    return (
        fstring(name)
        + fstring("IntProperty")
        + struct.pack("<q", 4)
        + b"\x00"  # has_property_guid
        + struct.pack("<i", value)
    )


def prop_float(name: str, value: float) -> bytes:
    return (
        fstring(name)
        + fstring("FloatProperty")
        + struct.pack("<q", 4)
        + b"\x00"
        + struct.pack("<f", value)
    )


def prop_bool(name: str, value: bool) -> bytes:
    return (
        fstring(name)
        + fstring("BoolProperty")
        + struct.pack("<q", 0)
        + bytes([1 if value else 0])  # value lives in the tag
        + b"\x00"
    )


def prop_str(name: str, value: str) -> bytes:
    body = fstring(value)
    return fstring(name) + fstring("StrProperty") + struct.pack("<q", len(body)) + b"\x00" + body


def prop_enum(name: str, value: str, enum_type: str) -> bytes:
    body = fstring(value)
    return (
        fstring(name)
        + fstring("EnumProperty")
        + struct.pack("<q", len(body))
        + fstring(enum_type)
        + b"\x00"
        + body
    )


def prop_struct(name: str, struct_type: str, inner: bytes) -> bytes:
    return (
        fstring(name)
        + fstring("StructProperty")
        + struct.pack("<q", len(inner))
        + fstring(struct_type)
        + b"\x00" * 16  # struct guid
        + b"\x00"  # has_property_guid
        + inner
    )


def prop_int64(name: str, value: int) -> bytes:
    return (
        fstring(name)
        + fstring("Int64Property")
        + struct.pack("<q", 8)
        + b"\x00"
        + struct.pack("<q", value)
    )


def prop_double(name: str, value: float) -> bytes:
    return (
        fstring(name)
        + fstring("DoubleProperty")
        + struct.pack("<q", 8)
        + b"\x00"
        + struct.pack("<d", value)
    )


def prop_byte_enum(name: str, enum_name: str, value: str) -> bytes:
    body = fstring(value)
    return (
        fstring(name)
        + fstring("ByteProperty")
        + struct.pack("<q", len(body))
        + fstring(enum_name)
        + b"\x00"
        + body
    )


def prop_byte_raw(name: str, value: int) -> bytes:
    return (
        fstring(name)
        + fstring("ByteProperty")
        + struct.pack("<q", 1)
        + fstring("None")
        + b"\x00"
        + bytes([value])
    )


def prop_array_str(name: str, values: list[str], inner: str = "StrProperty") -> bytes:
    body = struct.pack("<i", len(values)) + b"".join(fstring(v) for v in values)
    return (
        fstring(name)
        + fstring("ArrayProperty")
        + struct.pack("<q", len(body))
        + fstring(inner)
        + b"\x00"
        + body
    )


def prop_array_bytes(name: str, count: int, payload: bytes) -> bytes:
    """An ArrayProperty of a non-string inner type: opaque, must be skipped."""
    body = struct.pack("<i", count) + payload
    return (
        fstring(name)
        + fstring("ArrayProperty")
        + struct.pack("<q", len(body))
        + fstring("ByteProperty")
        + b"\x00"
        + body
    )


def prop_struct_binary(name: str, struct_type: str, raw: bytes) -> bytes:
    """A StructProperty whose type is serialised as opaque binary."""
    return (
        fstring(name)
        + fstring("StructProperty")
        + struct.pack("<q", len(raw))
        + fstring(struct_type)
        + b"\x00" * 16
        + b"\x00"
        + raw
    )


def prop_uint32(name: str, value: int) -> bytes:
    return (
        fstring(name)
        + fstring("UInt32Property")
        + struct.pack("<q", 4)
        + b"\x00"
        + struct.pack("<I", value)
    )


def prop_uint64(name: str, value: int) -> bytes:
    return (
        fstring(name)
        + fstring("UInt64Property")
        + struct.pack("<q", 8)
        + b"\x00"
        + struct.pack("<Q", value)
    )


def prop_map(
    name: str,
    key_type: str,
    value_type: str,
    count: int,
    entries: bytes = b"",
) -> bytes:
    """
    A MapProperty. The tag carries TWO extra FStrings (key and value type);
    omitting them makes a reader take the key-type length as the guid flag.
    """
    body = struct.pack("<ii", 0, count) + entries
    return (
        fstring(name)
        + fstring("MapProperty")
        + struct.pack("<q", len(body))
        + fstring(key_type)
        + fstring(value_type)
        + b"\x00"
        + body
    )


def prop_set(name: str, key_type: str, count: int, entries: bytes = b"") -> bytes:
    """A SetProperty. The tag carries one extra FString."""
    body = struct.pack("<ii", 0, count) + entries
    return (
        fstring(name)
        + fstring("SetProperty")
        + struct.pack("<q", len(body))
        + fstring(key_type)
        + b"\x00"
        + body
    )


def prop_unknown(name: str, ptype: str, raw: bytes) -> bytes:
    """A property type the reader does not model; it must skip exactly `size`."""
    return fstring(name) + fstring(ptype) + struct.pack("<q", len(raw)) + b"\x00" + raw


def prop_guid(name: str, raw: bytes) -> bytes:
    """A StructProperty of type Guid -- how a PlayerUId is stored."""
    assert len(raw) == 16
    return (
        fstring(name)
        + fstring("StructProperty")
        + struct.pack("<q", 16)
        + fstring("Guid")
        + b"\x00" * 16
        + b"\x00"
        + raw
    )


def prop_guid_array(name: str, guids: list[bytes]) -> bytes:
    """
    ArrayProperty<StructProperty> of Guid, as OldOwnerPlayerUIds is stored.

    The array repeats a full property header before the elements.
    """
    total = 16 * len(guids)
    header = (
        fstring(name)
        + fstring("StructProperty")
        + struct.pack("<q", total)
        + fstring("Guid")
        + b"\x00" * 16
        + b"\x00"
    )
    body = struct.pack("<i", len(guids)) + header + b"".join(guids)
    return (
        fstring(name)
        + fstring("ArrayProperty")
        + struct.pack("<q", len(body))
        + fstring("StructProperty")
        + b"\x00"
        + body
    )


def prop_byte_array(name: str, blob: bytes) -> bytes:
    """ArrayProperty<ByteProperty> -- how Palworld stores RawData."""
    body = struct.pack("<i", len(blob)) + blob
    return (
        fstring(name)
        + fstring("ArrayProperty")
        + struct.pack("<q", len(body))
        + fstring("ByteProperty")
        + b"\x00"
        + body
    )


def map_entries(entries: bytes, count: int, key_type: str = "StructProperty") -> bytes:
    """A MapProperty whose body is supplied verbatim."""
    body = struct.pack("<ii", 0, count) + entries
    return (
        fstring("TheMap")
        + fstring("MapProperty")
        + struct.pack("<q", len(body))
        + fstring(key_type)
        + fstring("StructProperty")
        + b"\x00"
        + body
    )


def group_raw_data(group_id: bytes, handles: list[tuple[bytes, bytes]], tail: bytes = b"") -> bytes:
    """GroupSaveDataMap RawData: group_id, name, handle count, handles, tail."""
    out = group_id + struct.pack("<i", 0) + struct.pack("<i", len(handles))
    for uid, instance in handles:
        out += uid + instance
    return out + tail


def character_entry(
    player_uid: bytes, instance: bytes, *, is_player: bool, extra: bytes = b""
) -> bytes:
    """
    A CharacterSaveParameterMap entry.

    ``IsPlayer`` is what distinguishes a player character from a Pal, and it
    decides whether ``key.PlayerUId`` is a real owner id or the Pal type
    marker. Both cases must be constructible for the tests to be meaningful.
    """
    key = (
        prop_guid("PlayerUId", player_uid)
        + prop_guid("InstanceId", instance)
        + prop_str("DebugName", "")
        + NONE
    )
    params = prop_int("Level", 12) + extra
    if is_player:
        params = prop_bool("IsPlayer", True) + params
    inner = (
        prop_struct("SaveParameter", "PalIndividualCharacterSaveParameter", params + NONE) + NONE
    )
    value = prop_byte_array("RawData", inner) + NONE
    return key + value


NONE = fstring("None")


@pytest.fixture
def minimal_gvas() -> bytes:
    """A tiny but structurally valid GVAS payload."""
    return gvas_header() + prop_int("Version", 101) + NONE


@pytest.fixture
def world_option_gvas() -> bytes:
    """A GVAS payload shaped like WorldOption.sav, with a Settings block."""
    settings = (
        prop_enum("Difficulty", "EPalOptionWorldDifficulty::Normal", "EPalOptionWorldDifficulty")
        + prop_float("ExpRate", 1.5)
        + prop_float("PalCaptureRate", 2.0)
        + prop_bool("bIsMultiplay", True)
        + prop_bool("bEnableInvaderEnemy", True)
        + prop_int("DropItemMaxNum", 3000)
        + prop_str("ServerName", "My Co-op World")
        + prop_int("PublicPort", 8211)
        + NONE
    )
    world_data = prop_struct("Settings", "PalOptionWorldSettings", settings) + NONE
    payload = (
        gvas_header()
        + prop_int("Version", 101)
        + prop_struct("OptionWorldData", "PalOptionWorldSaveData", world_data)
        + NONE
    )
    return payload


#: Trimmed stand-in for DefaultPalWorldSettings.ini, preserving its real shape.
DEFAULT_INI_TEXT = (
    "; sample\n"
    "[/Script/Pal.PalGameWorldSettings]\n"
    "OptionSettings=("
    "Difficulty=None,"
    "ExpRate=1.000000,"
    "PalCaptureRate=1.000000,"
    "bIsMultiplay=False,"
    "bEnableInvaderEnemy=True,"
    "DropItemMaxNum=3000,"
    'ServerName="Default Palworld Server",'
    'ServerPassword="",'
    "PublicPort=8211,"
    "CrossplayPlatforms=(Steam,Xbox,PS5,Mac)"
    ")\n"
)


@pytest.fixture
def default_ini_text() -> str:
    return DEFAULT_INI_TEXT
