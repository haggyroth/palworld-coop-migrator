# palworld-coop-migrator

Move a Palworld **co-op world onto a dedicated server** without losing your
character, your Pals, your guild or your map — including the modern
Oodle-compressed saves that the existing community tools cannot open.

> **Status: alpha, but proven.** `palmigrate migrate` has completed a real
> co-op → dedicated migration: 102 characters, 99 Pals with correct owners,
> guild membership, bases and map all intact. Verified against one game build
> (dedicated server `24181105`, UE 5.1.1).

---

## Contents

- [Why this exists](#why-this-exists)
- [Install](#install)
- [Full migration walkthrough](#full-migration-walkthrough) ← start here
- [Moving LocalData.sav for map and fast travel](#moving-localdatasav-for-map-and-fast-travel)
- [Verification checklist](#verification-checklist)
- [Troubleshooting](#troubleshooting)
- [Command reference](#command-reference)
- [The Pal type marker](#the-pal-type-marker)
- [Safety](#safety)
- [Known limitations](#known-limitations)

---

## Why this exists

Palworld hardcodes the co-op host's player id as
`00000000000000000000000000000001`. A dedicated server derives a unique id per
account instead. Copy the world across without remapping that id and **you**
spawn as a fresh level 1 next to your own untouched bases.

Everyone else in the world is fine automatically — their save files are already
named by a PlayerUId the server matches on its own. It's only the host who
breaks.

The tools usually recommended for this no longer work on current saves:

| | |
|---|---|
| `cheahjs/palworld-save-tools` | last commit 2024-10-05; zlib (`PlZ`) only |
| `xNul/palworld-host-save-fix` | pins palworld-save-tools v0.17.1 (Feb 2024) |
| Upstream Oodle support | an open PR, raised Jun 2025, unmerged |

Current Palworld writes `PlM` (Oodle) containers, so both fail before they
start.

**How this one reads modern saves.** Oodle is proprietary and the open-source
`ooz` binding decompresses only, so no tool can *write* `PlM`. That turns out
not to matter: the game still accepts legacy `PlZ` and rewrites it as `PlM` on
its next save. Verified by booting a server against a re-encoded world — it
loaded, kept the world, and re-emitted `PlM1` with an identical payload length.

**How it finds player ids.** Not by searching for bytes. The co-op host id is
twelve zero bytes then `int32` 1, indistinguishable from ordinary padding — on
a real save it matches **2,904** times while genuine player ids match 174 and
56. `palmigrate` reports only fields the parser identified as a `Guid`: **275
genuine references, 2,629 false positives avoided.**

---

## Install

```bash
pip install palmigrate[oodle]
```

`[oodle]` pulls in `pyooz`, needed to read `PlM` saves. Prebuilt wheels exist
for Windows, Linux (glibc and musl) and macOS on x86-64 and ARM64, so no
compiler is required.

From source:

```bash
git clone https://github.com/haggyroth/palworld-coop-migrator
cd palworld-coop-migrator
pip install -e ".[oodle,dev]"
pytest
```

---

## Full migration walkthrough

You need: your co-op save folder, a dedicated server, and about twenty minutes.

### 1. Back up your co-op save

Do this first and do not skip it.

```bash
# Windows
Compress-Archive -Path "$env:LOCALAPPDATA\Pal\Saved\SaveGames\<SteamID64>\<world>\*" `
                 -DestinationPath "coop-backup.zip"
```

Your co-op save normally lives at:

```
%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID64>\<32-char world id>\
```

**Never point `palmigrate` at this folder as its output.** It only ever reads
the source, but keep an untouched archive regardless.

### 2. Confirm what you have

```bash
palmigrate inspect "path/to/coop-world"
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

`PlM` means Oodle — the older tools cannot read this. The file named
`00000000000000000000000000000001.sav` is **you**, the co-op host.

### 3. Set up the dedicated server and start it once

Install the server (SteamCMD app id `2394010`, anonymous login works), start it
once so it generates `Pal/Saved/SaveGames/0/<world>/`, then stop it.

### 4. Recreate your co-op world's settings

A dedicated server **ignores `WorldOption.sav`** and reads
`PalWorldSettings.ini` instead. Rather than guessing your old rates, read them
out of the co-op save:

```bash
palmigrate settings "coop-world/WorldOption.sav" \
    --default-ini "PalServer/DefaultPalWorldSettings.ini" \
    --set 'ServerName=My Server' \
    --set 'ServerPassword=something' \
    --set 'AdminPassword=something-long' \
    --set 'Difficulty=None' \
    -o "PalServer/Pal/Saved/Config/WindowsServer/PalWorldSettings.ini"
```

Two things worth knowing:

- `OptionSettings=(...)` must be **one single line**. One stray parenthesis
  makes the server silently discard *every* setting and fall back to stock,
  with no error. `palmigrate` validates this before writing.
- Set **`Difficulty=None`** on a dedicated server. It means "use the explicit
  rate list below". A co-op world records the preset it was created with
  (e.g. `Normal`), and carrying that across risks the preset overriding values
  you customised.

⚠️ Also check **`bAutoResetGuildNoOnlinePlayers`**. If it's `True`, the server
dissolves guilds once nobody has logged in for `AutoResetGuildTimeNoOnlinePlayers`
hours (72 by default). Harmless in co-op, where the world only runs while you
play — but on a dedicated server that sits idle between sessions it can
dissolve your guild and orphan every base.

### 5. Get your new PlayerUId

This is the id the remap targets, and it only exists once you've joined.

1. Start the dedicated server
2. Join it with your own Steam account
3. Create a throwaway character — **don't invest anything in it**, the
   migration overwrites it
4. Quit, and stop the server
5. Look in `Pal/Saved/SaveGames/0/<world>/Players/`

The new `.sav` filename **is** your PlayerUId:

```
Players/A1B2C3D4000000000000000000000000.sav   ← this 32-char name
```

### 6. Run the migration

Always dry-run first:

```bash
palmigrate migrate "coop-world/" "migrated/" --new A1B2C3D4000000000000000000000000 --dry-run
```

Then for real:

```bash
palmigrate migrate "coop-world/" "migrated/" --new A1B2C3D4000000000000000000000000
```

```
files written:
  remapped   Level.sav  (302 references)
  copied     LevelMeta.sav  (no references)
  renamed    A1B2C3D4….sav  (was 00000000000000000000000000000001.sav, 2 references)
  copied     E5F60718….sav  (not the host)
  copied     E5F60718…_dps.sav  (not the host)
  excluded   WorldOption.sav  (a dedicated server reads PalWorldSettings.ini; this would override it)
  excluded   LocalData.sav  (client-side discovery data; the server never reads it)

302 reference(s) remapped, 99 Pal type marker(s) left alone

structural references to the old id : 0
structural references to the new id : 302
Pal type markers left untouched     : 99

PASS: old id gone, entities and Pal markers intact

STILL TO DO BY HAND:
  - Copy migrated/../client/LocalData.sav onto the PLAYER'S OWN machine…
```

The source folder is never written to. Exit code `2` means the plan was unsafe
and **nothing was written**; `3` means validation failed after writing.

### 7. Install it on the server

With the server **stopped**:

1. Delete the contents of `Pal/Saved/SaveGames/0/<world>/` — including the
   throwaway `Players/*.sav`
2. Copy in everything from `migrated/`
3. Confirm **no `WorldOption.sav`** is present in the server world folder

Start the server. It will load the world and re-save it as `PlM1` — that's
expected and correct.

### 8. Move `LocalData.sav` — see the next section

Skipping this is the single most confusing way to end up with a "broken"
migration that is actually fine.

---

## Moving LocalData.sav for map and fast travel

**This file does not go on the server.** A dedicated server never reads or
writes it — verified by dropping it into a server world folder and watching the
server leave its timestamp untouched across a full load-and-save cycle. Each
**client** keeps its own copy on their own PC.

### What happens if you skip it

Everything looks right — character, level, inventory, Pals, guild, bases — but:

- your map is unexplored
- most fast-travel points read as **locked**

even though the server-side unlock flags are correct and complete. In Palworld
a fast-travel statue you haven't *discovered* displays as locked regardless of
whether you hold the unlock flag, and discovery is client-side.

### The move, step by step

Do this **on the machine you play on**, not the server.

1. **Quit Palworld completely.** The client rewrites this file on exit, so it
   must not be running.

2. Open your client save folder:

   ```bash
   explorer "%LOCALAPPDATA%\Pal\Saved\SaveGames"
   ```

3. Go into your **SteamID64 folder** (a long number). Inside is one folder per
   world you've played.

4. Identify the **new dedicated-server folder**. Sort by *Date modified* — it's
   the one created when you first joined the server in step 5. It is **not**
   your old co-op world folder, which has the same name as the co-op save
   directory you migrated from.

5. **Rename the existing `LocalData.sav` to `LocalData.sav.bak`.** Don't delete
   it — that's your rollback.

6. Copy in the `LocalData.sav` that `palmigrate migrate` placed in its
   `client/` folder (next to your output directory).

7. Launch Palworld and rejoin. Your map and fast-travel points should be back.

If it doesn't work, restore the `.bak` and you're exactly where you started.
Nothing here can affect the server.

### For your friends

In co-op, **only the host's `LocalData.sav` lives in the world folder.** Your
friends' discovery data was always on their own machines, under *their* co-op
world folder.

So each friend does the same move, using their own file:

```
FROM: %LOCALAPPDATA%\Pal\Saved\SaveGames\<their SteamID64>\<old co-op world>\LocalData.sav
TO:   %LOCALAPPDATA%\Pal\Saved\SaveGames\<their SteamID64>\<new server world>\LocalData.sav
```

Same rules: quit the game first, back up the file already there.

### What's in it

| Field | Typical size | What it holds |
|---|---|---|
| `Local_HiddenLocationFlagMap` | 125 entries | discovered locations (map fog) |
| `Local_NewUnlockedTechs` | 227 entries | unlocked tech notifications |
| `Local_PalEncountFlag` | 101 entries | Pals you've encountered |
| `WorldMapUISaveDataMap` | 2 entries | world map UI state |

---

## Verification checklist

Run through this on your first join after migrating.

- [ ] You spawn as **your character**, at the right level — not a new one
- [ ] Inventory, gear and technology points intact
- [ ] **Palbox contents** — full roster, correct levels
- [ ] Guild exists and you're a member with the right rank
- [ ] Bases present with all structures
- [ ] ⚠️ **Base Pals are actively working**, not standing idle — this is what
      catches an incomplete remap
- [ ] Chests and containers open without a permission error
- [ ] Map explored and fast-travel points unlocked *(after the `LocalData.sav`
      move)*
- [ ] Each friend can join and their character is intact

If base Pals are idle or chests are locked, **stop playing** and restore your
backup — continuing writes new data on top of broken ownership records.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Map unexplored, fast travel locked, everything else fine | `LocalData.sav` not moved — see above |
| You spawn as a new level 1 | The remap didn't run, or `--new` was the wrong id |
| **All Pals missing** after first load | The Pal type marker was rewritten — see below. Restore and re-run with a current `palmigrate` |
| Base Pals idle, chests locked | Incomplete remap: ownership references missed |
| Every setting reverted to stock | Malformed `OptionSettings=(...)`, or `WorldOption.sav` was copied to the server and overrode the ini |
| Guild vanished after a few days | `bAutoResetGuildNoOnlinePlayers=True` with an idle server |
| Item/container counts dropped after first load | **Normal.** The server garbage-collects unused containers — an untouched world does it too |

That last row is worth stressing: seeing `ItemContainerSaveData` fall from 1,741
to 643 looks alarming and is not damage. Judge a migration by **character and
Pal counts**, not container counts.

---

## Command reference

### `palmigrate migrate`

```bash
palmigrate migrate SOURCE DESTINATION --new <PlayerUId> [--old <PlayerUId>] [--dry-run] [--force]
```

| Flag | Meaning |
|---|---|
| `--new` | **Required.** Your dedicated-server PlayerUId (the filename from `Players/`) |
| `--old` | The id being replaced. Defaults to the co-op host id |
| `--dry-run` | Print the plan, write nothing |
| `--force` | Overwrite an existing destination |

### `palmigrate inspect`

```bash
palmigrate inspect <file-or-world-dir> [-v]
```

Reports each save's container format and size. `-v` adds the GVAS header.

### `palmigrate scan`

```bash
palmigrate scan <Level.sav> [--guid <id>] [--reference <id>]...
```

Shows how many times an id appears as a raw byte pattern versus how often real
player ids do, and gives a verdict. Exits `2` when byte replacement is unsafe,
so it can gate a script.

### `palmigrate settings`

```bash
palmigrate settings <WorldOption.sav> [--default-ini <path>] [--set K=V]... [-o <path>]
```

Without `--default-ini` it prints the co-op world's settings. With it, renders a
validated `PalWorldSettings.ini`. Overrides inherit the shipped default's
quoting, so a shell that strips quotes can't produce a malformed line.

### `palmigrate convert`

```bash
palmigrate convert <in.sav> <out.sav> [--force]
```

Re-encodes `PlM` → `PlZ` and verifies the payload round-trips byte-for-byte.

---

## The Pal type marker

The most destructive trap in this migration, and worth understanding even if
you use another tool.

`CharacterSaveParameterMap` keys hold `00000000000000000000000000000001` for
**every Pal** — byte-identical to the co-op host's PlayerUId, but meaning
*"this row is a Pal"*, not *"this Pal belongs to the host"*. The proof: Pals
owned by other players carry it too.

Rewrite it and the server stops recognising those rows as Pals and **deletes
them on load**. Bisected against a real world:

| What was remapped | Refs | Characters after load |
|---|---|---|
| nothing (control) | 0 | 102 → 102 |
| everything | 401 | 102 → **3** |
| only `key.PlayerUId` | 100 | 102 → **3** |
| everything except `key.PlayerUId` | 301 | 102 → 102 |
| correct: keys only where `IsPlayer` | 302 | 102 → 102 |

`palmigrate` classifies each entry by `RawData.SaveParameter.IsPlayer` and keeps
markers in `WalkResult.pal_sentinels`, outside the remappable set.

**The obvious validation does not catch this.** "No reference to the old id
survives" *passes* on the destroyed save — the rewrite genuinely completed.
Entity counts don't catch it either, because the file still parses with every
row present and the deletion happens later, inside the game. So validation also
compares the **Pal marker count** before and after.

---

## Safety

- Every operation reads its input and writes somewhere else. The source world
  is never modified.
- Nothing is written unless the plan is safe *and* post-write validation passes.
- Planning **refuses outright** if a region that couldn't be decoded still
  contains the old id, rather than producing a partial remap.
- Every written save is read back and compared before being kept.
- The container decoder validates declared lengths, guards against absurd
  allocations, and rejects payloads that aren't GVAS.
- The GVAS reader fails loudly on desync rather than returning plausible
  garbage.

**Back up your save before using any tool on it, including this one.**

---

## Known limitations

- A few `RawData` blobs (`BaseCampSaveData`, some `ItemContainerSaveData`
  entries) are not decoded. They are **reported rather than silently skipped**,
  and were verified to contain no player ids in the save this was developed
  against. If yours does contain one, the migration refuses instead of
  producing a partial result.
- `LocalData.sav` has to be placed on each client by hand.
- Verified against one game build (dedicated server `24181105`, UE 5.1.1).
  Palworld changes its save format between updates; re-check before trusting
  this on a newer build.
- Only the co-op host is remapped. Other players migrate automatically.

---

## Privacy

No game save data is committed to this repository. Saves contain player names
and account-derived identifiers, so the test suite builds synthetic GVAS
fixtures byte-by-byte instead, and every player id in these docs is invented.
Occurrence counts quoted here are real measurements, but they're aggregate
statistics rather than identifiers.

## Acknowledgements

The GVAS structure work by [cheahjs/palworld-save-tools](https://github.com/cheahjs/palworld-save-tools)
and the original host-fix approach from [xNul/palworld-host-save-fix](https://github.com/xNul/palworld-host-save-fix)
mapped out this territory first. This project exists because both stopped
tracking the game, not because they were wrong.

Oodle decompression via [pyooz](https://pypi.org/project/pyooz/), which binds
the open-source `ooz` reimplementation.

Format notes and reverse-engineering detail: [docs/save-format.md](docs/save-format.md).

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Pocketpair, Inc.
