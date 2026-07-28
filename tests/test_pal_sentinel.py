"""
The Pal type marker.

``CharacterSaveParameterMap`` keys hold ``00000000000000000000000000000001``
for Pals. That is byte-identical to the co-op host's PlayerUId, but it means
"this entry is a Pal", not "this Pal belongs to the host" -- proven in a real
save by Pals carrying it while their ``OwnerPlayerUId`` was a different player.

Rewriting it makes the server delete the Pal on load. Measured on a real world:
remapping the 100 key occurrences took it from 102 characters to 3, while
remapping the other 301 references preserved all 102. These tests exist so that
never ships again.
"""

from __future__ import annotations

import pytest

from palmigrate import guid, locate, remap

from .conftest import NONE, character_entry, gvas_header, map_entries

HOST = guid.to_bytes(guid.COOP_HOST_GUID)
NEW = guid.to_bytes("d00dfeed000000000000000000000000")
FRIEND = guid.to_bytes("a1b2c3d4000000000000000000000000")


def build(entries: bytes, count: int):
    from palmigrate import gvas

    payload = gvas_header() + map_entries(entries, count) + NONE
    _, props = gvas.parse(payload)
    return payload, {"CharacterSaveParameterMap": props["TheMap"]}


def instance(n: int) -> bytes:
    return bytes([n]) + b"\xa5" * 15


class TestClassification:
    def test_pal_key_is_a_sentinel_not_a_reference(self):
        payload, world = build(character_entry(HOST, instance(1), is_player=False), 1)
        result = locate.walk(payload, world)
        assert result.matching(guid.COOP_HOST_GUID) == []
        assert len(result.pal_sentinels) == 1
        assert result.pal_sentinels[0].path.endswith("key.PlayerUId")

    def test_player_key_is_a_real_reference(self):
        payload, world = build(character_entry(HOST, instance(2), is_player=True), 1)
        result = locate.walk(payload, world)
        assert len(result.matching(guid.COOP_HOST_GUID)) == 1
        assert result.pal_sentinels == []

    def test_a_pal_owned_by_someone_else_still_carries_the_marker(self):
        """
        The observation that proves the field is a type marker: a Pal owned by
        another player still has the host id in its key.
        """
        from .conftest import prop_guid

        entry = character_entry(
            HOST,
            instance(3),
            is_player=False,
            extra=prop_guid("OwnerPlayerUId", FRIEND),
        )
        payload, world = build(entry, 1)
        result = locate.walk(payload, world)
        assert result.pal_sentinels, "key must be treated as a marker"
        # the real owner field is a different player and is left alone
        assert result.matching("a1b2c3d4000000000000000000000000")

    def test_mixed_world_counts_correctly(self):
        entries = (
            character_entry(HOST, instance(1), is_player=True)
            + character_entry(HOST, instance(2), is_player=False)
            + character_entry(HOST, instance(3), is_player=False)
            + character_entry(FRIEND, instance(4), is_player=True)
        )
        payload, world = build(entries, 4)
        result = locate.walk(payload, world)
        assert len(result.pal_sentinels) == 2
        assert len(result.matching(guid.COOP_HOST_GUID)) == 1

    def test_undecodable_entry_is_withheld_not_remapped(self):
        """
        When IsPlayer cannot be read, the key must NOT be treated as a
        remappable reference.

        The two mistakes are not equally bad. Wrongly skipping a real player's
        key leaves one stale reference — detectable and recoverable. Wrongly
        rewriting a Pal's type marker deletes the Pal permanently. So an
        entry we cannot classify is withheld and blocks the remap.
        """
        from .conftest import prop_byte_array, prop_guid, prop_str

        key = prop_guid("PlayerUId", HOST) + prop_str("DebugName", "") + NONE
        junk = prop_byte_array("RawData", b"\x7f\x7f\x7f\x7f" + b"\xfe" * 20)
        payload, world = build(key + junk + NONE, 1)
        result = locate.walk(payload, world)

        assert result.matching(guid.COOP_HOST_GUID) == [], "must not be remappable"
        assert len(result.unclassified) == 1
        assert result.unclassified[0].path.endswith("key.PlayerUId")

    def test_an_unclassifiable_entry_blocks_the_remap(self):
        """It refuses rather than guessing, because guessing can delete Pals."""
        from .conftest import prop_byte_array, prop_guid, prop_str

        key = prop_guid("PlayerUId", HOST) + prop_str("DebugName", "") + NONE
        junk = prop_byte_array("RawData", b"\x7f\x7f\x7f\x7f" + b"\xfe" * 20)
        payload, world = build(key + junk + NONE, 1)
        plan = remap.plan(payload, world, guid.COOP_HOST_GUID, "d00dfeed" + "0" * 24)

        assert not plan.is_safe
        assert any("cannot tell a player from a Pal" in b for b in plan.blockers)

    def test_a_non_sentinel_key_we_cannot_classify_is_still_remappable(self):
        """
        Only the sentinel value is ambiguous. Any other id in a key cannot be a
        Pal marker, so an undecodable blob does not make it unsafe.
        """
        from .conftest import prop_byte_array, prop_guid, prop_str

        key = prop_guid("PlayerUId", FRIEND) + prop_str("DebugName", "") + NONE
        junk = prop_byte_array("RawData", b"\x7f\x7f\x7f\x7f" + b"\xfe" * 20)
        payload, world = build(key + junk + NONE, 1)
        result = locate.walk(payload, world)

        assert len(result.matching("a1b2c3d4000000000000000000000000")) == 1
        assert result.unclassified == []


class TestRemapSkipsSentinels:
    def test_plan_excludes_and_reports_them(self):
        entries = character_entry(HOST, instance(1), is_player=True) + character_entry(
            HOST, instance(2), is_player=False
        )
        payload, world = build(entries, 2)
        plan = remap.plan(payload, world, guid.COOP_HOST_GUID, "d00dfeed000000000000000000000000")
        assert plan.skipped_sentinels == 1
        assert len(plan.refs) == 1
        assert plan.is_safe
        assert "NOT rewritten" in plan.summary()

    def test_applying_leaves_the_marker_bytes_alone(self):
        entries = character_entry(HOST, instance(1), is_player=True) + character_entry(
            HOST, instance(2), is_player=False
        )
        payload, world = build(entries, 2)
        plan = remap.plan(payload, world, guid.COOP_HOST_GUID, "d00dfeed000000000000000000000000")
        patched = remap.apply(payload, plan)

        from palmigrate import gvas

        _, props2 = gvas.parse(patched)
        after = locate.walk(patched, {"CharacterSaveParameterMap": props2["TheMap"]})
        assert len(after.pal_sentinels) == 1, "the Pal marker must survive"
        assert after.matching(guid.COOP_HOST_GUID) == []


class TestValidationCatchesTheDestructiveBug:
    def _world(self):
        entries = character_entry(HOST, instance(1), is_player=True) + b"".join(
            character_entry(HOST, instance(i), is_player=False) for i in range(2, 6)
        )
        return build(entries, 5)

    def test_correct_remap_passes(self):
        from palmigrate import gvas

        payload, world = self._world()
        before = remap.entity_counts(world)
        sentinels = len(locate.walk(payload, world).pal_sentinels)
        plan = remap.plan(payload, world, guid.COOP_HOST_GUID, "d00dfeed000000000000000000000000")
        patched = remap.apply(payload, plan)
        _, p2 = gvas.parse(patched)
        w2 = {"CharacterSaveParameterMap": p2["TheMap"]}
        report = remap.validate(
            patched,
            w2,
            guid.COOP_HOST_GUID,
            "d00dfeed000000000000000000000000",
            expected_incidental=10**9,
            counts_before=before,
            sentinels_before=sentinels,
        )
        assert report.sentinels_intact
        assert report.is_clean

    def test_rewriting_the_markers_is_caught(self):
        """
        The bug that shipped. Entity counts do NOT catch it -- the file still
        parses with every entry present, and the deletion happens later inside
        the game. Only the marker count reveals it while it is still a file.
        """
        from palmigrate import gvas

        payload, world = self._world()
        before = remap.entity_counts(world)
        walk = locate.walk(payload, world)
        sentinels = len(walk.pal_sentinels)
        assert sentinels == 4

        buf = bytearray(payload)
        for ref in list(walk.refs) + list(walk.pal_sentinels):
            if ref.value == guid.COOP_HOST_GUID:
                buf[ref.offset : ref.offset + 16] = NEW
        broken = bytes(buf)

        _, p2 = gvas.parse(broken)
        w2 = {"CharacterSaveParameterMap": p2["TheMap"]}
        report = remap.validate(
            broken,
            w2,
            guid.COOP_HOST_GUID,
            "d00dfeed000000000000000000000000",
            expected_incidental=10**9,
            counts_before=before,
            sentinels_before=sentinels,
        )
        assert report.structural_old_refs == [], "old id is gone, which is why the old check passed"
        assert report.entity_losses == [], "entity counts do not reveal this"
        assert not report.sentinels_intact, "the marker check must catch it"
        assert not report.is_clean
        assert "will destroy Pals" in report.summary()


class TestEntityCounts:
    def test_counts_the_maps_that_must_not_shrink(self):
        payload, world = build(character_entry(HOST, instance(1), is_player=True), 1)
        counts = remap.entity_counts(world)
        assert counts["CharacterSaveParameterMap"] == 1

    def test_detects_a_loss(self):
        problems = remap.compare_entity_counts(
            {"CharacterSaveParameterMap": 102}, {"CharacterSaveParameterMap": 3}
        )
        assert problems and "99 entries lost" in problems[0]

    def test_detects_a_missing_map(self):
        problems = remap.compare_entity_counts({"BaseCampSaveData": 2}, {})
        assert problems and "missing" in problems[0]

    def test_growth_is_not_a_problem(self):
        assert remap.compare_entity_counts({"A": 1}, {"A": 5}) == []


@pytest.mark.parametrize("is_player", [True, False])
def test_sentinel_constant_matches_the_coop_host_id(is_player):
    """
    The trap in one assertion: these are the same 32 characters, which is why a
    value-based remap cannot tell them apart and classification is required.
    """
    assert locate.PAL_SENTINEL_UID == guid.COOP_HOST_GUID
