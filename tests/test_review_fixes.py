"""
Regression tests for the findings from the v0.5.0 code review.

Each of these is a real defect that reproduced before the fix, so each test
should fail loudly if the guard is ever removed.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from palmigrate import container, guid, locate, migrate
from palmigrate.errors import ContainerError, PalMigrateError

from .conftest import (
    NONE,
    character_entry,
    gvas_header,
    map_entries,
    prop_byte_array,
    prop_guid,
    prop_str,
)

HOST = guid.to_bytes(guid.COOP_HOST_GUID)
NEW = "d00dfeed000000000000000000000000"


class TestUnboundedDecompression:
    """
    Finding 4. zlib.decompress has no output limit, and the header's declared
    length is attacker-controlled, so it cannot be used as the guard.
    """

    def test_a_bomb_is_refused_without_allocating_it(self):
        payload = b"GVAS" + b"\x00" * (4 * 1024 * 1024)
        body = zlib.compress(payload, 9)
        blob = (
            struct.pack("<II", 1000, len(body))
            + container.MAGIC_PLZ
            + bytes([container.TYPE_SINGLE])
            + body
        )
        # Small enough to be safe in a test, but the guard is what matters.
        with pytest.raises(ContainerError):
            container.decode(blob)

    def test_the_limit_is_enforced_during_decompression(self):
        big = b"\x00" * (2 * 1024 * 1024)
        with pytest.raises(ContainerError, match="decompression bomb"):
            container._zlib_decompress(zlib.compress(big, 9), limit=1024)

    def test_normal_saves_still_decompress(self, minimal_gvas):
        blob = container.encode(minimal_gvas)
        assert container.decode(blob).payload == minimal_gvas


class TestSourceIsNeverWritten:
    """Finding 2. The module promises it; force=True used to break it."""

    @pytest.fixture
    def world(self, tmp_path):
        from .test_migrate import level_payload

        src = tmp_path / "coop"
        (src / "Players").mkdir(parents=True)
        container.write(src / "Level.sav", level_payload())
        return src

    def test_destination_equal_to_source_is_refused(self, world):
        with pytest.raises(PalMigrateError, match="would overwrite the world"):
            migrate.migrate(world, world, guid.COOP_HOST_GUID, NEW, force=True)

    def test_destination_inside_source_is_refused(self, world):
        with pytest.raises(PalMigrateError, match="inside the source"):
            migrate.migrate(world, world / "out", guid.COOP_HOST_GUID, NEW, force=True)

    def test_a_sibling_destination_is_fine(self, world, tmp_path):
        result = migrate.migrate(world, tmp_path / "out", guid.COOP_HOST_GUID, NEW)
        assert result.ok


class TestPlainRemapRefusesWorldSaves:
    """
    Finding 6. _remap_plain_save rewrites every Guid holding the old id. That
    is correct for a player save and catastrophic for a world save, whose
    character keys hold Pal type markers.
    """

    def test_a_character_map_is_refused(self):
        from .test_migrate import level_payload

        with pytest.raises(PalMigrateError, match="Pal type markers"):
            migrate._remap_plain_save(level_payload(), guid.COOP_HOST_GUID, NEW)

    def test_a_player_save_is_accepted(self):
        payload = (
            gvas_header("/Script/Pal.PalWorldPlayerSaveGame") + prop_guid("PlayerUId", HOST) + NONE
        )
        out, changed = migrate._remap_plain_save(payload, guid.COOP_HOST_GUID, NEW)
        assert changed == 1
        assert len(out) == len(payload)


class TestFindReferencesCarriesSafetySignals:
    """
    Finding 3. It used to drop opaque regions, Pal markers and unclassified
    keys, so any caller relying on it lost exactly the signals that make a
    remap safe.
    """

    def test_signals_survive_filtering(self):
        entries = character_entry(HOST, bytes(range(16)), is_player=True) + character_entry(
            HOST, bytes(range(16, 32)), is_player=False
        )
        payload = gvas_header() + map_entries(entries, 2, name="CharacterSaveParameterMap") + NONE

        from palmigrate import gvas as gvas_mod

        _, props = gvas_mod.parse(payload)
        world = {"CharacterSaveParameterMap": props["CharacterSaveParameterMap"]}

        full = locate.walk(payload, world)
        filtered = locate.find_references(payload, world, guid.COOP_HOST_GUID)

        assert filtered.pal_sentinels == full.pal_sentinels
        assert filtered.opaque == full.opaque
        assert filtered.unclassified == full.unclassified
        assert filtered.undecoded == full.undecoded
        assert len(full.pal_sentinels) == 1


class TestGuildRosterMustBeUnambiguous:
    """
    Finding 5. It used to take the first offset that parsed as a roster. If two
    parse, we cannot tell which is real, and rewriting the wrong one corrupts
    bytes that may not be player ids at all.
    """

    def _roster(self, uids: list[bytes], names: list[str]) -> bytes:
        out = struct.pack("<i", len(uids))
        for uid, name in zip(uids, names):
            encoded = name.encode("ascii") + b"\x00"
            out += uid + struct.pack("<q", 1) + struct.pack("<i", len(encoded)) + encoded + b"\x00"
        return out

    def test_a_single_roster_is_decoded(self):
        tail = b"\x11" * 16 + self._roster([HOST], ["Someone"])
        refs = locate.decode_guild_members(tail, 0)
        assert [r.path for r in refs] == ["admin_player_uid", "players[0].player_uid"]

    def test_two_rosters_are_refused(self):
        one = self._roster([HOST], ["Someone"])
        tail = b"\x11" * 16 + one + b"\x22" * 16 + one
        assert len(locate.decode_guild_members(tail, 0)) == 0, (
            "an ambiguous tail must yield nothing so the caller marks it opaque"
        )

    def test_no_roster_yields_nothing(self):
        assert locate.decode_guild_members(b"\x00" * 13, 0) == []


class TestUnclassifiableEntriesBlockTheRemap:
    """Finding 1, the critical one. Restated here as an end-to-end guard."""

    def test_migrate_refuses_a_world_it_cannot_classify(self, tmp_path):
        key = prop_guid("PlayerUId", HOST) + prop_str("DebugName", "") + NONE
        junk = prop_byte_array("RawData", b"\x7f\x7f\x7f\x7f" + b"\xfe" * 20)
        entries = key + junk + NONE
        inner = map_entries(entries, 1, name="CharacterSaveParameterMap")

        from .test_migrate import _struct

        payload = (
            gvas_header("/Script/Pal.PalWorldSaveGame")
            + _struct("worldSaveData", inner + NONE)
            + NONE
        )
        src = tmp_path / "coop"
        (src / "Players").mkdir(parents=True)
        container.write(src / "Level.sav", payload)

        dst = tmp_path / "out"
        result = migrate.migrate(src, dst, guid.COOP_HOST_GUID, NEW)

        assert not result.ok
        assert result.plan is not None and not result.plan.is_safe
        assert not dst.exists(), "nothing may be written when the plan is unsafe"
        assert any("cannot tell a player from a Pal" in b for b in result.plan.blockers)
