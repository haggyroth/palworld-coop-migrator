"""
End-to-end migration of a co-op world folder onto a dedicated server.

Reads a co-op save, remaps the host's player id everywhere it genuinely
appears, and writes a complete world folder ready to drop into
``SaveGames/0/``. The source is never modified and never written to.

What is deliberately left behind
--------------------------------
``WorldOption.sav``
    A dedicated server ignores it and reads ``PalWorldSettings.ini`` instead.
    Copying it across silently overrides the ini. Use ``palmigrate settings``
    to turn the co-op values into an ini rather than carrying the file.

``LocalData.sav``
    Per-player *discovery* state: map exploration, hidden locations, unlocked
    tech notifications. A dedicated server never reads or writes it -- each
    client keeps its own copy under ``%LOCALAPPDATA%``. It cannot go in the
    server folder, but it must not simply be dropped either: without it the
    player's map is unexplored and fast-travel points read as locked, even
    though the server-side unlock flags are correct. :func:`migrate` copies it
    out to ``client/LocalData.sav`` with instructions instead.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import container, locate, remap
from . import guid as guid_mod
from .errors import PalMigrateError
from .gvas import parse

#: Files that must not be carried into a server world folder.
EXCLUDED_FROM_SERVER = {
    "WorldOption.sav": "a dedicated server reads PalWorldSettings.ini; this would override it",
    "LocalData.sav": "client-side discovery data; the server never reads it",
}

#: Suffix Palworld uses for the per-player Dimension Pal Storage sidecar.
DPS_SUFFIX = "_dps"


@dataclass
class FileOutcome:
    """What happened to one file."""

    name: str
    action: str
    detail: str = ""


@dataclass
class MigrationResult:
    """Everything the migration did, and anything the caller must still do."""

    files: list[FileOutcome] = field(default_factory=list)
    plan: remap.RemapPlan | None = None
    report: remap.ValidationReport | None = None
    client_localdata: Path | None = None
    manual_steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.plan is not None
            and self.plan.is_safe
            and self.report is not None
            and self.report.is_clean
        )

    def summary(self) -> str:
        lines = ["files written:"]
        for f in self.files:
            suffix = f"  ({f.detail})" if f.detail else ""
            lines.append(f"  {f.action:<10} {f.name}{suffix}")
        if self.plan is not None:
            lines.append("")
            lines.append(
                f"{len(self.plan.refs)} reference(s) remapped, "
                f"{self.plan.skipped_sentinels} Pal type marker(s) left alone"
            )
        if self.report is not None:
            lines.append("")
            lines.append(self.report.summary())
        if self.manual_steps:
            lines.append("")
            lines.append("STILL TO DO BY HAND:")
            for step in self.manual_steps:
                lines.append(f"  - {step}")
        return "\n".join(lines)


def _collect_guids(node: Any, path: str, out: list[tuple[str, int, str]]) -> None:
    if isinstance(node, dict):
        if "__struct_type__" in node:
            if node["__struct_type__"] == "Guid" and "__offset__" in node:
                out.append((path, node["__offset__"], guid_mod.from_bytes(node["__raw__"])))
            return
        if any(k in node for k in ("__map__", "__array_of__", "__unparsed__", "__set_of__")):
            return
        for key, value in node.items():
            _collect_guids(value, f"{path}.{key}" if path else key, out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect_guids(value, f"{path}[{index}]", out)


def _remap_plain_save(payload: bytes, old: str, new: str) -> tuple[bytes, int]:
    """
    Rewrite Guid fields holding ``old`` in a save with no character map.

    Used for player saves and LevelMeta. These have no Pal entries, so every
    Guid holding the old id really is a player reference.
    """
    _, props = parse(payload)
    found: list[tuple[str, int, str]] = []
    _collect_guids(props, "", found)

    old_bytes = guid_mod.to_bytes(old)
    new_bytes = guid_mod.to_bytes(new)
    buffer = bytearray(payload)
    changed = 0
    for _path, offset, value in found:
        if value != old:
            continue
        if bytes(buffer[offset : offset + 16]) != old_bytes:
            raise PalMigrateError(f"offset {offset} no longer holds the old id")
        buffer[offset : offset + 16] = new_bytes
        changed += 1

    result = bytes(buffer)
    if changed:
        _, after = parse(result)
        recheck: list[tuple[str, int, str]] = []
        _collect_guids(after, "", recheck)
        if any(v == old for _, _, v in recheck):
            raise PalMigrateError("old id survives in a player save after remap")
    return result, changed


def _write_verified(path: Path, payload: bytes) -> None:
    """Write a save and read it back, refusing to leave a bad file behind."""
    container.write(path, payload)
    if container.read(path).payload != payload:
        path.unlink(missing_ok=True)
        raise PalMigrateError(f"{path.name} did not survive a round trip; discarded")


def migrate(
    source: str | Path,
    destination: str | Path,
    old_guid: str,
    new_guid: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> MigrationResult:
    """
    Build a dedicated-server world from a co-op save folder.

    ``source`` is never written to. ``destination`` must not already exist
    unless ``force`` is set.
    """
    src = Path(source)
    dst = Path(destination)
    old = guid_mod.normalise(old_guid)
    new = guid_mod.normalise(new_guid)
    result = MigrationResult()

    level_path = src / "Level.sav"
    if not level_path.is_file():
        raise PalMigrateError(f"no Level.sav in {src}")
    if old == new:
        raise PalMigrateError("the old and new ids are identical")

    # --- plan against the world, before touching anything ------------------
    level = container.read(level_path)
    _, props = parse(level.payload)
    world = props.get("worldSaveData")
    if not isinstance(world, dict):
        raise PalMigrateError("Level.sav has no worldSaveData")

    counts_before = remap.entity_counts(world)
    sentinels_before = len(locate.walk(level.payload, world).pal_sentinels)

    plan = remap.plan(level.payload, world, old, new)
    result.plan = plan
    if not plan.is_safe:
        return result

    if dry_run:
        result.manual_steps.append("dry run: nothing was written")
        return result

    if dst.exists() and not force:
        raise PalMigrateError(f"{dst} already exists; pass force=True to overwrite")
    (dst / "Players").mkdir(parents=True, exist_ok=True)

    # --- Level.sav ---------------------------------------------------------
    patched = remap.apply(level.payload, plan)
    _, after_props = parse(patched)
    report = remap.validate(
        patched,
        after_props["worldSaveData"],
        old,
        new,
        expected_incidental=level.payload.count(guid_mod.to_bytes(old)) - len(plan.refs),
        counts_before=counts_before,
        sentinels_before=sentinels_before,
    )
    result.report = report
    if not report.is_clean:
        return result

    _write_verified(dst / "Level.sav", patched)
    result.files.append(FileOutcome("Level.sav", "remapped", f"{len(plan.refs)} references"))

    # --- LevelMeta.sav -----------------------------------------------------
    meta_path = src / "LevelMeta.sav"
    if meta_path.is_file():
        meta = container.read(meta_path)
        patched_meta, changed = _remap_plain_save(meta.payload, old, new)
        if changed:
            _write_verified(dst / "LevelMeta.sav", patched_meta)
            result.files.append(FileOutcome("LevelMeta.sav", "remapped", f"{changed} references"))
        else:
            shutil.copy2(meta_path, dst / "LevelMeta.sav")
            result.files.append(FileOutcome("LevelMeta.sav", "copied", "no references"))

    # --- Players -----------------------------------------------------------
    players = src / "Players"
    if players.is_dir():
        for path in sorted(players.glob("*.sav")):
            stem = path.stem
            base = stem[: -len(DPS_SUFFIX)] if stem.endswith(DPS_SUFFIX) else stem
            is_host = guid_mod.is_valid(base) and guid_mod.normalise(base) == old

            if not is_host:
                shutil.copy2(path, dst / "Players" / path.name)
                result.files.append(FileOutcome(path.name, "copied", "not the host"))
                continue

            new_name = new.upper() + (DPS_SUFFIX if stem.endswith(DPS_SUFFIX) else "")
            sav = container.read(path)
            patched_player, changed = _remap_plain_save(sav.payload, old, new)
            _write_verified(dst / "Players" / f"{new_name}.sav", patched_player)
            result.files.append(
                FileOutcome(
                    f"{new_name}.sav",
                    "renamed",
                    f"was {path.name}, {changed} references",
                )
            )

    # --- files that must not go to the server ------------------------------
    for name, why in EXCLUDED_FROM_SERVER.items():
        if (src / name).is_file():
            result.files.append(FileOutcome(name, "excluded", why))

    local = src / "LocalData.sav"
    if local.is_file():
        client_dir = dst.parent / "client"
        client_dir.mkdir(parents=True, exist_ok=True)
        target = client_dir / "LocalData.sav"
        shutil.copy2(local, target)
        result.client_localdata = target
        result.manual_steps.append(
            f"Copy {target} onto the PLAYER'S OWN machine, into "
            r"%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID64>\<new server world>\ "
            "(back up the one already there). Without it the map is unexplored "
            "and fast-travel points read as locked, even though the server-side "
            "unlock flags are correct."
        )

    result.manual_steps.append(
        "Generate PalWorldSettings.ini from the co-op WorldOption.sav with "
        "`palmigrate settings`, rather than copying WorldOption.sav across."
    )
    return result
