#!/bin/bash
# Install linux-computer-use for the current user:
#   - package copied to ~/.local/share/linux-computer-use (so the checkout can move or go away)
#   - linux-computer-use + lcu-stop in ~/.local/bin
#   - Super+Alt+Escape kill-switch shortcut
#   - lcu-shell GNOME Shell extension (active after your next login)
#   - registered with Claude Code as the user-scope MCP server "linux-computer-use"
# Re-running it upgrades in place. ./install.sh --uninstall removes everything it added.
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd)
DEST=$HOME/.local/share/linux-computer-use
BIN=$HOME/.local/bin
EXT_UUID=lcu-shell@linux-computer-use
EXT_DIR=$HOME/.local/share/gnome-shell/extensions/$EXT_UUID
KB_PATH=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/lcu-stop/
KB_SCHEMA=org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB_PATH
MCP_NAME=linux-computer-use

remove_keybinding() {
    local cur new
    cur=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
    new=$(python3 -c 'import ast,sys; c=sys.argv[1].removeprefix("@as "); print([p for p in ast.literal_eval(c) if p != sys.argv[2]])' "$cur" "$KB_PATH")
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new"
    gsettings reset-recursively "$KB_SCHEMA" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
    command -v claude >/dev/null && claude mcp remove --scope user "$MCP_NAME" 2>/dev/null || true
    gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
    rm -rf "$EXT_DIR" "$DEST" "$BIN/linux-computer-use" "$BIN/lcu-stop"
    remove_keybinding
    echo "Uninstalled."
    exit 0
fi

echo "== checking dependencies"
missing=()
python3 -c 'import gi; gi.require_version("Gst", "1.0"); gi.require_version("Gtk", "4.0")' 2>/dev/null || missing+=(python-gobject gtk4 gstreamer)
python3 -c 'import PIL' 2>/dev/null || missing+=(python-pillow)
gst-inspect-1.0 pipewiresrc >/dev/null 2>&1 || missing+=(gst-plugin-pipewire)
gst-inspect-1.0 videoconvert >/dev/null 2>&1 || missing+=(gst-plugins-base)
if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing packages: ${missing[*]}"
    echo "Install them with:  sudo pacman -S --needed ${missing[*]}"
    exit 1
fi
[ "${XDG_SESSION_TYPE:-}" = wayland ] && [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] ||
    echo "warning: this is built for GNOME on Wayland (found ${XDG_CURRENT_DESKTOP:-?}/${XDG_SESSION_TYPE:-?})"

echo "== installing package to $DEST"
rm -rf "$DEST"
mkdir -p "$DEST" "$BIN"
cp -r "$SRC/lcu" "$DEST/"
find "$DEST" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 "$SRC/bin/linux-computer-use" "$SRC/bin/lcu-stop" "$BIN/"

echo "== kill switch: Super+Alt+Escape -> lcu-stop"
remove_keybinding
cur=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
new=$(python3 -c 'import ast,sys; c=sys.argv[1].removeprefix("@as "); print(ast.literal_eval(c)+[sys.argv[2]])' "$cur" "$KB_PATH")
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new"
gsettings set "$KB_SCHEMA" name "Stop Claude computer use"
gsettings set "$KB_SCHEMA" command "$BIN/lcu-stop"
gsettings set "$KB_SCHEMA" binding "<Super><Alt>Escape"

echo "== GNOME Shell extension $EXT_UUID"
rm -rf "$EXT_DIR"
mkdir -p "$(dirname "$EXT_DIR")"
cp -r "$SRC/extension/$EXT_UUID" "$EXT_DIR"
if gdbus call --session --dest org.lcu.Shell --object-path /org/lcu/Shell \
        --method org.lcu.Shell.FocusedWindow >/dev/null 2>&1; then
    echo "   already running (log out and in to load an updated version)"
else
    gnome-extensions enable "$EXT_UUID" 2>/dev/null ||
        gsettings set org.gnome.shell enabled-extensions \
            "$(python3 -c 'import ast,sys; c=sys.argv[1].removeprefix("@as "); l=ast.literal_eval(c); print(l if sys.argv[2] in l else l+[sys.argv[2]])' \
               "$(gsettings get org.gnome.shell enabled-extensions)" "$EXT_UUID")"
    echo "   installed and enabled; GNOME on Wayland loads new extensions at your next login."
    echo "   Until then the server falls back to AT-SPI (frontmost-app checks only)."
fi

if command -v claude >/dev/null; then
    echo "== registering MCP server '$MCP_NAME' with Claude Code (user scope)"
    claude mcp remove --scope user "$MCP_NAME" >/dev/null 2>&1 || true
    claude mcp add --scope user "$MCP_NAME" -- "$BIN/linux-computer-use"
else
    echo "== Claude Code not found; register manually:  claude mcp add --scope user $MCP_NAME -- $BIN/linux-computer-use"
fi
echo "Done. Tools appear as mcp__${MCP_NAME}__* in new Claude Code sessions."
