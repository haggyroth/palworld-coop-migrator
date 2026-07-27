"""CLI tests: exit codes, output, and the error paths users actually hit."""

from __future__ import annotations

import pytest

from palmigrate import container
from palmigrate.cli import main

from .conftest import DEFAULT_INI_TEXT

HOST = "00000000000000000000000000000001"
PLAYER_A = "A1B2C3D4000000000000000000000000"
PLAYER_B = "E5F60718000000000000000000000000"


@pytest.fixture
def world(tmp_path, world_option_gvas, minimal_gvas):
    """A world directory shaped like a real co-op save."""
    root = tmp_path / "world"
    players = root / "Players"
    players.mkdir(parents=True)

    container.write(root / "Level.sav", world_option_gvas)
    container.write(root / "LevelMeta.sav", minimal_gvas)
    container.write(root / "WorldOption.sav", world_option_gvas)
    for uid in (HOST, PLAYER_A, PLAYER_B):
        container.write(players / f"{uid}.sav", minimal_gvas)
    # Pal-storage sidecar must be ignored when discovering player ids
    container.write(players / f"{PLAYER_A}_dps.sav", minimal_gvas)
    return root


@pytest.fixture
def default_ini(tmp_path):
    path = tmp_path / "DefaultPalWorldSettings.ini"
    path.write_text(DEFAULT_INI_TEXT, encoding="utf-8")
    return path


class TestInspect:
    def test_lists_every_save_in_a_directory(self, world, capsys):
        assert main(["inspect", str(world)]) == 0
        out = capsys.readouterr().out
        assert "Level.sav" in out
        assert "WorldOption.sav" in out
        assert f"{PLAYER_A}.sav" in out
        assert "PlZ2" in out

    def test_accepts_a_single_file(self, world, capsys):
        assert main(["inspect", str(world / "Level.sav")]) == 0
        assert "Level.sav" in capsys.readouterr().out

    def test_verbose_adds_gvas_header(self, world, capsys):
        assert main(["inspect", str(world), "-v"]) == 0
        out = capsys.readouterr().out
        assert "/Script/Pal.PalWorldOptionSaveGame" in out
        assert "engine=5.1.1" in out

    def test_empty_directory_is_an_error(self, tmp_path, capsys):
        (tmp_path / "empty").mkdir()
        assert main(["inspect", str(tmp_path / "empty")]) == 1
        assert "no .sav files" in capsys.readouterr().err

    def test_reports_unreadable_file_without_crashing(self, tmp_path, capsys):
        bad = tmp_path / "Broken.sav"
        bad.write_bytes(b"not a save at all")
        assert main(["inspect", str(bad)]) == 1
        assert "FAILED" in capsys.readouterr().out


class TestScan:
    def test_host_guid_is_reported_unsafe(self, world, capsys):
        """Exit 2 is the signal a wrapper script should gate on."""
        assert main(["scan", str(world / "Level.sav")]) == 2
        out = capsys.readouterr().out
        assert "NOT SAFE" in out

    def test_discovers_reference_guids_from_players_dir(self, world, capsys):
        main(["scan", str(world / "Level.sav")])
        out = capsys.readouterr().out
        assert PLAYER_A.lower() in out
        assert PLAYER_B.lower() in out

    def test_ignores_dps_sidecar_when_discovering(self, world, capsys):
        main(["scan", str(world / "Level.sav")])
        assert "_dps" not in capsys.readouterr().out

    def test_explicit_reference_overrides_discovery(self, world, capsys):
        main(["scan", str(world / "Level.sav"), "--reference", PLAYER_B])
        out = capsys.readouterr().out
        assert PLAYER_B.lower() in out
        assert PLAYER_A.lower() not in out

    def test_warns_when_no_references_available(self, tmp_path, world_option_gvas, capsys):
        lonely = tmp_path / "lonely"
        lonely.mkdir()
        container.write(lonely / "Level.sav", world_option_gvas)
        main(["scan", str(lonely / "Level.sav")])
        assert "No reference GUIDs" in capsys.readouterr().err

    def test_missing_file_is_an_error(self, tmp_path, capsys):
        assert main(["scan", str(tmp_path / "nope.sav")]) == 1
        assert "not a file" in capsys.readouterr().err


class TestSettings:
    def test_dumps_settings_without_default_ini(self, world, capsys):
        assert main(["settings", str(world / "WorldOption.sav")]) == 0
        out = capsys.readouterr().out
        assert "ServerName" in out
        assert "My Co-op World" in out

    def test_renders_ini_to_stdout(self, world, default_ini, capsys):
        code = main(["settings", str(world / "WorldOption.sav"), "--default-ini", str(default_ini)])
        assert code == 0
        out = capsys.readouterr().out
        assert out.startswith("[/Script/Pal.PalGameWorldSettings]")
        assert "OptionSettings=(" in out
        assert len(out.strip().splitlines()) == 2

    def test_writes_ini_to_file(self, world, default_ini, tmp_path, capsys):
        dst = tmp_path / "PalWorldSettings.ini"
        code = main(
            [
                "settings",
                str(world / "WorldOption.sav"),
                "--default-ini",
                str(default_ini),
                "-o",
                str(dst),
            ]
        )
        assert code == 0
        assert dst.exists()
        assert "OptionSettings=(" in dst.read_text(encoding="utf-8")

    def test_written_ini_uses_crlf(self, world, default_ini, tmp_path):
        """
        The server writes CRLF, so we do too. This also guards the 3.9 fix:
        Path.write_text(newline=...) is 3.10+, so the newline handling has to
        go through open() and must not silently regress to LF.
        """
        dst = tmp_path / "PalWorldSettings.ini"
        main(
            [
                "settings",
                str(world / "WorldOption.sav"),
                "--default-ini",
                str(default_ini),
                "-o",
                str(dst),
            ]
        )
        raw = dst.read_bytes()
        assert b"\r\n" in raw
        assert raw.count(b"\n") == raw.count(b"\r\n"), "found a bare LF"

    def test_override_is_applied(self, world, default_ini, tmp_path):
        dst = tmp_path / "out.ini"
        main(
            [
                "settings",
                str(world / "WorldOption.sav"),
                "--default-ini",
                str(default_ini),
                "--set",
                "ServerName=Dedicated Box",
                "-o",
                str(dst),
            ]
        )
        # quoting is restored even though the value arrived bare
        assert 'ServerName="Dedicated Box"' in dst.read_text(encoding="utf-8")

    def test_malformed_set_is_rejected(self, world, default_ini, capsys):
        code = main(
            [
                "settings",
                str(world / "WorldOption.sav"),
                "--default-ini",
                str(default_ini),
                "--set",
                "NoEqualsSign",
            ]
        )
        assert code == 1
        assert "KEY=VALUE" in capsys.readouterr().err

    def test_passwords_are_masked_in_the_change_report(self, world, default_ini, capsys):
        main(
            [
                "settings",
                str(world / "WorldOption.sav"),
                "--default-ini",
                str(default_ini),
                "--set",
                "ServerPassword=hunter2",
                "-o",
                str(world / "out.ini"),
            ]
        )
        err = capsys.readouterr().err
        assert "ServerPassword" in err
        assert "hunter2" not in err

    def test_non_world_option_file_errors_cleanly(self, world, capsys):
        assert main(["settings", str(world / "LevelMeta.sav")]) == 1
        assert "error:" in capsys.readouterr().err


class TestConvert:
    def test_round_trips_and_verifies(self, world, tmp_path, capsys):
        dst = tmp_path / "Level.plz.sav"
        assert main(["convert", str(world / "Level.sav"), str(dst)]) == 0
        assert dst.exists()
        assert "verified identical" in capsys.readouterr().out
        src = container.read(world / "Level.sav")
        assert container.read(dst).payload == src.payload

    def test_refuses_to_overwrite_without_force(self, world, tmp_path, capsys):
        dst = tmp_path / "out.sav"
        dst.write_bytes(b"existing")
        assert main(["convert", str(world / "Level.sav"), str(dst)]) == 1
        assert "--force" in capsys.readouterr().err
        assert dst.read_bytes() == b"existing"

    def test_force_overwrites(self, world, tmp_path):
        dst = tmp_path / "out.sav"
        dst.write_bytes(b"existing")
        assert main(["convert", str(world / "Level.sav"), str(dst), "--force"]) == 0
        assert dst.read_bytes() != b"existing"

    def test_unreadable_input_errors_cleanly(self, tmp_path, capsys):
        bad = tmp_path / "bad.sav"
        bad.write_bytes(b"junk")
        assert main(["convert", str(bad), str(tmp_path / "out.sav")]) == 1
        assert "error:" in capsys.readouterr().err


class TestMigrateCommand:
    @pytest.fixture
    def coop(self, tmp_path):
        from .test_migrate import FRIEND, HOST, level_payload, player_payload

        src = tmp_path / "coop"
        (src / "Players").mkdir(parents=True)
        container.write(src / "Level.sav", level_payload())
        container.write(src / "Players" / f"{'0' * 31}1.sav", player_payload(HOST))
        container.write(src / "Players" / f"{FRIEND.upper()}.sav", player_payload(HOST))
        container.write(src / "LocalData.sav", container.read(src / "Level.sav").payload)
        return src

    def test_dry_run_reports_and_writes_nothing(self, coop, tmp_path, capsys):
        dst = tmp_path / "out"
        code = main(["migrate", str(coop), str(dst), "--new", "d00dfeed" + "0" * 24, "--dry-run"])
        assert code == 0
        assert not dst.exists()
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "reference(s) would be rewritten" in out

    def test_writes_a_world(self, coop, tmp_path, capsys):
        dst = tmp_path / "out"
        code = main(["migrate", str(coop), str(dst), "--new", "d00dfeed" + "0" * 24])
        assert code == 0
        assert (dst / "Level.sav").is_file()
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "STILL TO DO BY HAND" in out

    def test_unsafe_plan_exits_2(self, coop, tmp_path, capsys):
        """Remapping onto an id already in the save must refuse."""
        dst = tmp_path / "out"
        code = main(["migrate", str(coop), str(dst), "--new", "a1b2c3d4" + "0" * 24])
        assert code == 2
        assert "BLOCKED" in capsys.readouterr().err

    def test_existing_destination_errors_cleanly(self, coop, tmp_path, capsys):
        dst = tmp_path / "out"
        dst.mkdir()
        code = main(["migrate", str(coop), str(dst), "--new", "d00dfeed" + "0" * 24])
        assert code == 1
        assert "already exists" in capsys.readouterr().err

    def test_force_overwrites(self, coop, tmp_path):
        dst = tmp_path / "out"
        dst.mkdir()
        code = main(["migrate", str(coop), str(dst), "--new", "d00dfeed" + "0" * 24, "--force"])
        assert code == 0

    def test_new_is_required(self, coop, tmp_path):
        with pytest.raises(SystemExit):
            main(["migrate", str(coop), str(tmp_path / "out")])


class TestParser:
    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert "palmigrate" in capsys.readouterr().out

    def test_command_is_required(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main([])
        assert excinfo.value.code == 2

    def test_unknown_command_rejected(self):
        with pytest.raises(SystemExit):
            main(["nonsense"])
