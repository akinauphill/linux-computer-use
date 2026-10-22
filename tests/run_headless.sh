#!/bin/bash
# Run a command inside a private headless GNOME Shell (own D-Bus session, virtual monitor,
# in-memory settings) with the lcu-shell extension installed and enabled. Nothing touches
# the real desktop.   Usage: tests/run_headless.sh [command...]   (default: the e2e test)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -z "${LCU_IN_HEADLESS:-}" ]; then
    export LCU_IN_HEADLESS=1
    exec dbus-run-session -- "$0" "$@"
fi
TMP=$(mktemp -d)
export PIPEWIRE_RUNTIME_DIR=${PIPEWIRE_RUNTIME_DIR:-$XDG_RUNTIME_DIR}
export XDG_DATA_HOME=$TMP/data XDG_CONFIG_HOME=$TMP/config XDG_RUNTIME_DIR=$TMP/run
export GSETTINGS_BACKEND=memory
mkdir -p "$XDG_DATA_HOME/gnome-shell/extensions" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
cp -r "$ROOT/extension/lcu-shell@linux-computer-use" "$XDG_DATA_HOME/gnome-shell/extensions/"
unset DISPLAY
gnome-shell --headless --wayland --no-x11 --wayland-display=lcu-test --virtual-monitor "${LCU_HEADLESS_SIZE:-1600x1000}" ${LCU_HEADLESS_EXTRA:-} \
    >"$TMP/shell.log" 2>&1 &
SHELL_PID=$!
trap 'kill $SHELL_PID 2>/dev/null; wait $SHELL_PID 2>/dev/null; rm -rf "$TMP"' EXIT
for _ in $(seq 100); do
    gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 && break
    sleep 0.2
done
gnome-extensions enable lcu-shell@linux-computer-use
for _ in $(seq 50); do
    gdbus call --session --dest org.lcu.Shell --object-path /org/lcu/Shell \
        --method org.lcu.Shell.FocusedWindow >/dev/null 2>&1 && break
    sleep 0.2
done
gnome-extensions info lcu-shell@linux-computer-use | grep -i state || true
# LCU_HEADLESS_SCALE=1.6667 exercises fractional scaling (stage != frame pixels)
if [ -n "${LCU_HEADLESS_SCALE:-}" ]; then
    python3 "$ROOT/tests/set_scale.py" "$LCU_HEADLESS_SCALE"
    sleep 1
fi
# the virtual pointer starts at (0,0), inside the hot corner: park it mid-screen first
python3 -c "import sys, time; sys.path.insert(0, '$ROOT'); from lcu.mutter import MutterSession
s = MutterSession(); s.start(); s.move(s.frame_size[0] / 2, s.frame_size[1] / 2); time.sleep(0.3); s.stop()"
sleep 0.3
# a fresh shell starts in the overview; leave it so the test starts on a normal desktop
gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
    --method org.freedesktop.DBus.Properties.Set org.gnome.Shell OverviewActive "<false>" >/dev/null || true
sleep 0.5
export WAYLAND_DISPLAY=lcu-test
if [ $# -eq 0 ]; then set -- python3 "$ROOT/tests/e2e_test.py"; fi
"$@"
