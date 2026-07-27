# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- feat: `palmigrate migrate` — end-to-end migration of a co-op world folder onto
  a dedicated server. Plans and validates before writing, never touches the
  source, renames the host's player file (carrying `_dps` sidecars), leaves
  `WorldOption.sav` behind because it would override `PalWorldSettings.ini`, and
  copies `LocalData.sav` out to a separate `client/` folder with instructions
  since it belongs on the player's own machine. `--dry-run` shows the plan and
  writes nothing; exit 2 means the plan was unsafe and nothing was written

## [0.4.0] - 2026-07-27

First tagged release, and the first that can complete a migration.

Verified end to end on a real co-op world moved to a dedicated server: 102
characters, 99 Pals with correct owners, guild membership, bases and map
objects all intact.

### Added

- feat: `locate` module finds player ids **structurally** — it reports only
  fields the parser identified as a `Guid`, never a byte-pattern match. On a
  real save that is 275 genuine references against 2,904 raw matches, avoiding
  2,629 false positives. Each reference carries an absolute byte offset, so a
  remap is a length-preserving in-place overwrite and untouched bytes stay
  untouched
- feat: `remap` module with planning, application and validation. Planning
  refuses outright when a region it could not decode still contains the old id
- feat: Pal type marker classification via `RawData.SaveParameter.IsPlayer`,
  keeping markers in `WalkResult.pal_sentinels` outside the remappable set
- feat: validation covering entity counts and Pal-marker preservation, not just
  "no old reference survives"
- feat: `MapProperty` and `SetProperty` tag support. Their tags carry extra type
  strings; without them the reader desynchronised on the first map and
  `Level.sav` could not be parsed at all
- feat: `ArrayProperty<StructProperty>` support, needed for `OldOwnerPlayerUIds`
- feat: `UInt32Property` and `UInt64Property`
- feat: `Reader` carries a base offset so references found inside a nested
  `RawData` blob resolve to the right place in the file
- test: CLI suite covering every command, exit code and error path
- test: coverage for the GVAS property types a real `Level.sav` uses
- ci: coverage gate at 90% with branch coverage, CodeQL, Dependabot, and
  `vermin` asserting the codebase runs on the oldest advertised Python
- docs: `Level.sav` structure, the Pal type marker with the bisect table that
  isolated it, and `LocalData.sav` being client-side

### Fixed

- fix: **do not rewrite the Pal type marker.** `CharacterSaveParameterMap` keys
  hold `00000000000000000000000000000001` on every Pal — byte-identical to the
  co-op host's PlayerUId, but a type marker rather than an owner, as proven by
  Pals owned by other players carrying it too. Rewriting it made the server
  delete the Pals on load, taking a real world from 102 characters to 3
- fix: `palmigrate settings -o` raised `TypeError` on Python 3.9. It used
  `Path.write_text(newline=...)`, which is 3.10+, while the package advertises
  3.9 support
- fix: `ooz.decompress` raises a bare `RuntimeError`; it is now wrapped so
  callers only ever handle `PalMigrateError`
- fix: `--set` overrides inherit the shipped default's quoting, so a shell that
  strips quotes cannot silently produce a malformed settings line

### Changed

- ci: `actions/checkout` v5→v7, `actions/setup-python` v6→v7,
  `github/codeql-action` v3→v4, clearing the Node.js 20 deprecation warning
- ci: workflows declare least-privilege `permissions` blocks

### Known limitations

- No single end-to-end `migrate` command yet; the pieces are composable but
  driving them is manual
- Custom `RawData` blobs in `BaseCampSaveData` and a few `ItemContainerSaveData`
  entries are not decoded. They are reported rather than skipped, and were
  verified to contain no player ids in the save this was developed against
- `LocalData.sav` must be placed on the player's own machine by hand
- Verified against one game build (dedicated server `24181105`, UE 5.1.1)

### Note on earlier versions

Versions 0.1.0 through 0.3.1 were development milestones and were never tagged
or published. `0.4.0` is the first release.

[Unreleased]: https://github.com/haggyroth/palworld-coop-migrator/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/haggyroth/palworld-coop-migrator/releases/tag/v0.4.0
