# palworld-coop-migrator

Tools for moving a Palworld **co-op world onto a dedicated server** — including
the modern Oodle-compressed saves that the existing community tools cannot
open.

> **Status: alpha, but proven.** The container codec, GVAS reader, structural
> locator and host remap have completed a real co-op → dedicated migration:
> 102 characters, 99 Pals with correct owners, guild and bases intact. There is
> not yet a single `migrate` command — see [Roadmap](ROADMAP.md).

---

## Why this exists

Migrating a co-op world to a dedicated server has one hard problem. Palworld
hardcodes the co-op host's player id as `00000000000000000000000000000001`,
while a dedicated server derives a unique id per account. Copy the world across
without remapping that id and the host spawns as a fresh level 1 while their
bases sit there untouched.

The community answer has been `cheahjs/palworld-save-tools` plus
`xNul/palworld-host-save-fix`. Both are stale, and on a current save they do not
work at all:

| | |
|---|---|
| `cheahjs/palworld-save-tools` | last commit **2024-10-05**; zlib (`PlZ`) only |
| `xNul/palworld-host-save-fix` | pins palworld-save-tools **v0.17.1** (Feb 2024) |
| Upstream Oodle support | open PR #215 only — raised Jun 2025, stalled, unmerged |

Current Palworld writes `PlM` (Oodle) containers. The old tools cannot decompress
them, so they fail before they begin.

### The blocker, and the way around it

Oodle is proprietary. The open-source `ooz` reimplementation **decompresses
only** — there is no open-source Kraken compressor, so no tool can *write* a
`PlM` file.

It turns out that doesn't matter: **the game still accepts legacy `PlZ`
containers and rewrites them as `PlM` on its next save.** Verified by
re-encoding a world as `PlZ2` and booting a server against it — it loaded, kept
the world, and re-emitted `PlM1` with a byte-identical payload length.

So the pipeline is `read PlM -> modify -> write PlZ -> game upgrades it`, with
no proprietary dependency anywhere. Full details in
[docs/save-format.md](docs/save-format.md).

---

## Install

```bash
pip install palmigrate[oodle]
```

`[oodle]` pulls in `pyooz`, needed to read modern `PlM` saves. Prebuilt wheels
exist for Windows, Linux (glibc and musl) and macOS on x86-64 and ARM64, so no
compiler is required. Omit the extra if you only handle old `PlZ` saves.

From source:

```bash
git clone https://github.com/haggyroth/palworld-coop-migrator
cd palworld-coop-migrator
pip install -e ".[oodle,dev]"
pytest
```

---

## Usage

### Identify what you have

```bash
palmigrate inspect "path/to/world"
```

```
file                                          format    compressed   uncompressed
------------------------------------------------------------------------------
Level.sav                                     PlM1         396,410      5,450,357
LevelMeta.sav                                 PlM1           2,011          2,239
LocalData.sav                                 PlM1          33,258      5,261,130
WorldOption.sav                               PlM1           4,410          9,187
Players/00000000000000000000000000000001.sav  PlM1           7,561         18,624
Players/A1B2C3D4000000000000000000000000.sav  PlM1           7,443         19,133
```

`PlM` means Oodle, and means the older tools will not read this save.

### Check whether a GUID can be safely replaced

```bash
palmigrate scan "path/to/world/Level.sav"
```

```
GUID                                     hits   4-byte aligned
--------------------------------------------------------------
a1b2c3d4000000000000000000000000          174              103
e5f60718000000000000000000000000           56               31
00000000000000000000000000000001  (target)   2,904              703

WARNING: GUID 00000000000000000000000000000001 contains a long run of zero
bytes. Its raw form collides with ordinary padding, so it must never be
located by byte-pattern search alone.
Busiest real player GUID has 174 references. The target has 2,904.
Estimated false positives: ~2,730

Byte-level replacement is NOT SAFE for this GUID. A structural remap is required.
```

This is the check that stops naive "find and replace the host GUID" scripts
from quietly destroying a save.

### Recover the co-op world's settings

A dedicated server ignores `WorldOption.sav` and reads `PalWorldSettings.ini`,
which is why guides tell you to delete it. But it can be *read* — so the
dedicated server can reproduce the co-op rules exactly instead of being
configured from memory.

```bash
palmigrate settings "world/WorldOption.sav"

palmigrate settings "world/WorldOption.sav" \
    --default-ini "PalServer/DefaultPalWorldSettings.ini" \
    --set 'ServerName="My Server"' \
    --set 'Difficulty=None' \
    -o PalWorldSettings.ini
```

The generated `OptionSettings=(...)` is validated as a single line with
balanced, quote-aware parentheses before it is written — one stray parenthesis
makes the server silently discard every setting with no error message.

Overrides inherit the default's quoting, so a shell that eats your quotes
cannot silently produce a malformed value.

### Convert a container

```bash
palmigrate convert Level.sav Level.plz.sav
```

Re-encodes `PlM` to `PlZ` and verifies the payload round-trips byte-for-byte
before reporting success.

---

## Safety

- Every operation reads its input and writes somewhere else. Nothing is
  modified in place.
- `convert` verifies the round-trip and deletes its own output if the payload
  does not match.
- The container decoder validates declared lengths, guards against absurd
  allocation sizes, and rejects payloads that are not GVAS.
- The GVAS reader fails loudly on desync rather than returning plausible
  garbage.

**Back up your save before using any tool on it, including this one.**

---

## The Pal type marker

The single most destructive trap in this migration, and the reason a naive
remap eats your Pals.

`CharacterSaveParameterMap` keys hold `00000000000000000000000000000001` for
**every Pal** — byte-identical to the co-op host's PlayerUId, but meaning
*"this row is a Pal"*, not *"this Pal belongs to the host"*. The proof is that
Pals owned by other players carry it too.

Rewrite it and the server stops recognising those rows as Pals and deletes them
on load. Bisected on a real world:

| What was remapped | Refs | Characters after load |
|---|---|---|
| nothing (control) | 0 | 102 → 102 |
| everything | 401 | 102 → **3** |
| only `key.PlayerUId` | 100 | 102 → **3** |
| everything except `key.PlayerUId` | 301 | 102 → 102 |
| correct: keys only where `IsPlayer` | 302 | 102 → 102 |

`palmigrate` classifies each entry by `RawData.SaveParameter.IsPlayer` and keeps
the markers in `WalkResult.pal_sentinels`, outside the remappable set, so they
can't be touched.

**And the obvious validation does not catch this.** "No reference to the old id
survives" passes on the destroyed save — the rewrite really was complete. So
validation also compares entity counts and the Pal-marker count before and
after.

## Don't forget `LocalData.sav`

It holds per-player map discovery (125 hidden-location flags, 227 unlocked
techs). A dedicated server never reads or writes it — each **client** keeps its
own under `%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID64>\<world>\`.

Skip it and you get a very confusing result: character, Pals, guild and
inventory all perfect, but an unexplored map and fast-travel points showing as
locked *even though the server-side unlock flags are correct*. Copy the co-op
`LocalData.sav` onto the player's own machine, into the new server world's
folder.

## What is not done yet

An end-to-end `migrate` command. The pieces all work and are tested — see
[ROADMAP.md](ROADMAP.md).

---

## Privacy

No game save data is committed to this repository. Saves carry player names and
account-derived identifiers, so the test suite builds synthetic GVAS fixtures
byte-by-byte instead, and every player id appearing in the docs is invented.

The occurrence counts quoted above are real measurements, but they are
aggregate statistics rather than identifiers.

---

## Acknowledgements

The GVAS structure work by [cheahjs/palworld-save-tools](https://github.com/cheahjs/palworld-save-tools)
and the original host-fix approach from [xNul/palworld-host-save-fix](https://github.com/xNul/palworld-host-save-fix)
mapped out this territory first. This project exists because both stopped
tracking the game, not because they were wrong.

Oodle decompression via [pyooz](https://pypi.org/project/pyooz/), which binds
the open-source `ooz` reimplementation.

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Pocketpair, Inc.
