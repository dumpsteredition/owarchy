#!/usr/bin/python3
"""One GtkLayerShell bottom terminal per GDK monitor; ttfx runs in each VTE PTY."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango, Vte

STOP = False
FONT = "JetBrainsMono Nerd Font 18"
GRID_SETTLE_MS = 250


def log(message: str, error: bool = False) -> None:
    print(message, file=sys.stderr if error else sys.stdout, flush=True)


def _stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def _parent_death_signal() -> None:
    parent = os.getppid()
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)  # Linux PR_SET_PDEATHSIG
    except (AttributeError, OSError):
        return
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGTERM)


def _available_effects(ttfx: str) -> set[str]:
    import re

    result = subprocess.run(
        [ttfx, "--help"], capture_output=True, text=True, timeout=10, check=True
    )
    names: set[str] = set()
    in_commands = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "Commands:":
            in_commands = True
            continue
        if in_commands and stripped in {"Options:", "Arguments:"}:
            break
        if in_commands:
            match = re.match(r"^\s{2}([a-z][a-z0-9_-]*)\s{2,}\S", line)
            if match and match.group(1) != "help":
                names.add(match.group(1))
    if not names:
        raise RuntimeError("ttfx --help did not expose any real effect commands")
    return names


def _branding_path() -> str:
    candidates: list[str | None] = [os.environ.get("OMARCHY_SCREENSAVER_TEXT")]
    home = Path(os.environ.get("HOME") or str(Path.home()))
    candidates.append(str(home / ".config/omarchy/branding/screensaver.txt"))
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg.startswith("/"):
        candidates.append(str(Path(xdg) / "omarchy/branding/screensaver.txt"))
    omarchy = os.environ.get("OMARCHY_PATH", "")
    if omarchy:
        candidates.append(str(Path(omarchy) / "logo.txt"))
    candidates.append("/usr/share/omarchy/logo.txt")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise FileNotFoundError("Omarchy screensaver branding was not found")


def _native_pointer(obj: Any) -> ctypes.c_void_p:
    raw = getattr(obj, "__gpointer__", None)
    if raw is None:
        raise RuntimeError("PyGObject object has no native GdkWindow pointer")
    if isinstance(raw, int):
        value = raw
    elif getattr(raw, "value", None) is not None:
        value = int(raw.value)
    else:
        get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
        get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
        get_pointer.restype = ctypes.c_void_p
        value = int(get_pointer(raw, None) or 0)
    if not value:
        raise RuntimeError("native GdkWindow pointer is NULL")
    return ctypes.c_void_p(value)


class Host:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.ttfx = shutil.which("ttfx")
        if not self.ttfx:
            raise FileNotFoundError("ttfx was not found on PATH")
        available = _available_effects(self.ttfx)
        self.effects = []
        for name in json.loads(args.effects):
            if isinstance(name, str) and name in available and name not in self.effects:
                self.effects.append(name)
        if not self.effects:
            raise ValueError("No selected effect remains in the real ttfx command catalog")
        self.branding = _branding_path()
        self.rotation_seconds = float(args.rotation_seconds)
        if self.rotation_seconds <= 0:
            raise ValueError("rotation interval must be positive")
        self.region = ctypes.CDLL(args.region_library)
        self.region.owarchy_try_gtk_empty_input_region.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t
        ]
        self.region.owarchy_try_gtk_empty_input_region.restype = ctypes.c_int
        self.display = Gdk.Display.get_default()
        if self.display is None:
            raise RuntimeError("GTK has no active GDK display")
        self.layers: list[TerminalLayer] = []
        self.stopping = False
        self.display.connect("monitor-added", self.on_monitor_added)
        self.display.connect("monitor-removed", self.on_monitor_removed)
        for index in range(self.display.get_n_monitors()):
            self.add_monitor(self.display.get_monitor(index))
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.request_stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.request_stop)
        log(f"OWARCHY_HOST_READY monitors={len(self.layers)} effects={','.join(self.effects)}")

    def add_monitor(self, monitor: Any) -> None:
        if self.stopping or any(layer.monitor == monitor for layer in self.layers):
            return
        try:
            layer = TerminalLayer(self, monitor)
            self.layers.append(layer)
            layer.show()
        except Exception as exc:
            log(f"OWARCHY_MONITOR_SETUP_FAILED error={type(exc).__name__}: {exc}", error=True)

    def on_monitor_added(self, _display: Any, monitor: Any) -> None:
        self.add_monitor(monitor)

    def on_monitor_removed(self, _display: Any, monitor: Any) -> None:
        for layer in list(self.layers):
            if layer.monitor == monitor:
                layer.stop("monitor-removed")

    def remove_layer(self, layer: "TerminalLayer") -> None:
        if layer in self.layers:
            self.layers.remove(layer)
        if self.stopping and not self.layers:
            Gtk.main_quit()

    def request_stop(self) -> bool:
        if not self.stopping:
            self.stopping = True
            log("OWARCHY_HOST_STOP requested")
            for layer in list(self.layers):
                layer.stop("supervisor-stop")
            if not self.layers:
                Gtk.main_quit()
        return GLib.SOURCE_REMOVE


class TerminalLayer:
    def __init__(self, host: Host, monitor: Any) -> None:
        self.host = host
        self.monitor = monitor
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("OWARCHY animated background")
        self.window.set_decorated(False)
        self.window.set_skip_taskbar_hint(True)
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.BOTTOM)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self.window, 0)
        GtkLayerShell.set_monitor(self.window, monitor)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self.window, edge, True)

        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(0)
        self.terminal.set_font(Pango.FontDescription.from_string(FONT))
        foreground = Gdk.RGBA()
        foreground.parse("#d7ffe0")
        background = Gdk.RGBA()
        background.parse("#000000")
        self.terminal.set_color_foreground(foreground)
        self.terminal.set_color_background(background)
        self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.OFF)
        self.terminal.set_can_focus(False)
        self.terminal.connect("child-exited", self.on_child_exited)
        self.terminal.connect("size-allocate", self.on_size_allocate)
        self.window.add(self.terminal)
        self.window.connect("delete-event", self.on_delete_event)
        self.window.connect("destroy", self.on_destroy)

        self.region_ready = False
        self.region_attempted = False
        self.child_pid: int | None = None
        self.spawn_pending = False
        self.settle_source = 0
        self.settle_snapshot: dict[str, int] | None = None
        self.stopping = False
        self.closed = False
        self.rotation_pending = False
        self.effect_index = 0
        self.rotation_source = 0
        self.retry_source = 0
        self.force_stop_source = 0

    def show(self) -> None:
        self.window.show_all()
        GLib.timeout_add(100, self.apply_empty_input_region)

    def apply_empty_input_region(self) -> bool:
        if self.stopping or self.region_attempted:
            return GLib.SOURCE_REMOVE
        if not self.window.get_mapped() or self.window.get_window() is None:
            return GLib.SOURCE_CONTINUE
        self.region_attempted = True
        try:
            info = ctypes.create_string_buffer(512)
            result = int(self.host.region.owarchy_try_gtk_empty_input_region(
                _native_pointer(self.window.get_window()), info, len(info)
            ))
            detail = info.value.decode("utf-8", errors="replace")
        except Exception as exc:
            result = -99
            detail = f"{type(exc).__name__}: {exc}"
        if result != 0:
            log(f"OWARCHY_INPUT_REGION_FAILED monitor={self.monitor.get_model()} rc={result} detail={detail}", error=True)
            # Fail closed: this mapped window is destroyed without ever starting ttfx.
            self.stop("empty-input-region-failed")
            return GLib.SOURCE_REMOVE
        self.region_ready = True
        log(f"OWARCHY_INPUT_REGION_READY monitor={self.monitor.get_model()} detail={detail}")
        self.maybe_spawn()
        return GLib.SOURCE_REMOVE

    def on_size_allocate(self, _widget: Any, _allocation: Any) -> None:
        if self.settle_source:
            GLib.source_remove(self.settle_source)
            self.settle_source = 0
        self.settle_snapshot = None
        self.maybe_spawn()

    def allocation_snapshot(self) -> dict[str, int] | None:
        if not self.window.get_mapped() or self.window.get_window() is None:
            return None
        window_allocation = self.window.get_allocation()
        terminal_allocation = self.terminal.get_allocation()
        snapshot = {
            "columns": int(self.terminal.get_column_count()),
            "rows": int(self.terminal.get_row_count()),
            "window_width": int(window_allocation.width),
            "window_height": int(window_allocation.height),
            "terminal_width": int(terminal_allocation.width),
            "terminal_height": int(terminal_allocation.height),
            "cell_width": int(self.terminal.get_char_width()),
            "cell_height": int(self.terminal.get_char_height()),
        }
        return snapshot if all(value > 0 for value in snapshot.values()) else None

    def maybe_spawn(self) -> bool:
        if self.stopping or self.closed or not self.region_ready or self.spawn_pending or self.child_pid:
            return GLib.SOURCE_REMOVE
        if self.settle_source:
            return GLib.SOURCE_REMOVE
        snapshot = self.allocation_snapshot()
        if snapshot is None:
            return GLib.SOURCE_REMOVE
        self.settle_snapshot = snapshot
        self.settle_source = GLib.timeout_add(GRID_SETTLE_MS, self.spawn_if_stable)
        return GLib.SOURCE_REMOVE

    def schedule_retry(self, interval_ms: int) -> None:
        source_id = 0

        def retry_spawn() -> bool:
            if self.retry_source == source_id:
                self.retry_source = 0
            return self.maybe_spawn()

        source_id = GLib.timeout_add(interval_ms, retry_spawn)
        self.retry_source = source_id

    def spawn_if_stable(self) -> bool:
        self.settle_source = 0
        if self.stopping or self.closed or not self.region_ready or self.spawn_pending or self.child_pid:
            self.settle_snapshot = None
            return GLib.SOURCE_REMOVE
        snapshot = self.allocation_snapshot()
        if snapshot is None:
            self.settle_snapshot = None
            return GLib.SOURCE_REMOVE
        if snapshot != self.settle_snapshot:
            self.settle_snapshot = snapshot
            self.settle_source = GLib.timeout_add(GRID_SETTLE_MS, self.spawn_if_stable)
            return GLib.SOURCE_REMOVE
        self.settle_snapshot = None
        columns = snapshot["columns"]
        rows = snapshot["rows"]
        effect = self.host.effects[self.effect_index]
        argv = [
            self.host.ttfx, "-i", self.host.branding,
            "--frame-rate", str(self.host.args.ttfx_frame_rate),
            "--canvas-width", str(columns), "--canvas-height", str(rows),
            "--reuse-canvas", "--anchor-canvas", "c", "--anchor-text", "c",
            "--no-eol", "--no-restore-cursor", effect,
        ]
        log(
            f"OWARCHY_GRID_SETTLED monitor={self.monitor.get_model()} columns={columns} rows={rows} "
            f"window={snapshot['window_width']}x{snapshot['window_height']} "
            f"terminal={snapshot['terminal_width']}x{snapshot['terminal_height']} "
            f"cell={snapshot['cell_width']}x{snapshot['cell_height']} debounce_ms={GRID_SETTLE_MS}"
        )
        self.spawn_pending = True
        log(
            f"OWARCHY_SPAWN_REQUEST effect={effect} columns={columns} rows={rows} "
            f"window={snapshot['window_width']}x{snapshot['window_height']} "
            f"terminal={snapshot['terminal_width']}x{snapshot['terminal_height']} "
            f"frame_rate={self.host.args.ttfx_frame_rate}"
        )
        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                os.environ.get("HOME", str(Path.home())),
                argv,
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                self.on_spawn_complete,
                None,
            )
        except Exception as exc:
            self.spawn_pending = False
            log(f"OWARCHY_SPAWN_FAILED error={type(exc).__name__}: {exc}", error=True)
            self.schedule_retry(1000)
        return GLib.SOURCE_REMOVE

    def on_spawn_complete(self, _terminal: Any, pid: int, error: Any, _user_data: Any = None) -> None:
        self.spawn_pending = False
        if error is not None:
            log(f"OWARCHY_SPAWN_FAILED error={error}", error=True)
            if not self.stopping:
                self.schedule_retry(1000)
            else:
                self.close_window()
            return
        self.child_pid = int(pid)
        if self.stopping:
            self._signal_owned_child(signal.SIGTERM)
            self.force_stop_source = GLib.timeout_add(1800, self.force_stop_child)
            return
        effect = self.host.effects[self.effect_index]
        log(f"OWARCHY_EFFECT_START effect={effect} pid={self.child_pid} parent={os.getpid()}")
        interval_ms = max(50, int(self.host.rotation_seconds * 1000))
        self.rotation_source = GLib.timeout_add(interval_ms, self.rotate_effect)

    def rotate_effect(self) -> bool:
        self.rotation_source = 0
        if self.stopping:
            return GLib.SOURCE_REMOVE
        self.rotation_pending = True
        self._signal_owned_child(signal.SIGTERM)
        return GLib.SOURCE_REMOVE

    def on_child_exited(self, _terminal: Any, status: int) -> None:
        old_pid = self.child_pid
        self.child_pid = None
        if self.rotation_source:
            GLib.source_remove(self.rotation_source)
            self.rotation_source = 0
        if self.force_stop_source:
            GLib.source_remove(self.force_stop_source)
            self.force_stop_source = 0
        log(f"OWARCHY_CHILD_EXIT effect={self.host.effects[self.effect_index]} pid={old_pid} status={status} stopping={self.stopping}")
        if self.stopping:
            self.close_window()
            return
        if self.rotation_pending or status == 0:
            self.rotation_pending = False
            self.effect_index = (self.effect_index + 1) % len(self.host.effects)
        else:
            # Omarchy's stock screensaver may terminate ttfx by PID/name when
            # it exits. Restart this VTE-reported child only; never search by name.
            log(f"OWARCHY_CHILD_RESTART effect={self.host.effects[self.effect_index]}")
        self.schedule_retry(250)

    def _signal_owned_child(self, signum: int) -> None:
        pid = self.child_pid
        if pid is None:
            return
        try:
            os.kill(pid, signum)  # exact PID returned by this VTE spawn callback
        except ProcessLookupError:
            pass

    def force_stop_child(self) -> bool:
        self.force_stop_source = 0
        if self.stopping and self.child_pid is not None:
            log(f"OWARCHY_CHILD_FORCE_STOP exact_pid={self.child_pid}", error=True)
            self._signal_owned_child(signal.SIGKILL)
        return GLib.SOURCE_REMOVE

    def stop(self, reason: str) -> None:
        if self.closed:
            return
        if not self.stopping:
            self.stopping = True
            log(f"OWARCHY_LAYER_STOP reason={reason}")
        if self.rotation_source:
            GLib.source_remove(self.rotation_source)
            self.rotation_source = 0
        if self.retry_source:
            GLib.source_remove(self.retry_source)
            self.retry_source = 0
        if self.settle_source:
            GLib.source_remove(self.settle_source)
            self.settle_source = 0
        self.settle_snapshot = None
        if self.child_pid is not None:
            self._signal_owned_child(signal.SIGTERM)
            if not self.force_stop_source:
                self.force_stop_source = GLib.timeout_add(1800, self.force_stop_child)
        elif not self.spawn_pending:
            self.close_window()

    def close_window(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.window.destroy()
        finally:
            self.host.remove_layer(self)

    def on_delete_event(self, _window: Any, _event: Any) -> bool:
        self.stop("window-close-request")
        return True

    def on_destroy(self, _window: Any) -> None:
        if not self.closed:
            self.stop("window-destroyed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effects", required=True, help="JSON array of actual ttfx command names")
    parser.add_argument("--rotation-seconds", required=True, type=float)
    parser.add_argument("--region-library", required=True)
    parser.add_argument("--ttfx-frame-rate", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.ttfx_frame_rate <= 120:
        parser.error("ttfx frame rate must be 1..120")
    if args.rotation_seconds <= 0:
        parser.error("rotation interval must be positive")
    try:
        effects = json.loads(args.effects)
    except json.JSONDecodeError as exc:
        parser.error(f"--effects must be JSON: {exc}")
    if not isinstance(effects, list):
        parser.error("--effects must be a JSON array")
    return args


def main() -> int:
    _parent_death_signal()
    try:
        host = Host(parse_args())
        Gtk.main()
        if not host.stopping:
            host.request_stop()
        return 0
    except Exception as exc:
        log(f"OWARCHY_HOST_ERROR {type(exc).__name__}: {exc}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
