# linux-computer-use

Claude-desktop-style **computer use for GNOME on Wayland**, as an MCP server for Claude Code
(or any MCP client). Claude takes screenshots of your desktop and drives it with the mouse,
keyboard, scroll wheel and clipboard, limited to the apps you approve.

It talks to Mutter directly via its RemoteDesktop and ScreenCast D-Bus APIs (the same ones
gnome-remote-desktop uses). That means:

- pixel-exact absolute pointer positioning at any fractional scale (no ydotool guesswork)
- keyboard input as keysyms: uppercase, symbols and shortcuts work regardless of layout;
  characters with no key in your layout (é, ✓, emoji) are pasted
- screenshots straight from a PipeWire stream, ~0.4 s, no portal and no files in `~/Pictures`
- real clipboard read/write
- GNOME's own screen-sharing indicator in the top bar while it is active

## Install

```sh
./install.sh            # copies to ~/.local/share/linux-computer-use and registers the MCP server
./install.sh --uninstall
```

Needs `python-gobject`, `gtk4`, `python-pillow`, `gst-plugin-pipewire` and `gst-plugins-base`.
The installer checks for them and prints the `pacman` line if any are missing.

It also:

- registers the server with Claude Code as user-scope MCP server **`linux-computer-use`** (Claude Code reserves `computer-use`), so the tools
  are named `mcp__linux-computer-use__*`
- binds **Super+Alt+Escape** to `lcu-stop`, the kill switch
- installs the **lcu-shell** GNOME Shell extension, which gives window-level hit testing. GNOME
  on Wayland only loads new extensions at login, so **log out and back in once** after
  installing. Until then the server works but can only check the frontmost app (via AT-SPI).

## Tools

The same surface as the Claude desktop app's computer use:

| Tool | Notes |
|---|---|
| `request_access` | `apps`, `reason`, optional `clipboardRead`, `clipboardWrite`, `systemKeyCombos`. Shows an approval dialog. Required first. |
| `list_granted_applications` | |
| `screenshot` | JPEG scaled to ≤1568 px long edge / ≤1.15 MP. All coordinates use this image's pixels. |
| `zoom` | `region: [x0,y0,x1,y1]`, full-resolution crop for small text |
| `left_click` `right_click` `middle_click` `double_click` `triple_click` | `coordinate`, optional `text` = modifiers (`"ctrl"`, `"shift+alt"`) |
| `mouse_move`, `left_click_drag`, `left_mouse_down`, `left_mouse_up` | |
| `scroll` | `coordinate`, `scroll_direction` up/down/left/right, `scroll_amount` (wheel clicks) |
| `type` | text |
| `key` | xdotool-style: `Return`, `ctrl+s`, `ctrl+shift+t`, `Page_Down`; space-separate for a sequence; `repeat` |
| `hold_key` | `text`, `duration` seconds |
| `cursor_position`, `wait` | |
| `open_application` | focuses an existing window of a granted app, else launches it |
| `read_clipboard`, `write_clipboard` | need the matching grant |
| `switch_display` | multi-monitor; omit `display` to list |
| `computer_batch` | `actions: [{action: "left_click", coordinate: [..]}, ...]` run in one call |

## Safety model

These rules mirror the desktop app:

- **Per-app approval.** Nothing works until you approve apps in the dialog. Grants last for
  the server process, which is one Claude Code session.
- **Tiers** come from each app's `.desktop` categories:
  - **Browsers** are *view only*: clicks and typing are refused. Use Claude in Chrome for web
    pages.
  - **Terminals and IDEs** are *click only*: left-click and scroll work; typing, keys,
    right/middle-click, modifier-clicks and drags are refused.
  - **Everything else** gets *full* control.
- **Where checks apply.** With the extension, every pointer action is checked against the
  window actually under the pointer, using Clutter's input pick. The check runs again after
  the hover settles, just before the button press. Keyboard actions are checked against the
  focused window. Shell UI (top bar, dash, overview, notifications) needs `systemKeyCombos`.
- **Lock screen and shell UI.** Input is refused while the screen is locked, or while the
  overview or a shell menu has keyboard focus.
- **System shortcuts** (Super…, Alt+Tab, Alt+F4, Ctrl+Alt+Del, Print) need `systemKeyCombos`.
- **Stopping.** Press **Super+Alt+Escape**, run `lcu-stop`, or click *Stop* on the
  screen-sharing indicator. Any of these revokes every grant, and Claude is told it was stopped.

## Tests

```sh
python3 tests/e2e_test.py        # on your real desktop: opens a throwaway window for ~15 s
tests/run_headless.sh            # same suite inside a private headless GNOME Shell with the
                                 # extension loaded; does not touch your desktop
LCU_HEADLESS_SIZE=2880x1800 LCU_HEADLESS_SCALE=1.6667 tests/run_headless.sh   # fractional scaling
tests/run_headless.sh python3 tests/approval_test.py      # drives the real approval dialog
LCU_HEADLESS_EXTRA="--virtual-monitor 1280x800" tests/run_headless.sh python3 tests/display_test.py
tests/run_headless.sh tests/claude_roundtrip.sh           # real Claude Code client -> server
```

The suite launches a small GTK window and checks what it actually received. It covers:

- clicks, right-click and the cursor position
- typing ASCII, then non-ASCII via paste
- key sequences, scrolling and zoom
- the clipboard round trip and `computer_batch`
- tier and shell blocking
- the kill switch

## Layout

```
lcu/mutter.py    RemoteDesktop/ScreenCast session: input, PipeWire capture, clipboard
lcu/server.py    MCP stdio server, tools, access + tier enforcement
lcu/apps.py      .desktop lookup, tier classification, launching
lcu/keys.py      key-name → keysym parsing, system-combo detection
lcu/shell.py     client for the lcu-shell extension (AT-SPI fallback)
lcu/approve.py   GTK4 approval dialog
extension/       lcu-shell GNOME Shell extension (org.lcu.Shell on the session bus)
```

## Notes

- The stream coordinate space for `NotifyPointerMotionAbsolute` is the stream's *buffer* pixels
  (e.g. 2880×1800 at 166 %), not the logical size in the stream's `Parameters`. This was
  verified empirically on GNOME 50.
- The org.lcu.Shell D-Bus API is readable by any process in your session. It exposes window
  titles and the pointer position, and can activate windows; it cannot inject input.
