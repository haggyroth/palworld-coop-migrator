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

#: The value Palworld puts in ``CharacterSaveParameterMap`` keys for entries
#: that are Pals rather than player characters.
#:
#: It is byte-identical to the co-op host's PlayerUId, and that coincidence is
#: a trap. In a real save every one of 99 Pals carried this in its key --
#: including Pals whose ``OwnerPlayerUId`` was a *different* player -- which
#: proves the key field is a type marker there, not an owner.
#:
#: Rewriting it tells the server those entries are no longer Pals and it
#: deletes them on load. Measured: remapping the 100 key occurrences took a
#: world from 102 characters to 3, while remapping the other 301 references
#: preserved all 102.
PAL_SENTINEL_UID = "00000000000000000000000000000001"


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
    #: Key fields holding :data:`PAL_SENTINEL_UID` on entries that are Pals.
    #: Deliberately kept out of ``refs`` so a remap can never rewrite them.
    pal_sentinels: list[GuidRef] = field(default_factory=list)

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


#: Sanity bound on a guild roster. Palworld's own GuildPlayerMaxNum default is
#: 20; this is deliberately loose but still rules out garbage.
MAX_GUILD_MEMBERS = 128

#: Longest plausible player name, in bytes, for the shape check below.
MAX_PLAYER_NAME = 64


def _try_parse_members(tail: bytes, pos: int) -> tuple[list[int], int] | None:
    """
    Try to read ``int32 count`` at ``pos`` followed by exactly that many player
    records of ``{Guid, int64 last_online, FString name, uint8 platform}``.

    Returns ``(uid_offsets, end)`` or ``None``. Used to find the roster by its
    shape instead of by a hardcoded offset, because the bytes preceding it vary
    with group type and guessing there corrupts guild membership.
    """
    if pos + 4 > len(tail):
        return None
    (count,) = struct.unpack_from("<i", tail, pos)
    if not 1 <= count <= MAX_GUILD_MEMBERS:
        return None

    offsets: list[int] = []
    cursor = pos + 4
    for _ in range(count):
        if cursor + GUID_SIZE + 8 + 4 > len(tail):
            return None
        offsets.append(cursor)
        cursor += GUID_SIZE + 8  # uid + last-online ticks
        (name_len,) = struct.unpack_from("<i", tail, cursor)
        cursor += 4
        if not 0 < name_len <= MAX_PLAYER_NAME or cursor + name_len > len(tail):
            return None
        name = tail[cursor : cursor + name_len]
        # A real name is printable ASCII with a trailing NUL.
        if not name.endswith(b"\x00") or not all(32 <= c < 127 for c in name[:-1]):
            return None
        cursor += name_len + 1  # name plus the trailing platform byte
    return offsets, cursor


def decode_guild_members(tail: bytes, base: int) -> list[GuidRef]:
    """
    Locate the admin id and member roster inside a Guild group's tail.

    The roster is found by shape: the only place a plausible count is followed
    by exactly that many well-formed player records. The admin id sits in the
    16 bytes immediately before the count.

    Verified against a real guild: admin plus three members, whose names decode
    as readable text, at the offsets this finds.
    """
    for pos in range(len(tail) - 4):
        parsed = _try_parse_members(tail, pos)
        if parsed is None:
            continue
        uid_offsets, _end = parsed
        refs: list[GuidRef] = []
        if pos >= GUID_SIZE:
            admin = pos - GUID_SIZE
            refs.append(
                GuidRef(
                    "admin_player_uid",
                    base + admin,
                    guid_mod.from_bytes(tail[admin : admin + GUID_SIZE]),
                )
            )
        for index, off in enumerate(uid_offsets):
            refs.append(
                GuidRef(
                    f"players[{index}].player_uid",
                    base + off,
                    guid_mod.from_bytes(tail[off : off + GUID_SIZE]),
                )
            )
        return refs
    return []


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

                # Character entries must be classified before their key can be
                # judged: a Pal's key.PlayerUId is a type marker, not an owner.
                is_player = None
                if map_name == "CharacterSaveParameterMap":
                    is_player = _entry_is_player(payload, value)

                key_refs: list[GuidRef] = []
                _collect(key, f"{base}.key", key_refs)
                for ref in key_refs:
                    if (
                        is_player is False
                        and ref.path.endswith(".key.PlayerUId")
                        and ref.value == PAL_SENTINEL_UID
                    ):
                        result.pal_sentinels.append(ref)
                    else:
                        result.refs.append(ref)

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


def _entry_is_player(payload: bytes, value: Any) -> bool | None:
    """
    Is this ``CharacterSaveParameterMap`` entry a player character?

    Reads ``RawData.SaveParameter.IsPlayer``. Returns ``None`` when the blob
    cannot be decoded, which the caller treats as "not proven to be a Pal" so
    the key is kept as a normal reference rather than silently dropped.
    """
    raw = value.get("RawData")
    if not _is_raw_data(raw):
        return None
    offset = raw["__data_offset__"]
    length = raw["__count__"]
    reader = Reader(payload[offset : offset + length], 0, base=offset)
    try:
        inner = read_properties(reader)
    except PalMigrateError:
        return None
    params = inner.get("SaveParameter")
    if not isinstance(params, dict):
        return None
    return bool(params.get("IsPlayer", False))


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
            tail = blob[tail_start:]
            tail_base = offset + tail_start
            members = decode_guild_members(tail, tail_base)
            if members:
                result.refs.extend(GuidRef(f"{path}.{m.path}", m.offset, m.value) for m in members)
            else:
                # No roster found. For a Guild that is suspicious, so surface
                # it; for the other group types the tail genuinely holds no
                # player ids and an empty result is correct.
                result.opaque.append(OpaqueRegion(f"{path}.tail", tail_base, length - tail_start))
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
