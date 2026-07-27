"""End-to-end migration of a world folder."""

from __future__ import annotations

import pytest

from palmigrate import container, guid, gvas, locate, migrate
from palmigrate.errors import PalMigrateError

from .conftest import NONE, character_entry, gvas_header, map_entries, prop_guid, prop_int

HOST = guid.to_bytes(guid.COOP_HOST_GUID)
NEW = "d00dfeed000000000000000000000000"
NEW_BYTES = guid.to_bytes(NEW)
FRIEND = "a1b2c3d4000000000000000000000000"
FRIEND_BYTES = guid.to_bytes(FRIEND)


def inst(n: int) -> bytes:
    return bytes([n]) + b"\x33" * 15


def level_payload(pal_count: int = 3) -> bytes:
    entries = character_entry(HOST, inst(1), is_player=True) + character_entry(
        FRIEND_BYTES, inst(2), is_player=True
    )
    for i in range(pal_count):
        entries += character_entry(HOST, inst(10 + i), is_player=False)
    # The map must carry its real name: locate.walk only looks inside the
    # maps it knows can hold player references.
    inner = map_entries(entries, 2 + pal_count, name="CharacterSaveParameterMap")
    return (
        gvas_header("/Script/Pal.PalWorldSaveGame") + _struct("worldSaveData", inner + NONE) + NONE
    )


def _struct(name: str, inner: bytes) -> bytes:
    import struct as _s

    from .conftest import fstring

    return (
        fstring(name)
        + fstring("StructProperty")
        + _s.pack("<q", len(inner))
        + fstring("PalWorldSaveData")
        + b"\x00" * 16
        + b"\x00"
        + inner
    )


def player_payload(owner: bytes) -> bytes:
    return (
        gvas_header("/Script/Pal.PalWorldPlayerSaveGame")
        + prop_guid("PlayerUId", owner)
        + prop_int("Level", 30)
        + NONE
    )


@pytest.fixture
def coop_world(tmp_path):
    """A co-op world folder: Level, LevelMeta, three players, and the extras."""
    src = tmp_path / "coop"
    (src / "Players").mkdir(parents=True)
    container.write(src / "Level.sav", level_payload())
    container.write(src / "LevelMeta.sav", gvas_header() + prop_int("V", 1) + NONE)
    container.write(src / "Players" / f"{guid.COOP_HOST_GUID.upper()}.sav", player_payload(HOST))
    container.write(src / "Players" / f"{FRIEND.upper()}.sav", player_payload(FRIEND_BYTES))
    container.write(
        src / "Players" / f"{FRIEND.upper()}_dps.sav", gvas_header() + prop_int("P", 1) + NONE
    )
    container.write(src / "WorldOption.sav", gvas_header() + prop_int("W", 1) + NONE)
    container.write(src / "LocalData.sav", gvas_header() + prop_int("L", 1) + NONE)
    return src


class TestMigration:
    def test_produces_a_complete_world(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        assert result.ok
        assert (dst / "Level.sav").is_file()
        assert (dst / "LevelMeta.sav").is_file()
        assert (dst / "Players" / f"{NEW.upper()}.sav").is_file()

    def test_host_player_file_is_renamed(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        names = {p.name for p in (dst / "Players").glob("*.sav")}
        assert f"{NEW.upper()}.sav" in names
        assert f"{guid.COOP_HOST_GUID.upper()}.sav" not in names

    def test_other_players_are_untouched(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        original = (coop_world / "Players" / f"{FRIEND.upper()}.sav").read_bytes()
        copied = (dst / "Players" / f"{FRIEND.upper()}.sav").read_bytes()
        assert copied == original

    def test_dps_sidecar_is_carried_across(self, coop_world, tmp_path):
        """Losing this loses that player's Pal storage."""
        dst = tmp_path / "out"
        migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        assert (dst / "Players" / f"{FRIEND.upper()}_dps.sav").is_file()

    def test_worldoption_is_not_carried_across(self, coop_world, tmp_path):
        """It would silently override PalWorldSettings.ini."""
        dst = tmp_path / "out"
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        assert not (dst / "WorldOption.sav").exists()
        assert any(f.name == "WorldOption.sav" and f.action == "excluded" for f in result.files)

    def test_localdata_goes_to_a_client_folder_not_the_server(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        assert not (dst / "LocalData.sav").exists()
        assert result.client_localdata is not None
        assert result.client_localdata.is_file()
        assert any("LOCALAPPDATA" in s for s in result.manual_steps)

    def test_pal_markers_survive(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        assert result.report is not None
        assert result.report.pal_sentinels_preserved == 3
        assert result.report.sentinels_intact

    def test_entity_counts_are_preserved(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        before = container.read(coop_world / "Level.sav")
        after = container.read(dst / "Level.sav")
        _, bp = gvas.parse(before.payload)
        _, ap = gvas.parse(after.payload)
        key = "CharacterSaveParameterMap"
        assert bp["worldSaveData"][key]["__count__"] == ap["worldSaveData"][key]["__count__"]

    def test_source_is_never_modified(self, coop_world, tmp_path):
        import hashlib

        before = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in coop_world.rglob("*.sav")
        }
        migrate.migrate(coop_world, tmp_path / "out", guid.COOP_HOST_GUID, NEW)
        after = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in coop_world.rglob("*.sav")
        }
        assert before == after

    def test_old_id_is_gone_from_the_output(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)
        sav = container.read(dst / "Level.sav")
        _, props = gvas.parse(sav.payload)
        result = locate.walk(sav.payload, props["worldSaveData"])
        assert result.matching(guid.COOP_HOST_GUID) == []
        assert result.matching(NEW)


class TestGuards:
    def test_dry_run_writes_nothing(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW, dry_run=True)
        assert not dst.exists()
        assert result.plan is not None
        assert result.plan.is_safe

    def test_existing_destination_is_refused(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        dst.mkdir()
        with pytest.raises(PalMigrateError, match="already exists"):
            migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW)

    def test_force_overwrites(self, coop_world, tmp_path):
        dst = tmp_path / "out"
        dst.mkdir()
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, NEW, force=True)
        assert result.ok

    def test_missing_level_is_refused(self, tmp_path):
        empty = tmp_path / "nothing"
        empty.mkdir()
        with pytest.raises(PalMigrateError, match="no Level.sav"):
            migrate.migrate(empty, tmp_path / "out", guid.COOP_HOST_GUID, NEW)

    def test_identical_ids_are_refused(self, coop_world, tmp_path):
        with pytest.raises(PalMigrateError, match="identical"):
            migrate.migrate(coop_world, tmp_path / "out", guid.COOP_HOST_GUID, guid.COOP_HOST_GUID)

    def test_level_without_worldsavedata_is_refused(self, tmp_path):
        src = tmp_path / "bad"
        src.mkdir()
        container.write(src / "Level.sav", gvas_header() + prop_int("X", 1) + NONE)
        with pytest.raises(PalMigrateError, match="no worldSaveData"):
            migrate.migrate(src, tmp_path / "out", guid.COOP_HOST_GUID, NEW)

    def test_unsafe_plan_stops_before_writing(self, coop_world, tmp_path):
        """Remapping onto an id already present must not produce a folder."""
        dst = tmp_path / "out"
        result = migrate.migrate(coop_world, dst, guid.COOP_HOST_GUID, FRIEND)
        assert not result.ok
        assert result.plan is not None and not result.plan.is_safe
        assert not dst.exists()


class TestSummary:
    def test_summary_lists_actions_and_manual_steps(self, coop_world, tmp_path):
        result = migrate.migrate(coop_world, tmp_path / "out", guid.COOP_HOST_GUID, NEW)
        text = result.summary()
        assert "Level.sav" in text
        assert "Pal type marker" in text
        assert "STILL TO DO BY HAND" in text

    def test_not_ok_without_a_plan(self):
        assert not migrate.MigrationResult().ok
