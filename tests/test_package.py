"""Focused, standard-library-only checks for the OWARCHY source package."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import control_state  # noqa: E402

PLUGIN_ID = "io.github.dumpsteredition.owarchy"


class StateContractTests(unittest.TestCase):
    def test_defaults_are_disabled_with_curated_available_effects_and_45_seconds(self) -> None:
        state = control_state.default_state(
            ["matrix", "blackhole", "thunderstorm", "fireworks", "synthgrid", "other"],
            True,
        )
        self.assertEqual(
            state,
            {
                "enabled": False,
                "effects": ["matrix", "blackhole", "thunderstorm", "fireworks", "synthgrid"],
                "rotation_seconds": 45,
            },
        )
        self.assertEqual(control_state.default_state(["matrix"], False)["effects"], [])

    def test_settings_path_is_owned_and_relative_xdg_falls_back_to_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            xdg = Path(temp) / "xdg-config"
            with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)}):
                self.assertEqual(control_state.config_path(), xdg / "omarchy" / "owarchy.json")
            with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": "relative-config"}):
                self.assertEqual(control_state.config_path(), home / ".config" / "omarchy" / "owarchy.json")

    def test_cli_persists_empty_effects_and_does_not_import_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            xdg = root / "config"
            legacy = xdg / "omarchy" / "screensaver-wallpaper.json"
            legacy.parent.mkdir(parents=True)
            legacy_payload = {"enabled": True, "effects": ["matrix"], "rotation_seconds": 20}
            legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

            fake_bin = root / "bin"
            fake_bin.mkdir()
            ttfx = fake_bin / "ttfx"
            ttfx.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'Commands:' '  matrix  Matrix effect' '  blackhole  Blackhole effect' 'Options:'\n",
                encoding="utf-8",
            )
            ttfx.chmod(0o755)
            env = os.environ.copy()
            env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)})
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

            def invoke(*args: str) -> dict[str, object]:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "control_state.py"), *args],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            initial = invoke("get")
            self.assertEqual(initial["config_path"], str(xdg / "omarchy" / "owarchy.json"))
            self.assertEqual(
                initial["state"],
                {"enabled": False, "effects": ["matrix", "blackhole"], "rotation_seconds": 45},
            )
            own_path = xdg / "omarchy" / "owarchy.json"
            self.assertTrue(own_path.is_file())
            self.assertEqual(json.loads(legacy.read_text(encoding="utf-8")), legacy_payload)

            invoke("set-effect", "matrix", "off")
            empty = invoke("set-effect", "blackhole", "off")
            self.assertEqual(empty["state"]["effects"], [])
            saved = json.loads(own_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["effects"], [])
            self.assertEqual(saved["rotation_seconds"], 45)

            invoke("set-enabled", "on")
            restarted = invoke("get")
            self.assertEqual(restarted["state"]["enabled"], True)
            self.assertEqual(restarted["state"]["effects"], [])


class PackageIdentityTests(unittest.TestCase):
    def test_manifest_identity_and_entrypoints_match_package_files(self) -> None:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], PLUGIN_ID)
        self.assertEqual(manifest["name"], "OWARCHY")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(
            manifest["entryPoints"],
            {
                "service": "engine/Engine.qml",
                "barWidget": "OwarchyWidget.qml",
                "panel": "OwarchySettings.qml",
            },
        )
        for path in manifest["entryPoints"].values():
            self.assertTrue((ROOT / path).is_file(), path)

    def test_qml_helper_resolution_is_relative_and_ids_are_consistent(self) -> None:
        widget = (ROOT / "OwarchyWidget.qml").read_text(encoding="utf-8")
        settings = (ROOT / "OwarchySettings.qml").read_text(encoding="utf-8")
        helper = (ROOT / "control_state.py").read_text(encoding="utf-8")
        for source in (widget, settings):
            self.assertIn('Qt.resolvedUrl("control_state.py")', source)
            self.assertIn(PLUGIN_ID, source)
            self.assertNotIn("/.config/omarchy/plugins/", source)
        self.assertIn('PLUGIN_ID = "' + PLUGIN_ID + '"', helper)
        self.assertIn('"owarchy.json"', helper)
        self.assertIn("io.github.dumpsteredition.owarchy-settings", settings)

    def test_runtime_owned_labels_and_native_helper_name_are_renamed(self) -> None:
        engine = (ROOT / "engine" / "Engine.qml").read_text(encoding="utf-8")
        layer = (ROOT / "engine" / "vte_layer.py").read_text(encoding="utf-8")
        manager = (ROOT / "engine" / "manager.py").read_text(encoding="utf-8")
        native = (ROOT / "engine" / "owarchy-input-region.c").read_text(encoding="utf-8")
        self.assertIn('console.info("OWARCHY: "', engine)
        self.assertNotIn("screensaver-" + "background-v3", engine)
        self.assertIn("OWARCHY_HOST_READY", layer)
        self.assertIn('set_title("OWARCHY animated background")', layer)
        self.assertIn("libowarchy-input-region.so", manager)
        self.assertIn("owarchy_try_gtk_empty_input_region", layer)
        self.assertIn("owarchy_try_gtk_empty_input_region", native)

    def test_makefile_builds_bundled_c_source_with_gtk_and_wayland(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        source = (ROOT / "engine" / "owarchy-input-region.c").read_text(encoding="utf-8")
        self.assertIn("gtk+-3.0", makefile)
        self.assertIn("gdk-wayland-3.0", makefile)
        self.assertIn("wayland-client", makefile)
        self.assertIn("engine/owarchy-input-region.c", makefile)
        self.assertIn("owarchy_try_gtk_empty_input_region", source)
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
