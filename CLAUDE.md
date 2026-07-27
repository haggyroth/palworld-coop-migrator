# CLAUDE.md

Guidance for Claude Code working in this repository.

`palmigrate` migrates a Palworld co-op world onto a dedicated server. The hard
part is remapping the co-op host's player id; the reason existing tools fail is
that current saves use Oodle compression they cannot read.

---

## Commands

```bash
pip install -e ".[oodle,dev]"     # [oodle] pulls pyooz; needed to read PlM saves

pytest                             # full suite, no native dependency required
pytest --cov=palmigrate --cov-fail-under=90    # what CI gates on
ruff check src tests
ruff format src tests
vermin --target=3.9 --violations --eval-annotations --no-tips src
```

---

## Hard rules

**1. Never commit save data or real player identifiers.**
`.sav` files are personal data: they carry player names and account-derived
ids. `.gitignore` blocks `*.sav`, but the rule extends to *pasting a real
PlayerUId into a doc, test, or commit message*. Use invented ids
(`a1b2c3d4000000000000000000000000` style: one populated `uint32`, twelve zero
bytes). This has already been violated once and required deleting and
recreating the repository, because rewriting history does not remove commits
GitHub has already made reachable.

**2. Never locate a player GUID by byte-pattern search.**
The co-op host id is twelve zero bytes then `int32` 1, which is
indistinguishable from ordinary padding followed by a count. On a real 5.4 MB
`Level.sav` it matches **2,904** times while genuine player ids match 174 and
56 — roughly 2,700 false positives. `scan.py` exists to make this measurable,
and `guid.entropy_warning()` to make it refusable. Any remap must walk the
structure and rewrite only fields that are genuinely `PlayerUId`.

**3. Python 3.9 is the floor.**
`pyproject.toml` advertises `>=3.9` and CI runs it. Do not reach for 3.10+
stdlib APIs. This already shipped a bug: `Path.write_text(newline=...)` is
3.10+, so `palmigrate settings -o` raised `TypeError` for 3.9 users. `vermin`
in the lint job now catches this statically — do not remove it. Use
`open(..., newline=...)` when newline control is needed.

**4. Writing `PlZ` instead of `PlM` is intentional, not a TODO.**
No open-source Oodle compressor exists, so we cannot emit `PlM`. The game
accepts legacy `PlZ` and rewrites it as `PlM` on its next save (verified). Do
not add a "write PlM" task; `container.encode()` raises deliberately.

---

## Editing files on Windows

Do not round-trip source files through PowerShell 5.1's
`Get-Content`/`Set-Content`. `Get-Content` misreads UTF-8 as ANSI, and
`Set-Content -Encoding utf8` writes a **BOM**. This has already corrupted em
dashes in the docs into `â€"` and, separately, put a BOM on `pyproject.toml`
that made every `pytest` run fail with `Invalid statement (at line 1,
column 1)`.

Use the editing tools directly. If a script must write a file, use
`[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`.

Also note `Set-Location` does not persist between tool calls here — use
absolute paths or `Push-Location`/`Pop-Location` within a single call.

---

## Layout

```
src/palmigrate/
  container.py   .sav container: decode PlM/PlZ, encode PlZ only
  gvas.py        UE5 GVAS property-tree reader (plain properties only)
  guid.py        PlayerUId text <-> FGuid bytes, low-entropy detection
  scan.py        GUID occurrence analysis and the safe/unsafe verdict
  settings.py    WorldOption.sav -> PalWorldSettings.ini
  cli.py         inspect / scan / settings / convert
  errors.py      all raises derive from PalMigrateError
docs/save-format.md   the reverse-engineering notes; read before touching gvas.py
```

Every public failure should surface as a `PalMigrateError` subclass. Third-party
exceptions get wrapped — `ooz.decompress` raises a bare `RuntimeError`, which
`container._oodle_decompress` catches so callers only handle one hierarchy.

---

## Format facts that are expensive to rediscover

Full detail in `docs/save-format.md`. The three that cost the most time:

**Palworld's property tag is not the stock `FPropertyTag`.** The size is an
`int64` and there is **no `ArrayIndex` field**:

```
FString name, FString type, int64 size, <type tag data>, uint8 guid_flag, <value>
```

Assuming the stock layout desynchronises on the first property and then tries
to read a 1.7 GB string.

**`PlZ2` headers record the *inner* zlib stage length**, not the bytes on disk.

**`Players/<uid>_dps.sav` sidecars** hold per-player Pal storage, split out of
`Level.sav`. Tools predating them silently drop Pals. Any migration must carry
and remap them.

---

## Testing

Fixtures are **synthetic GVAS built byte-by-byte** in `tests/conftest.py`
(`prop_int`, `prop_struct`, `prop_array_bytes`, …). Never add a real save file
as a fixture — see rule 1, and it makes tests assert the format rather than
echo a capture.

When testing a property type, append a **sentinel property after it** and
assert the sentinel still parses. A type that mis-advances the cursor then
fails loudly instead of returning plausible garbage:

```python
payload = gvas_header() + prop_under_test + prop_int("Sentinel", 999) + NONE
assert props["Sentinel"] == 999, "walk desynchronised"
```

Coverage is gated at 90% with branch coverage on (currently ~95%).

---

## CI and git workflow

`main` is protected: PRs required, force-push and deletion blocked, **14
required status checks** (test matrix across Windows/Linux/macOS × Python
3.9–3.12, both `oodle extra` and `lint` checks, `coverage`, `analyze python`).
`enforce_admins` is off so the owner can still land the version-bump commits
the workflow calls for.

Follow the repo owner's git workflow: branch `<type>/<desc>` off `main`,
Conventional Commits, a PR with What/Why/How-tested, **CI green before merge**,
merge commit titled `merge: <branch> (#<pr>)`.

Commit messages here carry the *why* — particularly measurements and
verification results, since much of this work is reverse engineering and the
evidence is the valuable part. Match that.

CodeQL runs the `security-and-quality` suite, whose quality queries are
somewhat false-positive prone. One dismissed FP so far
(`py/uninitialized-local-variable` on `container.py`). Verify before acting on
an alert; dismiss with a written reason rather than contorting correct code.

---

## Status

Working and tested: container codec, GVAS reader, GUID handling, collision
analysis, settings extraction and ini rendering, the four CLI commands.

**Not implemented: the structural host GUID remap** — the feature that lets the
tool actually finish a migration. It must cover all five surfaces (character
entry, guild membership, guild admin, container locks, Pal ownership) and
requires decoding Palworld's custom `RawData` blobs inside `Level.sav`, which
`gvas.py` deliberately leaves opaque today.

An incomplete remap does not fail loudly: the character loads and looks right
while base Pals stand idle and chests refuse to open. Plan for a dry-run mode
and a post-migration validator asserting no reference to the old id survives.
See `ROADMAP.md`.
