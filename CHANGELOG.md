# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
