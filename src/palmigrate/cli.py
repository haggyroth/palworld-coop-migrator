"""Command line interface for palmigrate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, container, scan
from . import guid as guid_mod
from . import settings as settings_mod
from .errors import PalMigrateError
from .gvas import parse

PLAYERS_DIRNAME = "Players"


def _find_player_guids(world_dir: Path) -> list[str]:
    """Player GUIDs present in ``<world>/Players``, excluding the co-op host."""
    players = world_dir / PLAYERS_DIRNAME
    if not players.is_dir():
        return []
    found: list[str] = []
    for path in sorted(players.glob("*.sav")):
        stem = path.stem
        if stem.endswith("_dps"):  # Pal-storage sidecar, not a player id
            continue
        if guid_mod.is_valid(stem) and not guid_mod.is_coop_host(stem):
            found.append(stem)
    return found


def cmd_inspect(args: argparse.Namespace) -> int:
    """Report the container format and basic facts about each save file."""
    target = Path(args.path)
    paths = sorted(target.glob("**/*.sav")) if target.is_dir() else [target]
    if not paths:
        print(f"no .sav files found under {target}", file=sys.stderr)
        return 1

    print(f"{'file':<52} {'format':<7} {'compressed':>12} {'uncompressed':>14}")
    print("-" * 90)
    failures = 0
    for path in paths:
        rel = path.relative_to(target) if target.is_dir() else path.name
        try:
            sav = container.read(path)
        except PalMigrateError as exc:
            print(f"{str(rel):<52} FAILED  {exc}")
            failures += 1
            continue
        print(
            f"{str(rel):<52} {sav.format_name:<7} "
            f"{sav.compressed_length:>12,} {sav.uncompressed_length:>14,}"
        )
        if args.verbose:
            header, _ = parse(sav.payload)
            print(
                f"{'':<52} class={header['save_game_class_name']} "
                f"engine={header['engine_version_major']}."
                f"{header['engine_version_minor']}."
                f"{header['engine_version_patch']}"
            )
    return 1 if failures else 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Show whether a GUID can be safely located by byte pattern."""
    level = Path(args.level)
    if not level.is_file():
        print(f"not a file: {level}", file=sys.stderr)
        return 1

    sav = container.read(level)
    references = args.reference or _find_player_guids(level.parent)
    if not references:
        print(
            "No reference GUIDs given and none discovered in ./Players. "
            "Pass --reference <guid> so the collision estimate has a baseline.",
            file=sys.stderr,
        )

    report = scan.build_report(sav.payload, args.guid, references)
    print(f"payload: {sav.uncompressed_length:,} bytes ({sav.format_name})\n")
    print(report.summary())
    return 0 if report.is_safe_to_byte_replace else 2


def cmd_settings(args: argparse.Namespace) -> int:
    """Extract co-op settings, optionally rendering PalWorldSettings.ini."""
    sav = container.read(args.world_option)
    values = settings_mod.extract(sav.payload)

    if not args.default_ini:
        print(f"{len(values)} settings recovered from {args.world_option}\n")
        for key, value in values.items():
            shown = f"{value:.6f}" if isinstance(value, float) else value
            print(f"  {key:<48} = {shown}")
        return 0

    overrides: dict[str, str] = {}
    for item in args.set or []:
        key, _, value = item.partition("=")
        if not key or not _:
            print(f"--set expects KEY=VALUE, got {item!r}", file=sys.stderr)
            return 1
        overrides[key.strip()] = value.strip()

    text = Path(args.default_ini).read_text(encoding="utf-8")
    ini, changes = settings_mod.render_ini(values, text, overrides)

    if args.output:
        # CRLF, matching what the server itself writes. Note that
        # Path.write_text() only grew a `newline` argument in 3.10, and this
        # package supports 3.9, so go through open() instead.
        with open(args.output, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(ini)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(ini)

    if changes:
        print(f"\n{len(changes)} setting(s) differ from the shipped default:", file=sys.stderr)
        for key, (old, new) in sorted(changes.items()):
            masked = "***" if "password" in key.lower() else new
            old_masked = "***" if "password" in key.lower() else old
            print(f"  {key:<44} {old_masked}  ->  {masked}", file=sys.stderr)
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    """Re-encode a save from PlM (Oodle) to PlZ (zlib), which the game accepts."""
    src = Path(args.input)
    dst = Path(args.output)
    sav = container.read(src)

    if dst.exists() and not args.force:
        print(f"{dst} exists; pass --force to overwrite", file=sys.stderr)
        return 1

    written = container.write(dst, sav.payload)
    verify = container.read(dst)
    if verify.payload != sav.payload:
        print("round-trip verification FAILED; output discarded", file=sys.stderr)
        dst.unlink(missing_ok=True)
        return 3

    print(
        f"{src.name}: {sav.format_name} ({sav.compressed_length:,} B) "
        f"-> {dst.name}: {verify.format_name} ({written:,} B), payload verified identical"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="palmigrate",
        description=(
            "Migrate a Palworld co-op world to a dedicated server. Reads the "
            "modern PlM (Oodle) save container that older tools cannot."
        ),
    )
    parser.add_argument("--version", action="version", version=f"palmigrate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="report save container formats")
    p_inspect.add_argument("path", help="a .sav file or a world directory")
    p_inspect.add_argument(
        "-v", "--verbose", action="store_true", help="also parse the GVAS header of each file"
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_scan = sub.add_parser(
        "scan",
        help="check whether a GUID is safe to locate by byte pattern",
    )
    p_scan.add_argument("level", help="path to Level.sav")
    p_scan.add_argument(
        "--guid",
        default=guid_mod.COOP_HOST_GUID,
        help="GUID to analyse (default: the co-op host id)",
    )
    p_scan.add_argument(
        "--reference", action="append", help="a known-good player GUID for comparison; repeatable"
    )
    p_scan.set_defaults(func=cmd_scan)

    p_settings = sub.add_parser(
        "settings",
        help="recover co-op world settings, optionally as PalWorldSettings.ini",
    )
    p_settings.add_argument("world_option", help="path to WorldOption.sav")
    p_settings.add_argument(
        "--default-ini", help="path to DefaultPalWorldSettings.ini; enables ini output"
    )
    p_settings.add_argument("-o", "--output", help="write the ini here instead of stdout")
    p_settings.add_argument(
        "--set", action="append", metavar="KEY=VALUE", help="override a setting; repeatable"
    )
    p_settings.set_defaults(func=cmd_settings)

    p_convert = sub.add_parser(
        "convert",
        help="re-encode a save as PlZ (zlib); the game upgrades it back to PlM",
    )
    p_convert.add_argument("input")
    p_convert.add_argument("output")
    p_convert.add_argument("--force", action="store_true", help="overwrite the output")
    p_convert.set_defaults(func=cmd_convert)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except PalMigrateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
