"""
Recover a co-op world's settings and render them as ``PalWorldSettings.ini``.

A co-op world stores its full option set in ``WorldOption.sav``. A dedicated
server ignores that file entirely and reads ``PalWorldSettings.ini`` instead --
which is why the migration guidance is always "do not copy WorldOption.sav
across". Extracting the values means the dedicated server can be configured to
match the world exactly, instead of the usual guesswork.

The ``OptionSettings=(...)`` struct is one very long single line. A single
malformed parenthesis makes the server silently discard *every* setting and
fall back to stock, so :func:`render_ini` validates before returning.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import PalMigrateError
from .gvas import parse

#: Settings that only make sense for a co-op session and should not be
#: carried onto a dedicated server.
COOP_ONLY_KEYS = frozenset({"bIsMultiplay"})


def extract(payload: bytes) -> dict[str, Any]:
    """Pull the settings dict out of a decoded ``WorldOption.sav`` payload."""
    _, props = parse(payload)
    world_data = props.get("OptionWorldData")
    if not isinstance(world_data, dict):
        raise PalMigrateError(
            "WorldOption.sav has no OptionWorldData; is this really a co-op world option file?"
        )
    settings = world_data.get("Settings")
    if not isinstance(settings, dict) or not settings:
        raise PalMigrateError("OptionWorldData contains no Settings block")
    return settings


def parse_default_ini(text: str) -> list[tuple[str, str]]:
    """
    Parse ``DefaultPalWorldSettings.ini`` into ordered ``(key, value)`` pairs.

    The shipped default file is the authority on key order and on how each
    value should be formatted, so we use it as the template.
    """
    match = re.search(r"OptionSettings=\((.*)\)\s*$", text, re.S | re.M)
    if not match:
        raise PalMigrateError("could not find OptionSettings=(...) in the default ini")

    pairs: list[tuple[str, str]] = []
    buffer = ""
    depth = 0
    in_quotes = False

    for char in match.group(1):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "(" and not in_quotes:
            depth += 1
        elif char == ")" and not in_quotes:
            depth -= 1

        if char == "," and depth == 0 and not in_quotes:
            pairs.append(_split_pair(buffer))
            buffer = ""
        else:
            buffer += char

    if buffer.strip():
        pairs.append(_split_pair(buffer))
    return pairs


def _split_pair(chunk: str) -> tuple[str, str]:
    key, _, value = chunk.partition("=")
    return key.strip(), value.strip()


def format_value(key: str, default_value: str, value: Any) -> str:
    """Format ``value`` using the shape implied by the default ini's value."""
    if isinstance(value, list):
        if default_value.startswith("("):
            names = [str(v).split("::")[-1] for v in value]
            return "(" + ",".join(names) + ")"
        return default_value
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str) and "::" in value:
        return value.split("::")[-1]
    if default_value.startswith('"'):
        return '"' + str(value) + '"'
    if re.fullmatch(r"-?\d+\.\d+", default_value):
        return f"{float(value):.6f}"
    if re.fullmatch(r"-?\d+", default_value):
        return str(int(value))
    return str(value)


def _quote_like_default(value: str, default_value: str) -> str:
    """
    Match the default's quoting when an override does not supply its own.

    Shells eat quotes. ``--set 'ServerName="My Server"'`` can arrive as
    ``ServerName=My Server``, which is paren-balanced and therefore sails past
    :func:`validate_option_line` while being a malformed value. If the shipped
    default for this key is a quoted string, quote the override too.
    """
    if not default_value.startswith('"'):
        return value
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value
    return '"' + value.replace('"', "") + '"'


def validate_option_line(line: str) -> None:
    """Raise unless ``line`` is a single line with balanced, quote-aware parens."""
    if "\n" in line or "\r" in line:
        raise PalMigrateError("OptionSettings must be exactly one line")

    depth = 0
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char == "(" and not in_quotes:
            depth += 1
        elif char == ")" and not in_quotes:
            depth -= 1
            if depth < 0:
                raise PalMigrateError("unbalanced parentheses: closed too many")
    if in_quotes:
        raise PalMigrateError("unbalanced quotes in OptionSettings")
    if depth != 0:
        raise PalMigrateError(f"unbalanced parentheses: {depth} left open")


def render_ini(
    settings: dict[str, Any],
    default_ini_text: str,
    overrides: dict[str, str] | None = None,
    *,
    drop_coop_only: bool = True,
) -> tuple[str, dict[str, tuple[str, str]]]:
    """
    Build ``PalWorldSettings.ini`` content.

    Returns ``(ini_text, changes)`` where ``changes`` maps each key that differs
    from the shipped default to ``(default_value, new_value)``.
    """
    overrides = overrides or {}
    defaults = parse_default_ini(default_ini_text)
    lowered = {k.lower(): v for k, v in settings.items()}

    rendered: list[str] = []
    changes: dict[str, tuple[str, str]] = {}

    for key, default_value in defaults:
        if key in overrides:
            value = _quote_like_default(overrides[key], default_value)
        elif drop_coop_only and key in COOP_ONLY_KEYS:
            value = default_value
        elif key.lower() in lowered:
            value = format_value(key, default_value, lowered[key.lower()])
        else:
            value = default_value

        if value != default_value:
            changes[key] = (default_value, value)
        rendered.append(f"{key}={value}")

    line = "OptionSettings=(" + ",".join(rendered) + ")"
    validate_option_line(line)
    return f"[/Script/Pal.PalGameWorldSettings]\n{line}\n", changes
