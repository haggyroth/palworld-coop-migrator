"""Remap planning, application and validation guards."""

from __future__ import annotations

import pytest

from palmigrate import guid, locate, remap
from palmigrate.errors import PalMigrateError

from .conftest import NONE, character_entry, gvas_header, map_entries

HOST = guid.to_bytes(guid.COOP_HOST_GUID)
NEW = "d00dfeed000000000000000000000000"
OTHER = guid.to_bytes("a1b2c3d4000000000000000000000000")


def build(entries: bytes, count: int):
    from palmigrate import gvas

    payload = gvas_header() + map_entries(entries, count) + NONE
    _, props = gvas.parse(payload)
    return payload, {"CharacterSaveParameterMap": props["TheMap"]}


def inst(n: int) -> bytes:
    return bytes([n]) + b"\x5a" * 15


class TestPlanBlockers:
    def test_identical_ids_are_refused(self):
        payload, world = build(character_entry(HOST, inst(1), is_player=True), 1)
        p = remap.plan(payload, world, guid.COOP_HOST_GUID, guid.COOP_HOST_GUID)
        assert not p.is_safe
        assert any("identical" in b for b in p.blockers)

    def test_no_references_is_refused(self):
        payload, world = build(character_entry(OTHER, inst(1), is_player=True), 1)
        p = remap.plan(payload, world, guid.COOP_HOST_GUID, NEW)
        assert not p.is_safe
        assert any("no references" in b for b in p.blockers)

    def test_target_already_present_is_refused(self):
        """Remapping onto an id already in the save would merge two identities."""
        entries = character_entry(HOST, inst(1), is_player=True) + character_entry(
            guid.to_bytes(NEW), inst(2), is_player=True
        )
        payload, world = build(entries, 2)
        p = remap.plan(payload, world, guid.COOP_HOST_GUID, NEW)
        assert not p.is_safe
        assert any("merge two identities" in b for b in p.blockers)

    def test_summary_explains_a_refusal(self):
        payload, world = build(character_entry(OTHER, inst(1), is_player=True), 1)
        p = remap.plan(payload, world, guid.COOP_HOST_GUID, NEW)
        text = p.summary()
        assert "BLOCKED" in text
        assert "worse than none" in text


class TestApply:
    def _plan(self):
        payload, world = build(character_entry(HOST, inst(1), is_player=True), 1)
        return payload, remap.plan(payload, world, guid.COOP_HOST_GUID, NEW)

    def test_rewrites_and_preserves_length(self):
        payload, p = self._plan()
        out = remap.apply(payload, p)
        assert len(out) == len(payload)
        ref = p.refs[0]
        assert out[ref.offset : ref.offset + 16] == guid.to_bytes(NEW)

    def test_only_the_target_bytes_change(self):
        payload, p = self._plan()
        out = remap.apply(payload, p)
        differing = sum(1 for a, b in zip(payload, out) if a != b)
        # old and new share 12 zero bytes, so at most 16 bytes per ref differ
        assert 0 < differing <= 16 * len(p.refs)

    def test_refuses_an_unsafe_plan(self):
        payload, world = build(character_entry(OTHER, inst(1), is_player=True), 1)
        p = remap.plan(payload, world, guid.COOP_HOST_GUID, NEW)
        with pytest.raises(PalMigrateError, match="refusing to apply"):
            remap.apply(payload, p)

    def test_detects_a_stale_offset(self):
        payload, p = self._plan()
        mangled = bytearray(payload)
        ref = p.refs[0]
        mangled[ref.offset : ref.offset + 16] = b"\xcc" * 16
        with pytest.raises(PalMigrateError, match="changed under us"):
            remap.apply(bytes(mangled), p)

    def test_duplicate_offsets_are_applied_once(self):
        payload, p = self._plan()
        p.refs = p.refs + p.refs  # same field reached twice
        out = remap.apply(payload, p)
        assert len(out) == len(payload)


class TestValidationReport:
    def test_reports_surviving_references(self):
        report = remap.ValidationReport(
            structural_old_refs=[locate.GuidRef("a.b", 1, guid.COOP_HOST_GUID)],
        )
        assert not report.is_clean
        assert "SURVIVING REFERENCES" in report.summary()
        assert "incomplete" in report.summary()

    def test_reports_entity_loss(self):
        report = remap.ValidationReport(entity_losses=["Characters: 102 -> 3"])
        assert not report.is_clean
        assert "ENTITIES LOST" in report.summary()
        assert "destroyed data" in report.summary()

    def test_reports_destroyed_pal_markers(self):
        report = remap.ValidationReport(pal_sentinels_expected=99, pal_sentinels_preserved=0)
        assert not report.sentinels_intact
        assert "PAL TYPE MARKERS DESTROYED" in report.summary()
        assert "will destroy Pals" in report.summary()

    def test_sentinels_unchecked_when_no_baseline(self):
        report = remap.ValidationReport()
        assert report.sentinels_intact

    def test_raw_hits_above_expectation_fail(self):
        report = remap.ValidationReport(raw_old_pattern_hits=10, expected_raw_hits=2)
        assert not report.is_clean

    def test_clean_report_says_so(self):
        report = remap.ValidationReport(pal_sentinels_expected=5, pal_sentinels_preserved=5)
        assert report.is_clean
        assert "PASS" in report.summary()


class TestBySurface:
    def test_groups_entries_by_field(self):
        p = remap.RemapPlan(old_guid="a" * 32, new_guid="b" * 32)
        p.refs = [
            locate.GuidRef("CharacterSaveParameterMap[0].key.PlayerUId", 0, "a" * 32),
            locate.GuidRef("CharacterSaveParameterMap[7].key.PlayerUId", 1, "a" * 32),
            locate.GuidRef("GroupSaveDataMap[2].RawData.handles[0].player_uid", 2, "a" * 32),
        ]
        surfaces = p.by_surface()
        assert surfaces["CharacterSaveParameterMap.key.PlayerUId"] == 2

    def test_path_without_an_index_is_kept_whole(self):
        p = remap.RemapPlan(old_guid="a" * 32, new_guid="b" * 32)
        p.refs = [locate.GuidRef("SomeTopLevelField", 0, "a" * 32)]
        assert "SomeTopLevelField" in p.by_surface()


class TestEndToEnd:
    def test_a_clean_migration_validates(self):
        from palmigrate import gvas

        entries = character_entry(HOST, inst(1), is_player=True) + b"".join(
            character_entry(HOST, inst(i), is_player=False) for i in range(2, 8)
        )
        payload, world = build(entries, 7)
        before = remap.entity_counts(world)
        sentinels = len(locate.walk(payload, world).pal_sentinels)

        p = remap.plan(payload, world, guid.COOP_HOST_GUID, NEW)
        assert p.skipped_sentinels == 6
        out = remap.apply(payload, p)

        _, props = gvas.parse(out)
        report = remap.validate(
            out,
            {"CharacterSaveParameterMap": props["TheMap"]},
            guid.COOP_HOST_GUID,
            NEW,
            expected_incidental=10**9,
            counts_before=before,
            sentinels_before=sentinels,
        )
        assert report.is_clean
        assert report.pal_sentinels_preserved == 6
