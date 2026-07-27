"""
Structurally locate player-id references in a decoded save.

The whole point of this module is to answer "where does this player id actually
appear?" *without* byte-pattern searching. It walks the save, and only reports
fields the parser identified as a ``Guid`` struct. A byte sequence that merely
looks like the host id -- zero padding followed by an ``int32`` of 1, which
occurs thousands of times -- is never considered, because it is not a field.

Each reference carries the absolute byte offset of its 16 raw bytes in the
uncompressed payload. Guids are fixed width, so a remap is a length-preserving
in-place overwrite: nothing needs re-serialising, and bytes we never decoded
cannot be disturbed.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import guid as guid_mod
from .errors import PalMigrateError
from .gvas import Reader, read_properties

GUID_SIZE = 16

#: Maps whose entries can hold player references. Listed explicitly so that a
#: map we cannot decode is reported rather than silently skipped -- a partial
#: remap is the failure this project exists to prevent.
PLAYER_BEARING_MAPS = (
    "CharacterSaveParameterMap",
    "GroupSaveDataMap",
    "CharacterContainerSaveData",
    "ItemContainerSaveData",
    "BaseCampSaveData",
    "GuildExtraSaveDataMap",
)

#: RawData blobs that are not player data and need no remapping.
#: ``CustomVersionData`` is ``int32 count`` followed by ``{Guid, int32}`` pairs
#: of *engine* custom-version stamps. Checked across all 69 character entries
#: of a real save: none contains the host id byte pattern.
NON_PLAYER_BLOBS = frozenset({"CustomVersionData"})


@dataclass(frozen=True)
class GuidRef:
    """One ``Guid``-typed field, located structurally."""

    path: str
    offset: int
    value: str

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"GuidRef({self.path} @{self.offset} = {self.value})"


@dataclass(frozen=True)
class OpaqueRegion:
    """
    A byte range known to contain player ids that we cannot decode field by
    field. Recorded rather than skipped: silently ignoring one produces a
    partial remap, which is worse than refusing, because the character loads
    and looks correct while pals idle and chests stay locked.
    """

    path: str
    offset: int
    length: int


@dataclass
class WalkResult:
    """Everything the walk found, plus anything it could not decode."""

    refs: list[GuidRef] = field(default_factory=list)
    undecoded: list[str] = field(default_factory=list)
    opaque: list[OpaqueRegion] = field(default_factory=list)

    def matching(self, guid_text: str) -> list[GuidRef]:
        wanted = guid_mod.normalise(guid_text)
        return [r for r in self.refs if r.value == wanted]

    def distinct_values(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.refs:
            counts[r.value] = counts.get(r.value, 0) + 1
        return counts


def _collect(node: Any, path: str, out: list[GuidRef]) -> None:
    """Recursively gather Guid structs, which carry their own offset."""
    if isinstance(node, dict):
        if "__struct_type__" in node:
            if node["__struct_type__"] == "Guid" and "__offset__" in node:
                out.append(
                    GuidRef(
                        path=path,
                        offset=node["__offset__"],
                        value=guid_mod.from_bytes(node["__raw__"]),
                    )
                )
            return
        if "__map__" in node or "__array_of__" in node or "__unparsed__" in node:
            return
        if "__set_of__" in node:
            return
        for key, value in node.items():
            _collect(value, f"{path}.{key}" if path else key, out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect(value, f"{path}[{index}]", out)


def _read_entry_key(r: Reader) -> Any:
    """
    Read a map entry key.

    Palworld uses two encodings. ``CharacterSaveParameterMap`` keys are a
    property list (``PlayerUId``, ``InstanceId``, ``DebugName``);
    ``GroupSaveDataMap`` keys are a bare 16-byte ``Guid`` with no property
    wrapper. Try the property list and fall back, restoring the cursor so the
    fallback reads from the right place.
    """
    saved = r.pos
    try:
        props = read_properties(r)
    except PalMigrateError:
        r.pos = saved
    else:
        if props:
            return props
        r.pos = saved

    offset = r.abs_pos
    raw = r.raw(GUID_SIZE)
    return {
        "__key_guid__": {
            "__struct_type__": "Guid",
            "__raw__": raw,
            "__offset__": offset,
        }
    }


def iter_map_entries(payload: bytes, map_info: dict[str, Any]) -> Iterator[tuple[Any, Any]]:
    """
    Yield ``(key, value)`` for each entry of a map recorded by the GVAS reader.

    Raises if the entries do not consume the map body exactly. That check is
    the integrity guarantee: a layout we have misunderstood ends at the wrong
    offset, and we would rather refuse than patch the wrong bytes.
    """
    start = map_info["__body_offset__"]
    length = map_info["__body_length__"]
    count = map_info["__count__"]
    r = Reader(payload, start)

    for _ in range(count):
        key = _read_entry_key(r)
        value = read_properties(r)
        yield key, value

    if r.pos != start + length:
        raise PalMigrateError(
            f"map entries consumed {r.pos - start} bytes but the body is "
            f"{length}; the entry layout was misread, refusing to trust it"
        )


def decode_group_raw_data(blob: bytes, base: int) -> tuple[list[GuidRef], int]:
    """
    Decode the leading, reliably-shaped part of a ``GroupSaveDataMap`` entry.

    Unlike ``CharacterSaveParameterMap``, a group's ``RawData`` is Palworld's
    own binary, not a nested property list::

        Guid    group_id
        FString group_name
        int32   handle_count
        handle_count * { Guid player_uid, Guid instance_id }
        <tail: layout varies by group type>

    Verified against all eight groups of a real save (handle counts 0, 3, 0,
    12, 0, 0, 0, 69), each landing exactly on the tail.

    Returns the player ids from the handle list and the offset where the tail
    begins. The tail holds the guild name, admin id and member list, whose
    layout shifts by group type -- it is handled separately rather than
    guessed at, because a wrong offset there corrupts guild membership.
    """
    refs: list[GuidRef] = []
    pos = 0

    if len(blob) < 24:
        raise PalMigrateError(f"group RawData is only {len(blob)} bytes")

    pos += GUID_SIZE  # group_id -- identifies the group, never a player

    (name_len,) = _unpack_i32(blob, pos)
    pos += 4
    if name_len > 0:
        pos += name_len
    elif name_len < 0:
        pos += -name_len * 2

    (handle_count,) = _unpack_i32(blob, pos)
    pos += 4
    if handle_count < 0 or pos + handle_count * 32 > len(blob):
        raise PalMigrateError(
            f"group RawData declares {handle_count} handles which do not fit in {len(blob)} bytes"
        )

    for index in range(handle_count):
        offset = pos
        refs.append(
            GuidRef(
                path=f"handles[{index}].player_uid",
                offset=base + offset,
                value=guid_mod.from_bytes(blob[offset : offset + GUID_SIZE]),
            )
        )
        pos += 32  # player uid + instance id

    return refs, pos


def _unpack_i32(buf: bytes, offset: int) -> tuple[int]:
    if offset + 4 > len(buf):
        raise PalMigrateError(f"read past end of group RawData at {offset}")
    return struct.unpack_from("<i", buf, offset)


def _walk_raw_data(value: Any, path: str, payload: bytes, out: list[GuidRef]) -> bool:
    """
    Parse a ``RawData`` byte array as a nested property list.

    Palworld stores per-entity state as an ``ArrayProperty<ByteProperty>`` whose
    contents are themselves a GVAS property list, so the same reader handles it
    once given the right base offset.

    Returns True if the blob was decoded.
    """
    if not (isinstance(value, dict) and value.get("__array_of__") == "ByteProperty"):
        return False

    offset = value.get("__data_offset__")
    length = value.get("__count__")
    if offset is None or not length:
        return False

    blob = payload[offset : offset + length]
    r = Reader(blob, 0, base=offset)
    try:
        props = read_properties(r)
    except PalMigrateError:
        return False
    _collect(props, path, out)
    return True


def walk(payload: bytes, world: dict[str, Any]) -> WalkResult:
    """Collect every ``Guid`` field reachable from ``worldSaveData``."""
    result = WalkResult()

    # Plain properties outside the big maps.
    for key, value in world.items():
        if isinstance(value, dict) and "__map__" in value:
            continue
        _collect(value, key, result.refs)

    for map_name in PLAYER_BEARING_MAPS:
        info = world.get(map_name)
        if not isinstance(info, dict) or "__map__" not in info:
            result.undecoded.append(f"{map_name} (absent or not a map)")
            continue
        try:
            for index, (key, value) in enumerate(iter_map_entries(payload, info)):
                base = f"{map_name}[{index}]"
                _collect(key, f"{base}.key", result.refs)
                for prop_name, prop_value in value.items():
                    if prop_name in NON_PLAYER_BLOBS:
                        continue
                    sub = f"{base}.{prop_name}"
                    if _is_raw_data(prop_value):
                        _walk_blob(map_name, sub, prop_value, payload, result)
                    else:
                        _collect(prop_value, sub, result.refs)
        except PalMigrateError as exc:
            result.undecoded.append(f"{map_name}: {exc}")

    return result


def _is_raw_data(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("__array_of__") == "ByteProperty"
        and value.get("__data_offset__") is not None
        and bool(value.get("__count__"))
    )


def _walk_blob(
    map_name: str,
    path: str,
    value: dict[str, Any],
    payload: bytes,
    result: WalkResult,
) -> None:
    """Decode a RawData blob, recording anything we cannot account for."""
    offset = value["__data_offset__"]
    length = value["__count__"]
    blob = payload[offset : offset + length]

    if map_name == "GroupSaveDataMap":
        try:
            refs, tail_start = decode_group_raw_data(blob, offset)
        except PalMigrateError as exc:
            result.undecoded.append(f"{path}: {exc}")
            return
        result.refs.extend(GuidRef(f"{path}.{r.path}", r.offset, r.value) for r in refs)
        if tail_start < length:
            # Guild name, admin id and member list live here. The layout
            # varies by group type, so it is reported rather than guessed.
            result.opaque.append(
                OpaqueRegion(f"{path}.tail", offset + tail_start, length - tail_start)
            )
        return

    if not _walk_raw_data(value, path, payload, result.refs):
        result.undecoded.append(f"{path}: RawData ({length} bytes) is not a property list")


def find_references(payload: bytes, world: dict[str, Any], guid_text: str) -> WalkResult:
    """Walk ``payload`` and keep only the refs equal to ``guid_text``."""
    full = walk(payload, world)
    wanted = guid_mod.normalise(guid_text)
    return WalkResult(
        refs=[r for r in full.refs if r.value == wanted],
        undecoded=full.undecoded,
    )
