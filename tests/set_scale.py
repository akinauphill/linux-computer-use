"""Apply a temporary scale to every monitor (headless testing). Usage: set_scale.py 1.6666"""
import sys

from gi.repository import Gio, GLib

DC = "org.gnome.Mutter.DisplayConfig"
want = float(sys.argv[1])
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
serial, monitors, logical, _props = bus.call_sync(DC, "/org/gnome/Mutter/DisplayConfig", DC, "GetCurrentState",
                                                  None, None, Gio.DBusCallFlags.NONE, -1, None).unpack()
configs, x = [], 0
for spec, modes, _mprops in monitors:
    mode = next(m for m in modes if m[6].get("is-current"))
    scale = min(mode[5], key=lambda s: abs(s - want))
    configs.append((x, 0, scale, 0, x == 0, [(spec[0], mode[0], {})]))
    x += round(mode[1] / scale)
    print(f"{spec[0]}: {mode[1]}x{mode[2]} scale {scale:.4f} (supported {[round(s, 3) for s in mode[5]]})")
bus.call_sync(DC, "/org/gnome/Mutter/DisplayConfig", DC, "ApplyMonitorsConfig",
              GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, 1, configs, {})),
              None, Gio.DBusCallFlags.NONE, -1, None)
