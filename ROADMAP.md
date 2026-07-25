# Roadmap

## Done

- [x] `PlM` (Oodle) container decoding via `ooz`
- [x] `PlZ` encoding, exploiting the game's acceptance of legacy containers
- [x] Round-trip verification (payload must survive decode → encode → decode)
- [x] GVAS header and property-tree reader with the corrected Palworld tag layout
- [x] GUID conversion, validation, and low-entropy detection
- [x] Collision analysis proving byte-level replacement is unsafe
- [x] `WorldOption.sav` settings extraction (all 119 keys)
- [x] `PalWorldSettings.ini` rendering with single-line paren validation
- [x] CLI: `inspect`, `scan`, `settings`, `convert`
- [x] Test suite on synthetic fixtures — no real save data in the repo
- [x] Format documentation
- [x] CI across Windows, Linux and macOS on Python 3.9–3.12
- [x] Coverage gate at 90% (currently ~95% with branch coverage)
- [x] CodeQL analysis and Dependabot
- [x] Branch protection on `main` with required status checks

## Next — the structural remap

This is the feature that makes the tool able to finish a migration.

- [ ] Decode `MapProperty` in the GVAS reader (key/value type tags, entry counts)
- [ ] Walk `Level.sav` `worldSaveData` and locate the player-bearing maps:
      `CharacterSaveParameterMap`, `GroupSaveDataMap`, `CharacterContainerSaveData`,
      `ItemContainerSaveData`, `BaseCampSaveData`
- [ ] Decode the custom `RawData` blobs those maps hold
- [ ] Remap the host GUID across all five surfaces:
  - [ ] character entry (`CharacterSaveParameterMap` key `PlayerUId`)
  - [ ] guild membership (`GroupSaveDataMap` → players list)
  - [ ] guild admin / owner (`admin_player_uid`)
  - [ ] storage container locks
  - [ ] Pal ownership (`OwnerPlayerUId`, `OldOwnerPlayerUIds`)
- [ ] Rename `Players/<old>.sav` and the `_dps.sav` sidecar, remapping their contents
- [ ] Write a dry-run mode reporting every field that would change, before changing any
- [ ] Post-migration validator: re-open the output and assert no references to
      the old GUID survive anywhere

### Verification target

An incomplete remap does not fail loudly. The character loads and looks correct
while base Pals stand idle and chests refuse to open, because ownership and
lock records still point at the old id. The validator above is the guard
against shipping that.

## Later

- [ ] `migrate` command tying the whole flow together end to end
- [ ] Handle a co-op world with multiple non-host players in one pass
- [ ] Optional JSON dump / reload for manual inspection and editing
- [ ] Detect and warn on `bAutoResetGuildNoOnlinePlayers`, which can dissolve a
      guild on a server that sits idle between sessions
- [ ] Test against saves from more than one game build to catch layout drift
- [ ] Publish to PyPI

## Non-goals

- Writing `PlM` (Oodle) containers. No open-source Kraken compressor exists,
  and the game upgrades `PlZ` on its own, so this is unnecessary.
- A general-purpose Palworld save editor. `cheahjs/palworld-save-tools` is the
  right home for that if it resumes development.
- Bundling or redistributing proprietary Oodle binaries.
