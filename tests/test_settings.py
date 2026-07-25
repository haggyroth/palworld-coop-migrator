"""Settings extraction and PalWorldSettings.ini rendering."""

from __future__ import annotations

import pytest

from palmigrate import settings as settings_mod
from palmigrate.errors import PalMigrateError

from .conftest import NONE, gvas_header


class TestExtract:
    def test_pulls_settings_block(self, world_option_gvas):
        values = settings_mod.extract(world_option_gvas)
        assert values["ServerName"] == "My Co-op World"
        assert values["ExpRate"] == pytest.approx(1.5)

    def test_errors_without_option_world_data(self, minimal_gvas):
        with pytest.raises(PalMigrateError, match="no OptionWorldData"):
            settings_mod.extract(minimal_gvas)

    def test_errors_on_empty_settings(self):
        from .conftest import prop_struct

        payload = (
            gvas_header() + prop_struct("OptionWorldData", "PalOptionWorldSaveData", NONE) + NONE
        )
        with pytest.raises(PalMigrateError, match="no Settings"):
            settings_mod.extract(payload)


class TestRenderIni:
    def test_preserves_default_key_order(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text)
        body = ini.split("OptionSettings=(", 1)[1]
        assert body.index("Difficulty=") < body.index("ExpRate=")
        assert body.index("ExpRate=") < body.index("PublicPort=")

    def test_carries_coop_values_across(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, changes = settings_mod.render_ini(values, default_ini_text)
        assert "ExpRate=1.500000" in ini
        assert "PalCaptureRate=2.000000" in ini
        assert "ExpRate" in changes

    def test_strips_enum_qualifier(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text)
        assert "Difficulty=Normal" in ini
        assert "EPalOptionWorldDifficulty" not in ini

    def test_quotes_string_values(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text)
        assert 'ServerName="My Co-op World"' in ini

    def test_drops_coop_only_keys(self, world_option_gvas, default_ini_text):
        """bIsMultiplay is a co-op session flag; a dedicated server sets it itself."""
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text)
        assert "bIsMultiplay=False" in ini

    def test_overrides_win(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text, {"ServerName": '"Dedicated"'})
        assert 'ServerName="Dedicated"' in ini
        assert "My Co-op World" not in ini

    def test_override_is_quoted_when_shell_ate_the_quotes(
        self, world_option_gvas, default_ini_text
    ):
        """
        Shells strip quotes, so --set can arrive as `ServerName=My Server`.
        That is paren-balanced and slips past validation while being malformed,
        so the renderer has to restore the default's quoting itself.
        """
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(
            values, default_ini_text, {"ServerName": "Utopia Planitia"}
        )
        assert 'ServerName="Utopia Planitia"' in ini
        assert "ServerName=Utopia Planitia," not in ini

    def test_override_quoting_not_applied_to_numeric_keys(
        self, world_option_gvas, default_ini_text
    ):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text, {"PublicPort": "8212"})
        assert "PublicPort=8212" in ini
        assert 'PublicPort="8212"' not in ini

    def test_override_strips_embedded_quotes(self, world_option_gvas, default_ini_text):
        """An embedded quote would terminate the struct early."""
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text, {"ServerName": 'sneaky"break'})
        assert 'ServerName="sneakybreak"' in ini
        settings_mod.validate_option_line(ini.splitlines()[1])

    def test_output_is_two_lines(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text)
        assert ini.splitlines()[0] == "[/Script/Pal.PalGameWorldSettings]"
        assert len(ini.splitlines()) == 2

    def test_nested_paren_value_survives(self, world_option_gvas, default_ini_text):
        values = settings_mod.extract(world_option_gvas)
        ini, _ = settings_mod.render_ini(values, default_ini_text)
        assert "CrossplayPlatforms=(Steam,Xbox,PS5,Mac)" in ini


class TestValidation:
    """A malformed struct makes the server silently revert every setting."""

    def test_accepts_balanced(self):
        settings_mod.validate_option_line("OptionSettings=(A=1,B=(X,Y))")

    def test_rejects_unclosed(self):
        with pytest.raises(PalMigrateError, match="left open"):
            settings_mod.validate_option_line("OptionSettings=(A=1")

    def test_rejects_extra_close(self):
        with pytest.raises(PalMigrateError, match="closed too many"):
            settings_mod.validate_option_line("OptionSettings=(A=1))")

    def test_rejects_multiline(self):
        with pytest.raises(PalMigrateError, match="one line"):
            settings_mod.validate_option_line("OptionSettings=(A=1,\nB=2)")

    def test_ignores_parens_inside_quotes(self):
        settings_mod.validate_option_line('OptionSettings=(Name="a (b) c")')

    def test_rejects_unbalanced_quotes(self):
        with pytest.raises(PalMigrateError, match="unbalanced quotes"):
            settings_mod.validate_option_line('OptionSettings=(Name="oops)')


class TestParseDefaultIni:
    def test_errors_without_option_settings(self):
        with pytest.raises(PalMigrateError, match="could not find OptionSettings"):
            settings_mod.parse_default_ini("[Section]\nNothing=here\n")

    def test_splits_top_level_commas_only(self, default_ini_text):
        pairs = dict(settings_mod.parse_default_ini(default_ini_text))
        assert pairs["CrossplayPlatforms"] == "(Steam,Xbox,PS5,Mac)"
        assert pairs["ServerName"] == '"Default Palworld Server"'
