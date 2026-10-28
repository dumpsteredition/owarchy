#!/usr/bin/python3
"""Own the optional VTE host for the OWARCHY community-preview plugin."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from control_state import clean_state, config_path, default_state, discover_effects

STOP = False


def _stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def _parent_death_signal() -> None:
    """Exit promptly if the Quickshell Process which owns this manager vanishes."""
    parent = os.getppid()
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG
    except (AttributeError, OSError):
        return
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGTERM)


def _load_state(available: list[str], discovery_ok: bool) -> dict[str, Any]:
    try:
        with config_path().open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except FileNotFoundError:
        raw = default_state(available, discovery_ok)
    except (OSError, json.JSONDecodeError):
        raw = {}
    return clean_state(raw, available, discovery_ok)


def _host_environment() -> dict[str, str]:
    env = os.environ.copy()
    # Private staged libraries are test-only inputs. Installed deployments use
    # normal system library and GI typelib search paths without these variables.
    private_libdir = env.pop("VTE_PRIVATE_LIBDIR", "")
    private_typelib_dir = env.pop("VTE_PRIVATE_TYPELIB_DIR", "")
    if private_libdir:
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = os.pathsep.join(part for part in (private_libdir, current) if part)
    if private_typelib_dir:
        current = env.get("GI_TYPELIB_PATH", "")
        env["GI_TYPELIB_PATH"] = os.pathsep.join(part for part in (private_typelib_dir, current) if part)
    return env


class Supervisor:
    def __init__(self) -> None:
        self.host: subprocess.Popen[bytes] | None = None
        self.signature: str | None = None
        self.next_restart = 0.0
        self.restart_delay = 0.5
        self.host_script = Path(__file__).with_name("vte_layer.py")
        self.region_library = Path(__file__).with_name("libowarchy-input-region.so")

    def stop_host(self) -> None:
        process = self.host
        self.host = None
        if process is None or process.poll() is not None:
            return
        # Popen owns exactly this host PID; that host owns its VTE child PIDs.
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print(f"host {process.pid} did not stop after SIGTERM; sending SIGKILL to that owned PID", file=sys.stderr, flush=True)
            process.kill()
            process.wait(timeout=2)

    def start_host(self, state: dict[str, Any]) -> None:
        args = [
            sys.executable, "-u", str(self.host_script),
            "--effects", json.dumps(state["effects"], separators=(",", ":")),
            "--rotation-seconds", str(state["rotation_seconds"]),
            "--region-library", str(self.region_library),
            "--ttfx-frame-rate", "120",
        ]
        self.host = subprocess.Popen(args, env=_host_environment())
        print(f"host started pid={self.host.pid} effects={','.join(state['effects'])} rotation_seconds={state['rotation_seconds']}", flush=True)

    def run(self) -> int:
        available, warning = discover_effects()
        if warning:
            print(warning, file=sys.stderr, flush=True)
        discovery_ok = not bool(warning)
        while not STOP:
            state = _load_state(available, discovery_ok)
            desired_signature = json.dumps(state, sort_keys=True, separators=(",", ":"))
            if desired_signature != self.signature:
                self.stop_host()
                self.signature = desired_signature
                self.restart_delay = 0.5
                self.next_restart = 0.0

            active = state["enabled"] and bool(state["effects"])
            if not active:
                if self.host is not None:
                    self.stop_host()
            elif self.host is None and time.monotonic() >= self.next_restart:
                self.start_host(state)
            elif self.host is not None and self.host.poll() is not None:
                code = self.host.returncode
                print(f"host pid={self.host.pid} exited code={code}; retrying", file=sys.stderr, flush=True)
                self.host = None
                self.next_restart = time.monotonic() + self.restart_delay
                self.restart_delay = min(self.restart_delay * 2, 8.0)
            time.sleep(0.2)

        self.stop_host()
        return 0


def main() -> int:
    _parent_death_signal()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return Supervisor().run()


if __name__ == "__main__":
    raise SystemExit(main())
