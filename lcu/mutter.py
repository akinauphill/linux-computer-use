"""Input injection, screen capture and clipboard through Mutter's private D-Bus APIs.

One RemoteDesktop session (with a linked ScreenCast session) is held for the life
of the process; GNOME shows its screen-sharing indicator while it is open, and the
user can end it from there at any time.

Coordinate space: NotifyPointerMotionAbsolute takes coordinates in the *buffer*
pixel space of the stream (physical pixels, e.g. 2880x1800 at 166% scale), not the
logical size Mutter advertises in the stream Parameters. Everything in this module
works in that frame-pixel space.
"""
import os
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402
from PIL import Image  # noqa: E402

Gst.init(None)

RD = "org.gnome.Mutter.RemoteDesktop"
SC = "org.gnome.Mutter.ScreenCast"
DC = "org.gnome.Mutter.DisplayConfig"

BTN = {"left": 0x110, "right": 0x111, "middle": 0x112, "back": 0x116, "forward": 0x115}

_loop_started = False


def _ensure_loop():
    """Signals are dispatched on the global default context; run it in a thread."""
    global _loop_started
    if not _loop_started:
        _loop_started = True
        threading.Thread(target=GLib.MainLoop().run, daemon=True, name="glib").start()


class SessionClosed(Exception):
    pass


def list_monitors():
    """[{connector, name, logical: (x, y, w, h), scale, primary}], primary first."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    _serial, monitors, logical, _props = bus.call_sync(
        DC, "/org/gnome/Mutter/DisplayConfig", DC, "GetCurrentState", None, None,
        Gio.DBusCallFlags.NONE, -1, None).unpack()
    names, modes = {}, {}
    for spec, mds, props in monitors:
        names[spec[0]] = props.get("display-name", spec[0])
        modes[spec[0]] = next((m for m in mds if m[6].get("is-current")), None)
    out = []
    for x, y, scale, _transform, primary, mons, _p in logical:
        for spec in mons:
            conn = spec[0]
            md = modes.get(conn)
            w, h = (round(md[1] / scale), round(md[2] / scale)) if md else (0, 0)
            out.append({"connector": conn, "name": names.get(conn, conn), "logical": (x, y, w, h),
                        "scale": scale, "primary": bool(primary)})
    out.sort(key=lambda m: (not m["primary"], m["logical"][0], m["logical"][1]))
    return out


class MutterSession:
    def __init__(self, connector=None, on_closed=None):
        _ensure_loop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.connector = connector
        self.on_closed = on_closed
        self.lock = threading.RLock()
        self.rd = self.sc = self.stream = None
        self.node = None
        self.frame_size = None  # (w, h) buffer pixels
        self.logical = None  # (x, y, w, h) from stream Parameters, in stage coordinates
        self.pointer = None  # last position we set, frame pixels
        self.closed = True
        self._self_stop = False
        self._subs = []
        self._clip_text = None
        self._clip_enabled = False
        self._pressed_keys = set()
        self._pressed_buttons = set()

    # ---- plumbing -------------------------------------------------------
    def _call(self, dest, path, iface, method, args=None, rtype=None, fds=None):
        vt = GLib.VariantType(rtype) if rtype else None
        if fds is not None:
            return self.bus.call_with_unix_fd_list_sync(dest, path, iface, method, args, vt,
                                                        Gio.DBusCallFlags.NONE, -1, fds, None)
        return self.bus.call_sync(dest, path, iface, method, args, vt, Gio.DBusCallFlags.NONE, -1, None)

    def _rd(self, method, args=None, rtype=None):
        if self.closed:
            raise SessionClosed("remote desktop session is not running")
        try:
            return self._call(RD, self.rd, RD + ".Session", method, args, rtype)
        except GLib.Error as e:
            if "UnknownObject" in str(e) or "UnknownMethod" in str(e):
                self._mark_closed()
                raise SessionClosed(str(e)) from e
            raise

    def _sub(self, sender, iface, member, path, cb):
        self._subs.append(self.bus.signal_subscribe(sender, iface, member, path, None,
                                                    Gio.DBusSignalFlags.NONE, cb))

    def _on_closed_signal(self, *_a):
        self._mark_closed(notify=not self._self_stop)

    def _mark_closed(self, notify=True):
        if self.closed:
            return
        self.closed = True
        for s in self._subs:
            self.bus.signal_unsubscribe(s)
        self._subs = []
        self._pressed_keys.clear()
        self._pressed_buttons.clear()
        if notify and self.on_closed:
            self.on_closed()

    def start(self):
        with self.lock:
            if not self.closed:
                return
            self.rd = self._call(RD, "/org/gnome/Mutter/RemoteDesktop", RD, "CreateSession", None, "(o)").unpack()[0]
            sid = self._call(RD, self.rd, "org.freedesktop.DBus.Properties", "Get",
                             GLib.Variant("(ss)", (RD + ".Session", "SessionId"))).unpack()[0]
            self.sc = self._call(SC, "/org/gnome/Mutter/ScreenCast", SC, "CreateSession",
                                 GLib.Variant("(a{sv})", ({"remote-desktop-session-id": GLib.Variant("s", sid)},)),
                                 "(o)").unpack()[0]
            # cursor-mode 0 = hidden: screenshots show the UI, not the pointer sprite
            self.stream = self._call(SC, self.sc, SC + ".Session", "RecordMonitor",
                                     GLib.Variant("(sa{sv})", (self.connector or "", {"cursor-mode": GLib.Variant("u", 0)})),
                                     "(o)").unpack()[0]
            got = threading.Event()

            def added(_c, _s, _p, _i, _sig, params):
                self.node = params.unpack()[0]
                got.set()

            self._sub(SC, SC + ".Stream", "PipeWireStreamAdded", self.stream, added)
            self._sub(RD, RD + ".Session", "Closed", self.rd, self._on_closed_signal)
            self._sub(RD, RD + ".Session", "SelectionTransfer", self.rd, self._on_selection_transfer)
            self._self_stop = False
            self.pointer = None
            self._call(RD, self.rd, RD + ".Session", "Start")
            self.closed = False
            if not got.wait(5):
                self.stop()
                raise RuntimeError("Mutter did not publish a PipeWire stream")
            params = self._call(SC, self.stream, "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", (SC + ".Stream", "Parameters"))).unpack()[0]
            self.logical = (*params.get("position", (0, 0)), *params["size"])
            self._clip_enabled = False
            self.capture()

    def stop(self):
        with self.lock:
            if self.closed:
                return
            self.release_all()
            self._self_stop = True
            try:
                self._call(RD, self.rd, RD + ".Session", "Stop")
            except GLib.Error:
                pass
            self._mark_closed(notify=False)

    # ---- coordinates ----------------------------------------------------
    def stage_to_frame(self, x, y):
        """Shell stage (logical) coordinates -> frame pixels of this stream."""
        lx, ly, lw, lh = self.logical
        fw, fh = self.frame_size
        return (x - lx) * fw / lw, (y - ly) * fh / lh

    def frame_to_stage(self, x, y):
        lx, ly, lw, lh = self.logical
        fw, fh = self.frame_size
        return lx + x * lw / fw, ly + y * lh / fh

    # ---- capture --------------------------------------------------------
    def capture(self):
        """Grab one full-resolution frame (PIL RGB image in frame pixels)."""
        with self.lock:
            if self.closed:
                raise SessionClosed("remote desktop session is not running")
            pipe = Gst.parse_launch(
                f"pipewiresrc path={self.node} always-copy=true ! "
                "videoconvert ! video/x-raw,format=RGB ! appsink name=s max-buffers=1 drop=true sync=false")
            sink = pipe.get_by_name("s")
            pipe.set_state(Gst.State.PLAYING)
            try:
                sample = sink.emit("try-pull-sample", 3 * Gst.SECOND)
                if sample is None:
                    raise RuntimeError("no frame received from screencast stream")
                st = sample.get_caps().get_structure(0)
                w, h = st.get_value("width"), st.get_value("height")
                buf = sample.get_buffer()
                ok, mi = buf.map(Gst.MapFlags.READ)
                try:
                    img = Image.frombuffer("RGB", (w, h), bytes(mi.data), "raw", "RGB",
                                           len(mi.data) // h, 1).copy()
                finally:
                    buf.unmap(mi)
                self.frame_size = (w, h)
                return img
            finally:
                pipe.set_state(Gst.State.NULL)

    # ---- pointer --------------------------------------------------------
    def move(self, x, y):
        w, h = self.frame_size
        x = min(max(float(x), 0.0), w - 1.0)
        y = min(max(float(y), 0.0), h - 1.0)
        self._rd("NotifyPointerMotionAbsolute", GLib.Variant("(sdd)", (self.stream, x, y)))
        if self.pointer is None:
            # Mutter can drop the y axis of the first event on a fresh virtual pointer
            time.sleep(0.02)
            self._rd("NotifyPointerMotionAbsolute", GLib.Variant("(sdd)", (self.stream, x, y)))
        self.pointer = (x, y)

    def button(self, name, pressed):
        self._rd("NotifyPointerButton", GLib.Variant("(ib)", (BTN[name], pressed)))
        (self._pressed_buttons.add if pressed else self._pressed_buttons.discard)(name)

    def scroll(self, dx_steps, dy_steps):
        """Discrete wheel clicks; positive = down / right."""
        if dy_steps:
            self._rd("NotifyPointerAxisDiscrete", GLib.Variant("(ui)", (0, int(dy_steps))))
        if dx_steps:
            self._rd("NotifyPointerAxisDiscrete", GLib.Variant("(ui)", (1, int(dx_steps))))

    # ---- keyboard -------------------------------------------------------
    def keysym(self, sym, pressed):
        self._rd("NotifyKeyboardKeysym", GLib.Variant("(ub)", (int(sym), pressed)))
        (self._pressed_keys.add if pressed else self._pressed_keys.discard)(sym)

    def release_all(self):
        for b in list(self._pressed_buttons):
            try:
                self.button(b, False)
            except Exception:
                pass
        for k in list(self._pressed_keys):
            try:
                self.keysym(k, False)
            except Exception:
                pass

    # ---- clipboard ------------------------------------------------------
    def _enable_clipboard(self):
        if not self._clip_enabled:
            self._rd("EnableClipboard", GLib.Variant("(a{sv})", ({},)))
            self._clip_enabled = True

    def _on_selection_transfer(self, _c, _s, _p, _i, _sig, params):
        _mime, serial = params.unpack()
        try:
            res, fdl = self._call(RD, self.rd, RD + ".Session", "SelectionWrite",
                                  GLib.Variant("(u)", (serial,)), "(h)", fds=Gio.UnixFDList.new())
            with os.fdopen(fdl.get(res.unpack()[0]), "wb") as f:
                f.write((self._clip_text or "").encode())
            ok = True
        except Exception:
            ok = False
        try:
            self._call(RD, self.rd, RD + ".Session", "SelectionWriteDone", GLib.Variant("(ub)", (serial, ok)))
        except Exception:
            pass

    def set_clipboard(self, text):
        self._enable_clipboard()
        self._clip_text = text
        mimes = ["text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT"]
        self._rd("SetSelection", GLib.Variant("(a{sv})", ({"mime-types": GLib.Variant("as", mimes)},)))

    def get_clipboard(self, timeout=3.0):
        self._enable_clipboard()
        last = None
        for mime in ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain"):
            try:
                res, fdl = self._call(RD, self.rd, RD + ".Session", "SelectionRead",
                                      GLib.Variant("(s)", (mime,)), "(h)", fds=Gio.UnixFDList.new())
            except GLib.Error as e:
                last = e
                continue
            fd = fdl.get(res.unpack()[0])
            chunks, deadline = [], time.monotonic() + timeout
            os.set_blocking(fd, False)
            try:
                while time.monotonic() < deadline:
                    try:
                        b = os.read(fd, 65536)
                    except BlockingIOError:
                        time.sleep(0.01)
                        continue
                    if not b:
                        break
                    chunks.append(b)
            finally:
                os.close(fd)
            return b"".join(chunks).decode("utf-8", "replace")
        if self._clip_text is not None:  # we own the selection, so Mutter won't read it back
            return self._clip_text
        raise RuntimeError(f"clipboard is empty or unreadable: {last}")
