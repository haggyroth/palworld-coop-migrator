"""
Minimal UE5 GVAS reader for Palworld saves.

Scope: the plain property tree. That is enough to read ``WorldOption.sav``
(the co-op world's full settings) and to walk the top level of ``Level.sav``.
It deliberately does **not** decode Palworld's custom binary ``RawData`` blobs
(``CharacterSaveParameterMap`` values and friends), which carry their own
undocumented layout and change between game versions.

Property tag layout
-------------------
Palworld's tag is **not** the stock ``FPropertyTag``. There is no
``ArrayIndex`` field, and the size is an ``int64``::

    FString  name          ("None" terminates the list)
    FString  type          e.g. "IntProperty"
    int64    value_size
    <type-specific tag data>
    uint8    has_property_guid
    <value bytes>

Getting this wrong desynchronises the walk immediately, which is how it was
found: assuming the stock layout made ``Version`` parse with an array index of
25856 and then read a 1.7 GB string length.
"""

from __future__ import annotations

import struct
from typing import Any, Final

from .errors import GvasError

GVAS_MAGIC: Final = b"GVAS"

#: Struct types serialised as opaque binary rather than a nested property list.
BINARY_STRUCTS: Final = frozenset(
    {
        "DateTime",
        "Guid",
        "Vector",
        "Vector2D",
        "Vector4",
        "Quat",
        "Rotator",
        "LinearColor",
        "Color",
        "IntPoint",
        "IntVector",
        "Box",
        "Transform",
        "FloatRange",
        "FloatRangeBound",
    }
)


class Reader:
    """
    Little-endian cursor over a bytes buffer.

    ``base`` is the offset of ``buf[0]`` within the outermost payload. Nested
    blobs (a ``RawData`` array parsed as its own property list) are read from a
    slice, so without it every offset recorded inside them would be relative to
    the slice and useless for patching the original file.
    """

    __slots__ = ("buf", "pos", "base")

    def __init__(self, buf: bytes, pos: int = 0, base: int = 0) -> None:
        self.buf = buf
        self.pos = pos
        self.base = base

    @property
    def abs_pos(self) -> int:
        """Current position as an offset into the outermost payload."""
        return self.base + self.pos

    def _unpack(self, fmt: str, size: int) -> Any:
        if self.pos + size > len(self.buf):
            raise GvasError(
                f"read past end of payload at offset {self.pos} (buffer is {len(self.buf)} bytes)"
            )
        (value,) = struct.unpack_from(fmt, self.buf, self.pos)
        self.pos += size
        return value

    def u8(self) -> int:
        return self._unpack("<B", 1)

    def u16(self) -> int:
        return self._unpack("<H", 2)

    def i32(self) -> int:
        return self._unpack("<i", 4)

    def u32(self) -> int:
        return self._unpack("<I", 4)

    def i64(self) -> int:
        return self._unpack("<q", 8)

    def u64(self) -> int:
        return self._unpack("<Q", 8)

    def f32(self) -> float:
        return self._unpack("<f", 4)

    def f64(self) -> float:
        return self._unpack("<d", 8)

    def raw(self, count: int) -> bytes:
        if count < 0:
            raise GvasError(f"negative read length {count} at offset {self.pos}")
        if self.pos + count > len(self.buf):
            raise GvasError(
                f"read of {count} bytes at offset {self.pos} exceeds payload "
                f"({len(self.buf)} bytes)"
            )
        out = self.buf[self.pos : self.pos + count]
        self.pos += count
        return out

    def fstring(self) -> str:
        """UE ``FString``: int32 length, positive = ASCII, negative = UTF-16LE."""
        length = self.i32()
        if length == 0:
            return ""
        if length > 0:
            return self.raw(length)[:-1].decode("utf-8", errors="replace")
        return self.raw(-length * 2)[:-2].decode("utf-16-le", errors="replace")


def read_header(r: Reader) -> dict[str, Any]:
    """Parse the GVAS file header, leaving the cursor at the first property."""
    magic = r.raw(4)
    if magic != GVAS_MAGIC:
        raise GvasError(f"payload does not start with GVAS (found {magic!r})")

    header: dict[str, Any] = {
        "save_game_version": r.i32(),
        "package_file_version_ue4": r.i32(),
        "package_file_version_ue5": r.i32(),
        "engine_version_major": r.u16(),
        "engine_version_minor": r.u16(),
        "engine_version_patch": r.u16(),
        "engine_version_changelist": r.u32(),
        "engine_version_branch": r.fstring(),
        "custom_version_format": r.i32(),
    }
    count = r.i32()
    if count < 0 or count > 10_000:
        raise GvasError(f"implausible custom version count {count}")
    header["custom_version_count"] = count
    r.raw(count * 20)  # each entry is a 16-byte guid plus an int32
    header["save_game_class_name"] = r.fstring()
    return header


def _read_struct_array(r: Reader, count: int, end: int) -> list[Any] | None:
    """
    Read an ``ArrayProperty<StructProperty>`` whose elements are a binary
    struct (``OldOwnerPlayerUIds`` is an array of ``Guid``).

    The array repeats a full property header before the elements::

        FString element_name, FString "StructProperty", int64 total_size,
        FString struct_type, byte[16] struct_guid, uint8 pad,
        <count * struct values>

    Returns ``None`` if the layout is not this shape or the struct type is not
    one we treat as binary, leaving the caller to skip the array opaquely. The
    cursor is restored on failure so the caller's skip stays correct.
    """
    saved = r.pos
    try:
        r.fstring()  # element name, repeated
        element_type = r.fstring()
        if element_type != "StructProperty":
            r.pos = saved
            return None
        total_size = r.i64()
        struct_type = r.fstring()
        if struct_type not in BINARY_STRUCTS:
            r.pos = saved
            return None
        r.raw(16)  # struct guid
        r.u8()  # padding
        if count == 0:
            return []
        element_size, remainder = divmod(total_size, count)
        if remainder or element_size <= 0 or r.pos + total_size > end:
            r.pos = saved
            return None
        out: list[Any] = []
        for _ in range(count):
            offset = r.abs_pos
            out.append(
                {
                    "__struct_type__": struct_type,
                    "__raw__": r.raw(element_size),
                    "__offset__": offset,
                }
            )
        return out
    except GvasError:
        r.pos = saved
        return None


def _read_value(r: Reader, ptype: str, size: int, tag: dict[str, Any]) -> Any:
    if ptype == "IntProperty":
        return r.i32()
    if ptype == "Int64Property":
        return r.i64()
    if ptype == "UInt32Property":
        return r.u32()
    if ptype == "UInt64Property":
        return r.u64()
    if ptype == "FloatProperty":
        return r.f32()
    if ptype == "DoubleProperty":
        return r.f64()
    if ptype == "BoolProperty":
        return bool(tag["bool_value"])  # value lives in the tag, not the body
    if ptype in ("StrProperty", "NameProperty", "EnumProperty"):
        return r.fstring()
    if ptype == "ByteProperty":
        if tag.get("enum_name", "None") != "None":
            return r.fstring()
        return r.u8()
    if ptype == "StructProperty":
        struct_type = tag.get("struct_type")
        if struct_type in BINARY_STRUCTS:
            offset = r.abs_pos
            return {
                "__struct_type__": struct_type,
                "__raw__": r.raw(size),
                "__offset__": offset,
            }
        return read_properties(r)
    if ptype == "ArrayProperty":
        end = r.pos + size
        inner = tag.get("inner_type")
        count = r.i32()
        if inner in ("StrProperty", "NameProperty", "EnumProperty"):
            values = [r.fstring() for _ in range(count)]
            r.pos = end
            return values
        if inner == "StructProperty":
            values = _read_struct_array(r, count, end)
            if values is not None:
                r.pos = end
                return values
        data_pos = r.pos
        data_offset = r.abs_pos
        r.pos = end  # opaque; skip to keep the walk in sync
        return {
            "__array_of__": inner,
            "__count__": count,
            "__data_offset__": data_offset,
            "__data_length__": end - data_pos,
        }
    if ptype == "SetProperty":
        end = r.pos + size
        r.i32()  # elements-to-remove count
        count = r.i32()
        r.pos = end  # opaque; element layout depends on the inner type
        return {"__set_of__": tag.get("key_type"), "__count__": count}
    if ptype == "MapProperty":
        # Palworld's big world tables (CharacterSaveParameterMap and friends)
        # are maps whose entries hold custom binary. We read the counts, which
        # are cheap and useful, then skip the body rather than guess at
        # layouts that shift between game versions.
        end = r.pos + size
        r.i32()  # entries-to-remove count, always 0 in practice
        count = r.i32()
        body_start = r.pos
        r.pos = end
        return {
            "__map__": True,
            "__key_type__": tag.get("key_type"),
            "__value_type__": tag.get("value_type"),
            "__count__": count,
            "__body_offset__": body_start,
            "__body_length__": end - body_start,
        }
    # Unknown type: skip its declared size so the walk stays aligned.
    r.raw(size)
    return {"__unparsed__": ptype, "__size__": size}


def read_properties(r: Reader) -> dict[str, Any]:
    """Read a ``None``-terminated property list into a dict."""
    out: dict[str, Any] = {}
    while True:
        name = r.fstring()
        if name in ("None", ""):
            return out

        ptype = r.fstring()
        if not ptype.endswith("Property"):
            raise GvasError(
                f"expected a property type after {name!r} but read {ptype!r}; "
                "the walk has desynchronised"
            )
        size = r.i64()
        if size < 0:
            raise GvasError(f"negative property size {size} for {name!r}")

        # NB: no ArrayIndex field here -- Palworld goes straight from the
        # int64 size into the type-specific tag data.
        tag: dict[str, Any] = {}
        if ptype == "StructProperty":
            tag["struct_type"] = r.fstring()
            tag["struct_guid"] = r.raw(16)
        elif ptype == "BoolProperty":
            tag["bool_value"] = r.u8()
        elif ptype in ("ByteProperty", "EnumProperty"):
            tag["enum_name"] = r.fstring()
        elif ptype == "ArrayProperty":
            tag["inner_type"] = r.fstring()
        elif ptype in ("MapProperty", "SetProperty"):
            # A map tag carries TWO extra FStrings. Missing them makes the
            # reader take the key-type length as the guid flag and desync
            # immediately, which is how Level.sav first failed to parse.
            tag["key_type"] = r.fstring()
            if ptype == "MapProperty":
                tag["value_type"] = r.fstring()
        r.u8()  # has_property_guid

        out[name] = _read_value(r, ptype, size, tag)


def parse(payload: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse a GVAS payload into ``(header, properties)``."""
    r = Reader(payload)
    header = read_header(r)
    return header, read_properties(r)
