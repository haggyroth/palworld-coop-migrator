"""
Occurrence analysis for player GUIDs inside a save.

This module exists to make one point measurable: **you cannot migrate a co-op
host by doing a byte-level search-and-replace on their GUID.**

The co-op host id ``00000000000000000000000000000001`` serialises to twelve
zero bytes followed by ``01 00 00 00``. That byte pattern also matches any
zero padding followed by an ``int32`` of 1 -- which is everywhere in a save
file. Comparing its hit count against a real, high-entropy player GUID from
the same file shows the scale of the problem immediately.

Measured on a real 5.4 MB ``Level.sav``:

===========================================  ======
GUID                                          Hits
===========================================  ======
friend ``A1B2C3D4...`` (high entropy)            174
friend ``E5F60718...`` (high entropy)             56
host ``00000000...0001``                       2,904
===========================================  ======

Genuine player reference counts land in the tens-to-low-hundreds, so roughly
2,700 of those 2,904 hits are noise. Rewriting them would corrupt thousands of
unrelated fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import guid as guid_mod


@dataclass
class GuidOccurrences:
    """Where a GUID's byte pattern appears in a payload."""

    guid: str
    total: int
    aligned_4: int
    offsets: list[int] = field(default_factory=list)

    @property
    def alignment_ratio(self) -> float:
        return self.aligned_4 / self.total if self.total else 0.0


def find_occurrences(payload: bytes, guid_text: str, *, keep_offsets: int = 64) -> GuidOccurrences:
    """Count byte-pattern occurrences of ``guid_text`` within ``payload``."""
    needle = guid_mod.to_bytes(guid_text)
    offsets: list[int] = []
    total = 0
    aligned = 0

    index = payload.find(needle)
    while index != -1:
        total += 1
        if index % 4 == 0:
            aligned += 1
        if len(offsets) < keep_offsets:
            offsets.append(index)
        index = payload.find(needle, index + 1)

    return GuidOccurrences(
        guid=guid_mod.normalise(guid_text),
        total=total,
        aligned_4=aligned,
        offsets=offsets,
    )


@dataclass
class CollisionReport:
    """Comparison of a target GUID against known-good reference GUIDs."""

    target: GuidOccurrences
    references: list[GuidOccurrences]

    @property
    def reference_max(self) -> int:
        return max((r.total for r in self.references), default=0)

    @property
    def estimated_false_positives(self) -> int:
        """
        How many target hits are probably noise.

        Uses the busiest reference GUID as the plausible ceiling for a real
        player's reference count. Conservative by construction: it assumes the
        target legitimately appears as often as the most-referenced real player.
        """
        return max(0, self.target.total - self.reference_max)

    @property
    def is_safe_to_byte_replace(self) -> bool:
        """
        True only when the target's hit count is consistent with the references.

        A target with no long zero-run and a hit count within 2x of the busiest
        reference is plausibly all-genuine. Anything else is not.
        """
        if guid_mod.entropy_warning(self.target.guid) is not None:
            return False
        if not self.references:
            return False
        return self.target.total <= max(1, self.reference_max) * 2

    def summary(self) -> str:
        lines = [
            f"{'GUID':<36} {'hits':>8} {'4-byte aligned':>16}",
            "-" * 62,
        ]
        for ref in self.references:
            lines.append(f"{ref.guid:<36} {ref.total:>8,} {ref.aligned_4:>16,}")
        t = self.target
        lines.append(f"{t.guid + '  (target)':<36} {t.total:>8,} {t.aligned_4:>16,}")
        lines.append("")

        warning = guid_mod.entropy_warning(t.guid)
        if warning:
            lines.append(f"WARNING: {warning}")
        if self.references:
            lines.append(
                f"Busiest real player GUID has {self.reference_max:,} references. "
                f"The target has {t.total:,}."
            )
            lines.append(f"Estimated false positives: ~{self.estimated_false_positives:,}")
        lines.append("")
        if self.is_safe_to_byte_replace:
            lines.append("Byte-level replacement looks plausible for this GUID.")
        else:
            lines.append(
                "Byte-level replacement is NOT SAFE for this GUID. A structural remap is required."
            )
        return "\n".join(lines)


def build_report(payload: bytes, target_guid: str, reference_guids: list[str]) -> CollisionReport:
    """Scan ``payload`` for the target and each reference GUID."""
    return CollisionReport(
        target=find_occurrences(payload, target_guid),
        references=[find_occurrences(payload, g) for g in reference_guids],
    )
