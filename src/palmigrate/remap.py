"""
Rewrite one player id as another, in place.

Player ids are fixed-width ``Guid`` structs, so a remap overwrites 16 bytes at
each located offset and changes nothing else. The payload is never
re-serialised: bytes we did not decode cannot be disturbed, and the file length
is identical by construction.

There are two ways to get this wrong, and they pull in opposite directions.

Remap *too little* and the result is a partial migration: the character loads
looking correct while base Pals stand idle and chests refuse to open. Planning
guards that by refusing outright if a region we could not decode still holds
the old id's byte pattern.

Remap *too much* and you destroy the save. Palworld marks Pal entries with the
constant ``00000000000000000000000000000001`` in their map key -- the same
bytes as the co-op host's PlayerUId. Rewriting those tells the server the
entries are no longer Pals and it deletes them on load. This is not
hypothetical: it took a real world from 102 characters to 3.

The second failure is the nastier one, because the obvious check passes. "No
reference to the old id survives" proves the *rewrite* finished; it says
nothing about whether the result still means anything to the game. So
validation also compares entity counts and, decisively, the number of Pal type
markers before and after -- see :attr:`ValidationReport.sentinels_intact`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import guid as guid_mod
from . import locate
from .errors import PalMigrateError

GUID_SIZE = 16


@dataclass
class RemapPlan:
    """What a remap would change, and why it is or is not safe to run."""

    old_guid: str
    new_guid: str
    refs: list[locate.GuidRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Pal type markers that share the old id's bytes and must NOT be rewritten.
    skipped_sentinels: int = 0

    @property
    def is_safe(self) -> bool:
        return not self.blockers and bool(self.refs)

    def by_surface(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ref in self.refs:
            # collapse the entry index so surfaces group together
            head, _, rest = ref.path.partition("[")
            _, _, field_path = rest.partition("].")
            key = f"{head}.{field_path}" if field_path else head
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary(self) -> str:
        lines = [
            f"remap {self.old_guid}",
            f"   -> {self.new_guid}",
            "",
            f"{len(self.refs)} reference(s) would be rewritten:",
        ]
        for surface, count in self.by_surface().items():
            lines.append(f"  {count:>5}  {surface}")
        if self.skipped_sentinels:
            lines.append("")
            lines.append(
                f"{self.skipped_sentinels} Pal type marker(s) share these bytes and are "
                "deliberately NOT rewritten."
            )
            lines.append("Rewriting them makes the server delete every Pal on load.")
        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"WARNING: {w}")
        if self.blockers:
            lines.append("")
            for b in self.blockers:
                lines.append(f"BLOCKED: {b}")
            lines.append("")
            lines.append("Refusing to remap. A partial rewrite is worse than none.")
        return "\n".join(lines)


def plan(payload: bytes, world: dict[str, Any], old_guid: str, new_guid: str) -> RemapPlan:
    """Work out every byte that would change, and whether it is safe to do it."""
    old = guid_mod.normalise(old_guid)
    new = guid_mod.normalise(new_guid)
    result = RemapPlan(old_guid=old, new_guid=new)

    if old == new:
        result.blockers.append("the old and new ids are identical")
        return result

    walk = locate.walk(payload, world)
    result.refs = [r for r in walk.refs if r.value == old]
    result.skipped_sentinels = sum(1 for r in walk.pal_sentinels if r.value == old)

    if not result.refs:
        result.blockers.append(f"no references to {old} were found")

    existing_new = [r for r in walk.refs if r.value == new]
    if existing_new:
        result.blockers.append(
            f"{new} already appears {len(existing_new)} time(s) in this save; "
            "remapping onto it would merge two identities"
        )

    old_bytes = guid_mod.to_bytes(old)

    # Anything we could not decode must be proven free of the old id, or the
    # remap would silently leave references behind.
    for region in walk.opaque:
        blob = payload[region.offset : region.offset + region.length]
        hits = blob.count(old_bytes)
        if hits:
            result.blockers.append(
                f"{region.path} ({region.length} bytes) could not be decoded "
                f"and contains the old id {hits} time(s)"
            )
    for note in walk.undecoded:
        result.warnings.append(f"undecoded: {note}")

    # Belt and braces: every located offset must actually hold the old bytes.
    for ref in result.refs:
        actual = payload[ref.offset : ref.offset + GUID_SIZE]
        if actual != old_bytes:
            result.blockers.append(
                f"{ref.path} at offset {ref.offset} does not hold the old id; "
                "the offset map is stale"
            )

    return result


def apply(payload: bytes, remap_plan: RemapPlan) -> bytes:
    """Return a new payload with every planned reference rewritten."""
    if not remap_plan.is_safe:
        raise PalMigrateError("refusing to apply an unsafe remap:\n" + remap_plan.summary())

    new_bytes = guid_mod.to_bytes(remap_plan.new_guid)
    old_bytes = guid_mod.to_bytes(remap_plan.old_guid)
    buffer = bytearray(payload)

    seen: set[int] = set()
    for ref in remap_plan.refs:
        if ref.offset in seen:
            continue  # the same field reached by two paths
        seen.add(ref.offset)
        if bytes(buffer[ref.offset : ref.offset + GUID_SIZE]) != old_bytes:
            raise PalMigrateError(f"offset {ref.offset} ({ref.path}) changed under us; aborting")
        buffer[ref.offset : ref.offset + GUID_SIZE] = new_bytes

    if len(buffer) != len(payload):
        raise PalMigrateError("remap changed the payload length, which is impossible")
    return bytes(buffer)


#: Maps whose entry count must survive a remap unchanged. A remap only
#: rewrites 16-byte id fields, so it can never legitimately add or remove an
#: entry. If one disappears, the save has been damaged.
ENTITY_MAPS = (
    "CharacterSaveParameterMap",
    "GroupSaveDataMap",
    "BaseCampSaveData",
)


def entity_counts(world: dict[str, Any]) -> dict[str, int]:
    """Entry counts for the maps a remap must leave untouched."""
    out: dict[str, int] = {}
    for name in ENTITY_MAPS:
        info = world.get(name)
        if isinstance(info, dict) and "__count__" in info:
            out[name] = info["__count__"]
    return out


def compare_entity_counts(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """
    Report any map that lost entries.

    This is the check that catches a semantically wrong remap. Proving no old
    id survives says the *rewrite* finished; it says nothing about whether the
    result still makes sense to the game. A save can pass that check with every
    Pal deleted -- which is exactly what happened when the Pal type marker was
    rewritten as if it were an owner id.
    """
    problems: list[str] = []
    for name, count in before.items():
        now = after.get(name)
        if now is None:
            problems.append(f"{name} is missing after the remap")
        elif now < count:
            problems.append(f"{name}: {count} -> {now} ({count - now} entries lost)")
    return problems


@dataclass
class ValidationReport:
    """Result of re-reading a remapped payload."""

    structural_old_refs: list[locate.GuidRef] = field(default_factory=list)
    structural_new_refs: list[locate.GuidRef] = field(default_factory=list)
    raw_old_pattern_hits: int = 0
    expected_raw_hits: int = 0
    undecoded: list[str] = field(default_factory=list)
    entity_losses: list[str] = field(default_factory=list)
    pal_sentinels_preserved: int = 0
    pal_sentinels_expected: int | None = None

    @property
    def sentinels_intact(self) -> bool:
        """
        Were the Pal type markers left alone?

        This is the check that actually catches the destructive bug. Entity
        counts do not: the file still parses with every entry present, and the
        deletion only happens later, inside the game, when the server decides
        those entries are no longer Pals. By then the damage is done. Comparing
        the marker count before and after catches it while it is still a file.
        """
        if self.pal_sentinels_expected is None:
            return True
        return self.pal_sentinels_preserved == self.pal_sentinels_expected

    @property
    def is_clean(self) -> bool:
        return (
            not self.structural_old_refs
            and not self.entity_losses
            and self.sentinels_intact
            and self.raw_old_pattern_hits <= self.expected_raw_hits
        )

    def summary(self) -> str:
        lines = [
            f"structural references to the old id : {len(self.structural_old_refs)}",
            f"structural references to the new id : {len(self.structural_new_refs)}",
            f"Pal type markers left untouched     : {self.pal_sentinels_preserved}",
            f"raw byte-pattern hits for the old id: {self.raw_old_pattern_hits:,} "
            f"(expected <= {self.expected_raw_hits:,} incidental)",
        ]
        if self.structural_old_refs:
            lines.append("")
            lines.append("SURVIVING REFERENCES:")
            for ref in self.structural_old_refs[:20]:
                lines.append(f"  {ref.path} @{ref.offset}")
        if self.entity_losses:
            lines.append("")
            lines.append("ENTITIES LOST -- the save has been damaged:")
            for loss in self.entity_losses:
                lines.append(f"  {loss}")
        if not self.sentinels_intact:
            lines.append("")
            lines.append(
                f"PAL TYPE MARKERS DESTROYED: {self.pal_sentinels_expected} before, "
                f"{self.pal_sentinels_preserved} after."
            )
            lines.append("The server will delete every affected Pal when it loads this save.")
        lines.append("")
        if self.is_clean:
            lines.append("PASS: old id gone, entities and Pal markers intact")
        elif not self.sentinels_intact:
            lines.append("FAIL: the remap will destroy Pals")
        elif self.entity_losses:
            lines.append("FAIL: the remap destroyed data")
        else:
            lines.append("FAIL: the remap is incomplete")
        return "\n".join(lines)


def validate(
    payload: bytes,
    world: dict[str, Any],
    old_guid: str,
    new_guid: str,
    expected_incidental: int,
    counts_before: dict[str, int] | None = None,
    sentinels_before: int | None = None,
) -> ValidationReport:
    """
    Re-read a remapped payload and check both that the old id is gone *and*
    that nothing was destroyed.

    ``expected_incidental`` is how many raw byte-pattern hits are known to be
    unrelated data rather than player fields. For the co-op host that number is
    large, because its id is twelve zero bytes plus ``int32`` 1, so the raw
    count alone means nothing -- only the structural count does.

    ``counts_before`` should come from :func:`entity_counts` on the *source*
    world. Without it this only proves the rewrite completed, which a save with
    every Pal deleted can also do.
    """
    old = guid_mod.normalise(old_guid)
    new = guid_mod.normalise(new_guid)

    walk = locate.walk(payload, world)
    losses = (
        compare_entity_counts(counts_before, entity_counts(world))
        if counts_before is not None
        else []
    )
    return ValidationReport(
        entity_losses=losses,
        pal_sentinels_preserved=len(walk.pal_sentinels),
        pal_sentinels_expected=sentinels_before,
        structural_old_refs=[r for r in walk.refs if r.value == old],
        structural_new_refs=[r for r in walk.refs if r.value == new],
        raw_old_pattern_hits=payload.count(guid_mod.to_bytes(old)),
        expected_raw_hits=expected_incidental,
        undecoded=list(walk.undecoded),
    )
