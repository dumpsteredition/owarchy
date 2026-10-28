# OWARCHY

**OWARCHY — “warchin’ all the cool screensavers” — puts animated terminal-text effects on your Omarchy desktop as a live wallpaper.** Click the circle in the right side of the bar to turn it on or off; use the gear to open settings, choose effects, and set how often they rotate. A fresh setup starts **off**.

> **Community preview (0.1.0):** The proposed repository and marketplace listing are not yet public, and the plugin ID is not permanently reserved. Omarchy plugins run with your user permissions rather than in a sandbox; review the source before installing or enabling it.

## What it does

- Adds a circle toggle and settings button to Omarchy’s right-hand bar.
- Lets you choose which installed `ttfx` effects rotate and set the interval (45 seconds by default; available curated effects are selected for a fresh setup).
- Starts the wallpaper layer only when OWARCHY is enabled and at least one effect is selected. The default is off.
- Stores OWARCHY’s settings in `${XDG_CONFIG_HOME}/omarchy/owarchy.json` when `XDG_CONFIG_HOME` is absolute, or `$HOME/.config/omarchy/owarchy.json` otherwise.

**Settings and plugin files use different paths.** OWARCHY settings honor `XDG_CONFIG_HOME`; Omarchy installs plugins under `$HOME/.config/omarchy/plugins` even when `XDG_CONFIG_HOME` is set.

## Requirements and local checks

On Arch/Omarchy, install the runtime and build dependencies with:

```sh
omarchy pkg add vte3 gtk-layer-shell python-gobject gtk3 wayland gcc make pkgconf ttfx
```

This uses Omarchy’s package helper; it does not install anything during plugin validation. A Wayland Omarchy/Quickshell session is required to run the wallpaper. `make helper` builds the bundled C input-region helper from source; no prebuilt helper is included.

From a source checkout, build and run the focused package checks with:

```sh
make helper
make check
```

`make check` builds the helper and runs the Python package tests. `make clean` removes the generated helper and local Python bytecode caches.

## Install after publication

The repository URL below is proposed and is not yet published. Once an owner-approved public repository is available, add it without `--enable`. If Omarchy asks whether to enable the plugin during `add`, decline; build and validate it first.

```sh
omarchy plugin add https://github.com/dumpsteredition/owarchy.git
```

The installed plugin path is fixed by Omarchy’s installer, not by `XDG_CONFIG_HOME`. Build and validate the installed tree, review it, then explicitly enable it:

```sh
plugin_id='io.github.dumpsteredition.owarchy'
plugin_dir="$HOME/.config/omarchy/plugins/$plugin_id"

cd "$plugin_dir"
make helper
omarchy plugin validate "$plugin_dir"
# Review the source and build result before enabling.
omarchy plugin enable "$plugin_id"
```

Validation checks the Omarchy manifest and entry-point layout; it does not build the native helper or review plugin security.

## Update

Disable before updating. Omarchy still keeps the plugin under `$HOME/.config/omarchy/plugins`, regardless of `XDG_CONFIG_HOME`:

```sh
plugin_id='io.github.dumpsteredition.owarchy'
omarchy plugin disable "$plugin_id"
omarchy plugin update "$plugin_id"
plugin_dir="$HOME/.config/omarchy/plugins/$plugin_id"

cd "$plugin_dir"
make clean
make helper
omarchy plugin validate "$plugin_dir"
omarchy plugin enable "$plugin_id"
```

A plugin update is separate from any future marketplace listing or review.

## Disable, remove, and settings

```sh
plugin_id='io.github.dumpsteredition.owarchy'
omarchy plugin disable "$plugin_id"
omarchy plugin remove "$plugin_id"
```

Removing the plugin leaves OWARCHY’s settings in place. To remove only its settings and lock file, use the settings path (which does honor `XDG_CONFIG_HOME`):

```sh
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
case "$config_home" in /*) ;; *) config_home="$HOME/.config" ;; esac
rm -f -- "$config_home/omarchy/owarchy.json" \
  "$config_home/omarchy/owarchy.json.lock"
```

OWARCHY does not import the old local prototype’s `screensaver-wallpaper.json`. If you migrate preferences manually, do not overwrite an existing `owarchy.json`; copy only the effect selections and rotation interval you want, and set `enabled` to `false` rather than carrying over the old enabled state. Disable the old prototype before enabling OWARCHY.

## Limits

The animation loop currently targets 120 FPS; that target is not a promise of 120 displayed FPS. Actual frame rate and system load depend on the machine, compositor, and selected effect. There is no thermal-performance guarantee or multi-monitor certification. `make check` is a focused source/package check, not a compositor-session or performance certification.
