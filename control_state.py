#!/usr/bin/env python3
"""Read and atomically update OWARCHY's private settings for the QML controls."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ID = "io.github.dumpsteredition.owarchy"
CURATED_EFFECTS = ["matrix", "blackhole", "thunderstorm", "fireworks", "synthgrid"]
DEFAULT_ROTATION_SECONDS = 45
MIN_ROTATION_SECONDS = 5
MAX_ROTATION_SECONDS = 3600
COMMAND_RE = re.compile(r"^\s{2}([a-z][a-z0-9_-]*)\s{2,}\S")


def config_path() -> Path:
    home = Path(os.environ.get("HOME") or str(Path.home()))
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".config"
    return base / "omarchy" / "owarchy.json"


def discover_effects() -> tuple[list[str], str]:
    binary = shutil.which("ttfx")
    if not binary:
        return [], "ttfx was not found; effect choices are unavailable."
    try:
        result = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=4, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"Could not read `ttfx --help`: {exc}."
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        return [], f"Could not read `ttfx --help`: {detail}."

    in_commands = False
    names: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "Commands:":
            in_commands = True
            continue
        if in_commands and stripped in {"Options:", "Arguments:"}:
            break
        if not in_commands:
            continue
        match = COMMAND_RE.match(line)
        if match:
            name = match.group(1)
            if name != "help" and name not in names:
                names.append(name)
    if not names:
        return [], "`ttfx --help` did not list any effect commands."
    return names, ""


def default_state(available: list[str], discovery_ok: bool) -> dict[str, Any]:
    return {
        "enabled": False,
        "effects": [name for name in CURATED_EFFECTS if name in available] if discovery_ok else [],
        "rotation_seconds": DEFAULT_ROTATION_SECONDS,
    }


def clean_state(raw: Any, available: list[str] | None, discovery_ok: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    enabled = raw.get("enabled", False)
    enabled = enabled if isinstance(enabled, bool) else False

    seconds = raw.get("rotation_seconds", DEFAULT_ROTATION_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        seconds = DEFAULT_ROTATION_SECONDS
    try:
        finite_positive = math.isfinite(float(seconds)) and float(seconds) > 0
    except (OverflowError, TypeError, ValueError):
        finite_positive = False
    if not finite_positive:
        seconds = DEFAULT_ROTATION_SECONDS
    # Keep valid positive user values intact; the UI stepper writes a bounded
    # integer, while the shared engine contract accepts any positive number.
    effects: list[str] = []
    raw_effects = raw.get("effects", [])
    if isinstance(raw_effects, list):
        for value in raw_effects:
            if not isinstance(value, str) or value in effects:
                continue
            if discovery_ok and available is not None and value not in available:
                continue
            if not discovery_ok:
                # When ttfx cannot be queried, return no command names to the
                # caller; preserve the on-disk value only for a later recovery.
                continue
            effects.append(value)
    return {"enabled": enabled, "effects": effects, "rotation_seconds": seconds}


def read_raw(path: Path) -> tuple[Any, bool, str]:
    if not path.exists():
        return None, False, ""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle), True, ""
    except (OSError, json.JSONDecodeError) as exc:
        return None, True, f"Settings file could not be read; safe defaults are in use ({exc})."


def atomic_write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def emit(state: dict[str, Any], available: list[str], warning: str = "", error: str = "") -> None:
    print(json.dumps({
        "ok": not bool(error),
        "state": state,
        "available_effects": available,
        "config_path": str(config_path()),
        "warning": warning,
        "error": error,
    }, separators=(",", ":")))


def parse_effects_arg(value: str) -> str:
    if value not in {"on", "off"}:
        raise argparse.ArgumentTypeError("must be 'on' or 'off'")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("get", help="print current state and available ttfx effect commands")
    sub.add_parser("toggle", help="atomically flip enabled without changing selections")
    enabled = sub.add_parser("set-enabled", help="set enabled state without changing selections")
    enabled.add_argument("value", choices=("on", "off"))
    effect = sub.add_parser("set-effect", help="select or deselect one available ttfx effect")
    effect.add_argument("name")
    effect.add_argument("value", type=parse_effects_arg)
    rotation = sub.add_parser("set-rotation-seconds", help="set positive rotation interval in seconds")
    rotation.add_argument("value")
    return parser


def run(args: argparse.Namespace) -> int:
    path = config_path()
    available, discovery_warning = discover_effects()
    discovery_ok = not bool(discovery_warning)
    warning = discovery_warning
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")

    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw, existed, read_warning = read_raw(path)
        if read_warning:
            warning = (warning + " " + read_warning).strip()
        if not existed or read_warning:
            state = default_state(available, discovery_ok)
        else:
            state = clean_state(raw, available, discovery_ok)

        # A successful help query is the authority for the list: discard stale
        # or hand-edited invalid command names, including on read-only `get`.
        if discovery_ok:
            if not existed or read_warning or raw != state:
                atomic_write(path, state)
        elif args.command == "get":
            # Do not overwrite settings just because ttfx is temporarily absent.
            pass

        error = ""
        if args.command == "toggle":
            state["enabled"] = not state["enabled"]
        elif args.command == "set-enabled":
            state["enabled"] = args.value == "on"
        elif args.command == "set-effect":
            if not discovery_ok:
                error = "Cannot validate an effect until `ttfx --help` is available."
            elif args.name not in available:
                error = f"Unknown ttfx effect command: {args.name}"
            elif args.value == "on" and args.name not in state["effects"]:
                state["effects"].append(args.name)
            elif args.value == "off" and args.name in state["effects"]:
                state["effects"].remove(args.name)
        elif args.command == "set-rotation-seconds":
            try:
                seconds = float(args.value)
                if not math.isfinite(seconds) or seconds <= 0:
                    raise ValueError
                seconds = min(MAX_ROTATION_SECONDS, max(MIN_ROTATION_SECONDS, seconds))
                state["rotation_seconds"] = int(seconds) if seconds.is_integer() else seconds
            except ValueError:
                error = "Rotation time must be a finite positive number of seconds."

        if args.command != "get" and not error and discovery_ok:
            # Re-clean immediately before persistence so only real command
            # names can ever enter the shared state through this helper.
            state = clean_state(state, available, True)
            atomic_write(path, state)
        elif args.command != "get" and not error and not discovery_ok:
            # Preserve existing effect choices while the command catalog is
            # unavailable; only non-effect settings are updated. The QML view
            # receives an empty effect list and the warning above.
            try:
                with path.open("r", encoding="utf-8") as handle:
                    stored = json.load(handle)
                if not isinstance(stored, dict):
                    stored = {}
            except (OSError, json.JSONDecodeError):
                stored = {}
            stored["enabled"] = state["enabled"]
            stored["effects"] = stored.get("effects", []) if isinstance(stored.get("effects", []), list) else []
            stored["rotation_seconds"] = state["rotation_seconds"]
            atomic_write(path, stored)

        # Reflect the visible safe state, never unverified names.
        visible = clean_state(state, available, discovery_ok)
        emit(visible, available, warning, error)
        return 2 if error else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except OSError as exc:
        emit(default_state([], False), [], "", f"Local settings I/O failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
