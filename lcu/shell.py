"""Window information. Primary source is the lcu-shell GNOME Shell extension
(org.lcu.Shell on the session bus); without it we fall back to AT-SPI, which can
name the focused application but knows nothing about geometry or stacking."""
import json

import gi
from gi.repository import Gio, GLib

NAME, PATH, IFACE = "org.lcu.Shell", "/org/lcu/Shell", "org.lcu.Shell"


class Shell:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self._atspi = None

    def _call(self, method, args=None, rtype="(s)"):
        return self.bus.call_sync(NAME, PATH, IFACE, method, args, GLib.VariantType(rtype),
                                  Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()

    @property
    def available(self):
        try:
            self._call("FocusedWindow")
            return True
        except GLib.Error:
            return False

    def windows(self):
        try:
            return json.loads(self._call("ListWindows")[0])
        except GLib.Error:
            return None

    def focused(self):
        """{'wm_class','app_id','title',...} or None. Uses AT-SPI if the extension is missing."""
        try:
            return json.loads(self._call("FocusedWindow")[0])
        except GLib.Error:
            return self._atspi_focused()

    def window_at(self, x, y):
        """Window under a stage (logical) coordinate; False if unknown (no extension)."""
        try:
            return json.loads(self._call("WindowAt", GLib.Variant("(dd)", (float(x), float(y))))[0])
        except GLib.Error:
            return False

    def activate(self, win_id):
        try:
            return self._call("ActivateWindow", GLib.Variant("(t)", (int(win_id),)), "(b)")[0]
        except GLib.Error:
            return False

    def pointer(self):
        try:
            return self._call("GetPointer", None, "(dd)")
        except GLib.Error:
            return None

    # ---- AT-SPI fallback ---------------------------------------------------
    def _atspi_focused(self):
        try:
            if self._atspi is None:
                gi.require_version("Atspi", "2.0")
                from gi.repository import Atspi
                Atspi.init()
                self._atspi = Atspi
            Atspi = self._atspi
            desktop = Atspi.get_desktop(0)
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if app is None:
                    continue
                for j in range(min(app.get_child_count(), 50)):
                    win = app.get_child_at_index(j)
                    if win and win.get_state_set().contains(Atspi.StateType.ACTIVE):
                        name = app.get_name() or ""
                        return {"title": win.get_name() or "", "wm_class": name, "app_id": "",
                                "app_name": name, "pid": app.get_process_id(), "focused": True,
                                "source": "atspi"}
        except Exception:
            pass
        return None
