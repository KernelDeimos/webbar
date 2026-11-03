# webbar

A web-tech status bar for [i3](https://i3wm.org/) — your bar is a plain HTML/CSS
page rendered in a borderless, transparent GTK + WebKit dock window. One process
drives a bar on every monitor, sharing a single web engine to keep memory down.
No Electron.

![webbar](docs/webbar.png)

## Why

Status bars like polybar are powerful but you style them in a bespoke config
language. webbar's whole premise is that **the bar is a web page** (`ui/index.html`):
restyle it with normal CSS, lay it out with normal HTML, and it **hot-reloads the
moment you save**. A small Python backend feeds it live data (workspaces, system
metrics, a system tray) and relays clicks back.

## Features

- **Per-monitor bars** from one process, sharing one WebKit `WebContext`.
- **Instant workspace updates** over the i3 IPC socket (no polling, no `i3-msg`
  subprocess in the hot path).
- **Number-over-icon workspaces** — the workspace emoji/glyph as a large
  translucent backdrop with the number on top; the focused one gets an accent
  underline, urgent ones ripple.
- **Live per-workspace screenshot thumbnails** behind each workspace icon.
- **polybar-style module scripts** — each module is a script that prints one line;
  run on an interval. CPU/RAM/swap render as little meter bars.
- **System tray** via a pure-Python StatusNotifier (SNI) host over DBus — no extra
  dependencies. (Modern SNI/AppIndicator apps; legacy XEmbed-only apps aren't covered.)
- **i3 mode indicator**, clock, and a click-to-cycle power-profile pill.
- Everything visual is in `ui/index.html` and **hot-reloads on save**.

## Requirements

- `python-gobject` (PyGObject, GTK 3)
- `webkit2gtk-4.1`
- `i3` and `xprop`
- A compositor for the transparency (e.g. `picom`)

Optional, used by bundled modules if present:

- `powerprofilesctl` (power-profile module + click-to-cycle)
- ImageMagick (`import`, `convert`) for the per-workspace screenshot thumbnails
- `wpctl` / `pactl` / `amixer` for the volume module
- `lm_sensors` (`sensors`) as a fallback for the temperature module

## Install & run

```sh
git clone https://github.com/KernelDeimos/webbar.git
cd webbar

./webbar           # a bar on every monitor
./webbar DP-2      # only this output
./webbar --stop    # stop a running webbar
```

Put it on your `PATH` (e.g. symlink `webbar` into `~/bin`) and autostart it from
your i3 config:

```
exec_always --no-startup-id /path/to/webbar/webbar
```

webbar is single-instance: launching it replaces any running instance.

## Configuration

A few constants at the top of `webbar`:

| name              | meaning                                                             |
|-------------------|--------------------------------------------------------------------|
| `HEIGHT`          | base bar height in px (at scale 1.0)                                |
| `BOTTOM_OUTPUTS`  | set of output names to dock at the **bottom** instead of the top    |
| `SCALES`          | per-output zoom for high-DPI displays, e.g. `{"eDP-1": 1.6}`        |
| `WS_SHOTS`        | toggle the live per-workspace screenshot thumbnails                 |
| `MODULES`         | the list of `{name, exec, interval}` module scripts                 |

Everything about the **look** lives in `ui/index.html` — colours are CSS custom
properties on `:root`; edit and save to see it live.

### Modules

A module is any executable that prints a single line to stdout; webbar runs it on
its interval and pushes the text to the page. Bundled scripts live in `scripts/`
(`cpu`, `mem`, `swap`, `temp`, `battery`, `powerprofile`, `volume`, `disk`). Add
your own by dropping a script in and adding an entry to `MODULES`.

`cpu`, `mem` and `swap` are special-cased in the page: their numeric value drives a
meter bar instead of a text pill.

## How it works

- Each monitor gets a `Gtk.Window` with the `DOCK` type hint, undecorated, an RGBA
  visual for real transparency, and EWMH struts (`_NET_WM_STRUT[_PARTIAL]`, set via
  `xprop` on the window's XID) so i3 reserves space for it.
- The window hosts a `WebKit2.WebView` loading `ui/index.html`. All bars share one
  `WebContext` to keep memory down.
- Workspaces come from the i3 IPC socket; the event socket is registered in GTK's
  own poll set (`GLib.unix_fd_add_full`) so incoming events wake the loop instantly.
- Module scripts run asynchronously (`Gio.Subprocess`) so a slow sampler never
  blocks the UI.
- The page talks back to Python through a `UserContentManager` script-message
  handler (`window.webkit.messageHandlers.bar`) for workspace switches, the
  power-profile click, and tray activation.

## License

[MIT](LICENSE) © Eric Dubé
