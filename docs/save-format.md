# Palworld save format notes

Findings from working with current-build Palworld saves (dedicated server
buildid `24181105`, UE 5.1.1). Everything here was verified against real files
rather than taken from existing write-ups, several of which are now stale.

Player ids shown below are synthetic. Real ones are derived from a player's
account, so none appear in this repository.

---

## 1. The container

A `.sav` file is a 12-byte header wrapping a compressed UE5 GVAS blob.

| Offset | Size | Field |
|--------|------|-------|
| 0 | 4 | `uint32` uncompressed length |
| 4 | 4 | `uint32` compressed length |
| 8 | 3 | magic, `PlZ` or `PlM` |
| 11 | 1 | save type, `0x31` single stage or `0x32` double stage |
| 12 | .. | compressed payload |

### `PlZ`, the zlib era

`0x31` is one zlib stage; `0x32` is two nested zlib stages.

For `PlZ2` the header's *compressed length* records the **inner** stage's
length, not the number of bytes on disk. Writing the on-disk length there
produces a file the game rejects.

### `PlM`, the Oodle era

Current builds write `PlM1`: a single Oodle (Kraken) stage. Every file in a
modern save uses it.

```
Level.sav        PlM1  cmp=  396,410  unc=   5,450,357
LevelMeta.sav    PlM1  cmp=    2,011  unc=       2,239
LocalData.sav    PlM1  cmp=   33,258  unc=   5,261,130
WorldOption.sav  PlM1  cmp=    4,410  unc=       9,187
```

A quick way to identify one by eye: `GVAS` often appears as an early literal
around offset 20, because Oodle emits leading literals uncompressed.

```
75 2A 53 00  7A 0C 06 00  50 6C 4D 31  8C 0A 00 3A  C7 88 24 BD  47 56 41 53
^ unc len    ^ cmp len    ^ "PlM1"     ^ Oodle stream            ^ "GVAS"
```

---

## 2. Writing modern saves without an Oodle compressor

Oodle is proprietary. The open-source `ooz` reimplementation, and the `pyooz`
binding built on it, **decompress only**. There is no open-source Kraken
compressor, so a tool cannot emit a `PlM` container.

That looks fatal for round-tripping a modern save. It isn't.

**The game still accepts `PlZ` containers and rewrites them as `PlM` on its
next save.**

Verified by taking a server-generated world, re-encoding `Level.sav` as `PlZ2`,
and booting the server against it:

```
wrote PlZ2 Level.sav (17,608 bytes)

server stayed up, world id unchanged, no new world generated
server re-emitted     : PlM1
uncompressed payload  : 278,299 -> 278,299 bytes (identical)
byte-identical        : no  (timestamps and world clock advance, as expected)
```

So the supported pipeline is:

```
read PlM (ooz)  ->  modify GVAS  ->  write PlZ (zlib)  ->  game upgrades to PlM
```

This is what makes a fully open-source toolchain possible on current saves.

---

## 3. Property tag layout

Palworld's GVAS property tag is **not** the stock `FPropertyTag`:

```
FString  name              ("None" terminates the list)
FString  type              e.g. "IntProperty"
int64    value_size
<type-specific tag data>
uint8    has_property_guid
<value bytes>
```

Two differences that matter:

- the size is an **`int64`**, not an `int32`
- there is **no `ArrayIndex` field**

Assuming the stock layout desynchronises the walk on the very first property.
The failure is loud and distinctive: `Version` parses with an array index of
`25856`, then the reader tries to read a 1.7 GB string.

```
struct.error: unpack_from requires a buffer of at least 1701671052 bytes
```

Correct parse of the same bytes:

```
08 00 00 00 "Version\0"  0c 00 00 00 "IntProperty\0"
04 00 00 00 00 00 00 00     <- int64 size = 4
00                          <- has_property_guid
65 00 00 00                 <- value = 101
```

Type-specific tag data, inserted between the size and the guid flag:

| Type | Tag data |
|------|----------|
| `StructProperty` | `FString` struct type, then a 16-byte struct guid |
| `BoolProperty` | `uint8` value; the value lives here, body size is 0 |
| `ByteProperty` / `EnumProperty` | `FString` enum name |
| `ArrayProperty` | `FString` inner type |

---

## 4. Player identifiers

A PlayerUId is a UE `FGuid`: four little-endian `uint32` fields, printed as
8 hex digits each and concatenated into the 32-character name used for files
in `Players/`.

Palworld only populates the first field, so a player id looks like
`A1B2C3D4000000000000000000000000`: four populated bytes and twelve zero bytes.

The co-op host is always `00000000000000000000000000000001`, which in memory
is:

```
00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00
```

Twelve zero bytes followed by `int32` 1.

### Why byte-level search-and-replace does not work

That pattern is indistinguishable from ordinary zero padding followed by a
count, flag, or version of 1, which appears constantly in save data.

Measured on one real 5.4 MB `Level.sav`:

| GUID | Occurrences |
|------|-------------|
| player `A1B2C3D4...` (populated field) | 174 |
| player `E5F60718...` (populated field) | 56 |
| **host `00000000...0001`** | **2,904** |

Genuine player reference counts sit in the tens to low hundreds. So roughly
**2,700 of the 2,904 host matches are false positives**. Rewriting them all
would corrupt thousands of unrelated fields.

Alignment does not rescue the approach either: only 703 of the 2,904 hits are
4-byte aligned, which is still an order of magnitude too many.

A correct remap has to locate GUIDs **structurally**, by walking the save and
rewriting only fields that are genuinely `PlayerUId`.

Run `palmigrate scan` against any save to see this analysis for yourself.

---

## 5. Per-player Pal storage (`_dps.sav`)

Modern saves add a `Players/<PlayerUId>_dps.sav` sidecar holding that player's
Pal storage, split out of `Level.sav`. It compresses extraordinarily well
because the container is largely preallocated empty slots:

```
A1B2C3D4..._dps.sav   PlM1  cmp=65,839  unc=73,642,279   (1118x)
```

`PlayerDataPalStorageUpdateCheckTickInterval` in `PalWorldSettings.ini` is the
related tunable. Any migration tool has to carry these sidecars across and
remap them, or Pals go missing. Tools predating this file will silently ignore
it.

---

## 6. `WorldOption.sav` vs `PalWorldSettings.ini`

A co-op world stores its complete option set in `WorldOption.sav`. A dedicated
server **ignores that file** and reads `PalWorldSettings.ini` instead, which
is why every migration guide says to delete `WorldOption.sav` on import. A
server-generated world does not contain one at all, which confirms it.

The practical consequence is better than "delete it": the file can be *read*,
and its 119 settings rendered into a matching `PalWorldSettings.ini`, so the
dedicated server reproduces the co-op world's rules exactly instead of being
configured from memory. That is what `palmigrate settings` does.

Two traps when generating the ini:

- `OptionSettings=(...)` must be **one single line**. One malformed
  parenthesis makes the server silently discard every setting and fall back to
  stock, with no error.
- `Difficulty` should be `None` on a dedicated server, meaning "use the
  explicit rate list below". A co-op world records the preset it was created
  with (e.g. `Normal`), and carrying that across risks the preset overriding
  individual values that were customised.

---

## 7. A settings footgun worth knowing about

`bAutoResetGuildNoOnlinePlayers` dissolves a guild once no member has logged in
for `AutoResetGuildTimeNoOnlinePlayers` hours (72 by default).

On a co-op world that is harmless, because the world only runs while the host
is playing. On a dedicated server that sits idle between sessions it can
dissolve the guild and orphan every base, which is precisely what a migration
is meant to protect. Worth reviewing before carrying the co-op value across.
