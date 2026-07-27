# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- fix: **do not rewrite the Pal type marker.** `CharacterSaveParameterMap` keys
  hold `00000000000000000000000000000001` on every Pal — byte-identical to the
  co-op host's PlayerUId, but a type marker rather than an owner (Pals owned by
  other players carry it too). Rewriting it made the server delete the Pals on
  load, taking a real world from 102 characters to 3. Entries are now classified
  by `RawData.SaveParameter.IsPlayer`, and markers are kept in
  `WalkResult.pal_sentinels`, outside the remappable set

### Added

- feat: `remap.entity_counts` / `compare_entity_counts`, and a Pal-marker
  before/after check in `validate()`. The previous validation — "no reference to
  the old id survives" — **passed** on the save with every Pal destroyed,
  because the rewrite really had completed. Only the marker count reveals it
  while it is still a file; the deletion itself happens later, inside the game
- docs: the Pal type marker, with the bisect table that isolated it
- docs: `LocalData.sav` is client-side. A dedicated server never reads or writes
  it (verified: the server left its timestamp untouched across a full
  load-and-save). Omitting it costs map exploration and makes fast-travel points
  read as locked even though the server-side unlock flags are correct

- feat: `locate` module finds player ids **structurally**, reporting only
  fields the parser identified as a `Guid`. On a real save that is 275 genuine
  references against 2,904 byte-pattern matches — 2,629 false positives
  avoided. Each reference carries an absolute byte offset, so a remap is a
  length-preserving in-place overwrite
- feat: `ArrayProperty<StructProperty>` support, needed for `OldOwnerPlayerUIds`
- feat: `Reader` carries a base offset so references found inside a nested
  `RawData` blob point at the right place in the file

- feat: `MapProperty` and `SetProperty` tag support in the GVAS reader. Their
  tags carry extra type strings; without them the reader desynchronised on the
  first map, so `Level.sav` could not be parsed at all. All eight files of a
  real save now parse with no unhandled property types
- feat: `UInt32Property` and `UInt64Property`
- docs: `Level.sav` top-level structure and the location of the five surfaces a
  host remap must cover

- test: CLI test suite covering every command, exit code and error path
  (`cli.py` went from 0% to 97% coverage)
- test: coverage for the GVAS property types a real `Level.sav` uses but the
  WorldOption fixture never exercised, including opaque
  `ArrayProperty<ByteProperty>` blobs and unknown property types
- ci: coverage job with branch coverage and a 90% floor, reported to the job
  summary
- ci: CodeQL security analysis on push, pull request and weekly schedule
- chore: Dependabot for GitHub Actions and pip, grouped and weekly

### Fixed

- fix: `palmigrate settings -o` raised `TypeError` on Python 3.9. It used
  `Path.write_text(newline=...)`, which is 3.10+, while the package advertises
  3.9 support. Found by the new CLI tests running against the CI matrix.

### Changed

- ci: `actions/checkout` v4 to v5 and `actions/setup-python` v5 to v6, clearing
  the Node.js 20 deprecation warning on every run
- ci: workflows now declare a least-privilege `permissions` block
- ci: `vermin` asserts the codebase runs on the oldest advertised Python
  statically, rather than relying on a test happening to execute the line

## [0.1.0] - 2026-07-25

Initial release. Reads modern Oodle saves; does not yet perform the host GUID
remap.

### Added

- feat: `PlM` (Oodle) container decoding via the open-source `ooz` bindings
- feat: `PlZ` container encoding, relying on the game upgrading legacy
  containers to `PlM` on its next save
- feat: GVAS header and property-tree reader using Palworld's actual tag layout
  (`int64` size, no `ArrayIndex` field)
- feat: GUID conversion between text and little-endian `FGuid` bytes, with
  low-entropy detection
- feat: collision analysis quantifying why byte-level GUID replacement is unsafe
- feat: `WorldOption.sav` settings extraction
- feat: `PalWorldSettings.ini` rendering with single-line, quote-aware
  parenthesis validation
- feat: CLI with `inspect`, `scan`, `settings` and `convert` commands
- test: suite built on synthetic GVAS fixtures, so no game save data is
  committed to the repository
- docs: `docs/save-format.md` covering the container layout, the `PlZ` upgrade
  behaviour, the corrected property tag layout, and the GUID collision data

### Known limitations

- The host GUID remap is not implemented. This release cannot complete a
  migration on its own.
- Custom `RawData` blobs inside `Level.sav` are not decoded.
- Verified against one game build (dedicated server `24181105`, UE 5.1.1).

[Unreleased]: https://github.com/haggyroth/palworld-coop-migrator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/haggyroth/palworld-coop-migrator/releases/tag/v0.1.0
