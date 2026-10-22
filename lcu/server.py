"""MCP server (stdio, JSON-RPC 2.0) exposing Claude-desktop-style computer-use tools
for GNOME on Wayland."""
import base64
import io
import json
import math
import os
import subprocess
import sys
import time
import traceback

from . import __version__, apps as appdb, keys
from .mutter import MutterSession, SessionClosed, list_monitors
from .shell import Shell

MAX_LONG_EDGE = 1568
MAX_PIXELS = 1_150_000
RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "lcu")
STOP_FILE = os.path.join(RUNTIME, "stop")
TIER_RANK = {appdb.TIER_READ: 0, appdb.TIER_CLICK: 1, appdb.TIER_FULL: 2}
TIER_EXPLAIN = {
    appdb.TIER_READ: "view only: visible in screenshots; clicks and typing are blocked. Use a browser "
                     "automation tool (Claude in Chrome) for web pages.",
    appdb.TIER_CLICK: "click only: left-click and scroll work; typing, key presses, right-click, "
                      "modifier-clicks and drag are blocked. Use your shell tool for commands.",
    appdb.TIER_FULL: "full control",
}


def log(*a):
    print("[lcu]", *a, file=sys.stderr, flush=True)


class ToolError(Exception):
    pass


def fit_size(w, h):
    s = min(1.0, MAX_LONG_EDGE / max(w, h), math.sqrt(MAX_PIXELS / (w * h)))
    return max(1, round(w * s)), max(1, round(h * s))


def encode(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True)
    return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"}


def text(s):
    return {"type": "text", "text": s}


class Computer:
    def __init__(self):
        self.shell = Shell()
        self.session = None
        self.connector = None
        self.grants = {}  # base_id -> App
        self.flags = {"clipboardRead": False, "clipboardWrite": False, "systemKeyCombos": False}
        self.shot_size = None  # (w, h) of the last screenshot we returned
        self.stopped_reason = None
        os.makedirs(RUNTIME, exist_ok=True)

    # ---- session / safety ---------------------------------------------------
    def _on_closed(self):
        if self.grants:
            self.revoke("the user ended the remote-desktop session from the GNOME screen-sharing indicator")

    def revoke(self, why):
        self.grants.clear()
        for k in self.flags:
            self.flags[k] = False
        self.stopped_reason = why
        log("access revoked:", why)

    def _check_stop(self):
        if os.path.exists(STOP_FILE):
            try:
                os.unlink(STOP_FILE)
            except OSError:
                pass
            self.revoke("the user pressed the computer-use kill switch")
            self._end_session()
        if self.stopped_reason:
            why, self.stopped_reason = self.stopped_reason, None
            raise ToolError(f"Computer use was stopped: {why}. All app access has been revoked. Do not "
                            "continue the task with computer use; ask the user how they want to proceed.")

    def _end_session(self):
        if self.session and not self.session.closed:
            self.session.stop()

    def _require_access(self):
        self._check_stop()
        if not self.grants:
            raise ToolError("No applications are granted. Call request_access with the apps you need first.")

    def _sess(self):
        if self.session is None:
            self.session = MutterSession(self.connector, on_closed=self._on_closed)
        if self.session.closed:
            self.session.start()
        return self.session

    # ---- coordinates --------------------------------------------------------
    def _scale(self):
        s = self._sess()
        fw, fh = s.frame_size
        sw, sh = self.shot_size or fit_size(fw, fh)
        return fw / sw, fh / sh

    def to_frame(self, coord):
        if not isinstance(coord, (list, tuple)) or len(coord) != 2:
            raise ToolError("coordinate must be [x, y]")
        sx, sy = self._scale()
        sw, sh = self.shot_size or fit_size(*self._sess().frame_size)
        x, y = float(coord[0]), float(coord[1])
        if not (0 <= x < sw and 0 <= y < sh):
            raise ToolError(f"coordinate {coord} is outside the screenshot ({sw}x{sh})")
        return (x + 0.5) * sx, (y + 0.5) * sy

    def from_frame(self, x, y):
        sx, sy = self._scale()
        return round(x / sx), round(y / sy)

    # ---- tier enforcement ---------------------------------------------------
    def _granted_app_for(self, win):
        if not win:
            return None
        cands = list(self.grants.values())
        for key in ("app_id", "gtk_app_id", "sandboxed_app_id"):
            v = win.get(key)
            if v:
                a = appdb.match_window("", v if v.endswith(".desktop") else v + ".desktop", cands)
                if a:
                    return a
        return appdb.match_window(win.get("wm_class", ""), win.get("app_id", ""), cands) or \
            appdb.match_window(win.get("app_name", ""), "", cands)

    def _win_label(self, win):
        return win.get("app_name") or win.get("wm_class") or win.get("title") or "unknown app"

    def _check_window(self, win, need, what):
        if win is None:
            return
        app = self._granted_app_for(win)
        if app is None:
            raise ToolError(f"{what} blocked: '{self._win_label(win)}' is not in the granted applications. "
                            "Use open_application to bring a granted app to the front, or request_access "
                            "to ask the user for this app.")
        if TIER_RANK[app.tier] < TIER_RANK[need]:
            raise ToolError(f"{what} blocked: {app.name} is granted at tier '{app.tier}' "
                            f"({TIER_EXPLAIN[app.tier]})")

    def _gate(self, need, what, frame_xy=None):
        """Pointer actions are checked against the window under the pointer (falling back to
        the frontmost window without the shell extension); keyboard actions against the
        frontmost window."""
        self._require_access()
        st = self.shell.state()
        if st and st.get("locked"):
            raise ToolError(f"{what} blocked: the screen is locked. Ask the user to unlock it.")
        if st and frame_xy is None and (st.get("overview") or st.get("modal")) and not self.flags["systemKeyCombos"]:
            raise ToolError(f"{what} blocked: GNOME Shell UI (overview, menu or dialog) has keyboard focus, "
                            "not a granted app. Use open_application or click into the app first.")
        win = False
        if frame_xy is not None:
            win = self.shell.window_at(*self._sess().frame_to_stage(*frame_xy))
        if win is False:
            self._check_window(self.shell.focused(), need, what)
            return
        if win is None or win.get("shell"):
            if not self.flags["systemKeyCombos"]:
                raise ToolError(f"{what} blocked: that point is GNOME Shell UI (top bar, dash, overview, "
                                "notification or desktop background), not a granted app window. Request "
                                "systemKeyCombos access to interact with the shell."
                                + (f" [picked: {win['actor']}]" if win and win.get("actor") else ""))
            return
        self._check_window(win.get("owner") or win, need, what)

    # ---- tools: access --------------------------------------------------------
    def request_access(self, apps=None, reason="", clipboardRead=False, clipboardWrite=False,
                       systemKeyCombos=False, **_):
        if os.path.exists(STOP_FILE):
            os.unlink(STOP_FILE)
        self.stopped_reason = None
        installed = appdb.all_apps()
        resolved, unresolved = [], []
        for name in apps or []:
            a = appdb.resolve(name, installed)
            if a is None:
                unresolved.append(name)
            elif a.base_id not in self.grants and all(r.base_id != a.base_id for r in resolved):
                resolved.append(a)
        want = {k: v for k, v in (("clipboardRead", clipboardRead), ("clipboardWrite", clipboardWrite),
                                  ("systemKeyCombos", systemKeyCombos)) if v and not self.flags[k]}
        if resolved or want:
            req = {"reason": reason, "apps": [a.to_json() for a in resolved], "unresolved": unresolved, **want}
            if os.environ.get("LCU_TEST_AUTO_APPROVE") == "1":
                resp = {"approved": True, "apps": [a.base_id for a in resolved], **want}
            else:
                resp = self._ask_user(req)
            if resp.get("approved"):
                for a in resolved:
                    if a.base_id in resp.get("apps", []):
                        self.grants[a.base_id] = a
                for k in want:
                    self.flags[k] = bool(resp.get(k))
                if self.grants:
                    self._notify()
            denied = [a.name for a in resolved if a.base_id not in self.grants]
        else:
            denied = []
        out = {
            "granted": [{**a.to_json(), "access": TIER_EXPLAIN[a.tier]} for a in self.grants.values()],
            "denied": denied,
            "not_found": unresolved,
            **self.flags,
        }
        if unresolved:
            out["hint"] = "Unrecognised names: use the app's display name as shown in the GNOME app grid."
        return [text(json.dumps(out, indent=1))]

    def _ask_user(self, req):
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            p = subprocess.run([sys.executable, "-m", "lcu.approve"], input=json.dumps(req), text=True,
                               capture_output=True, cwd=pkg_root, timeout=330)
            return json.loads(p.stdout or "{}")
        except Exception as e:
            log("approval dialog failed:", e)
            raise ToolError(f"Could not show the approval dialog: {e}")

    def _notify(self):
        try:
            subprocess.Popen(["notify-send", "-a", "Claude", "-i", "input-mouse",
                              "Claude is using your computer",
                              "Allowed: " + ", ".join(a.name for a in self.grants.values()) +
                              "\nPress Super+Alt+Escape to stop."],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def list_granted_applications(self, **_):
        self._check_stop()
        return [text(json.dumps({"granted": [{**a.to_json(), "access": TIER_EXPLAIN[a.tier]}
                                             for a in self.grants.values()], **self.flags}, indent=1))]

    # ---- tools: screen ---------------------------------------------------------
    def screenshot(self, **_):
        self._require_access()
        s = self._sess()
        img = s.capture()
        size = fit_size(*img.size)
        self.shot_size = size
        out = [encode(img.resize(size, resample=3))]
        notes = []
        foc = self.shell.focused()
        if foc and not self._granted_app_for(foc):
            notes.append(f"Note: the frontmost app ({self._win_label(foc)}) is not granted; input to it is blocked.")
        if notes:
            out.append(text(" ".join(notes)))
        return out

    def zoom(self, region=None, **_):
        self._require_access()
        if not region or len(region) != 4:
            raise ToolError("region must be [x0, y0, x1, y1] in screenshot coordinates")
        s = self._sess()
        sx, sy = self._scale()
        x0, y0, x1, y1 = (float(v) for v in region)
        if x1 <= x0 or y1 <= y0:
            raise ToolError("region must have x1 > x0 and y1 > y0")
        img = s.capture()
        box = (max(0, round(x0 * sx)), max(0, round(y0 * sy)),
               min(img.width, round(x1 * sx)), min(img.height, round(y1 * sy)))
        crop = img.crop(box)
        w, h = fit_size(crop.width * 4, crop.height * 4)
        w, h = min(w, crop.width * 2), min(h, crop.height * 2)  # never blow up more than 2x
        return [encode(crop.resize((max(1, w), max(1, h)), resample=3))]

    def cursor_position(self, **_):
        self._require_access()
        s = self._sess()
        p = self.shell.pointer()
        if p is not None:
            fx, fy = s.stage_to_frame(*p)
        elif s.pointer is not None:
            fx, fy = s.pointer
        else:
            raise ToolError("Cursor position is unknown until the mouse has been moved (install the "
                            "lcu-shell extension for the live position).")
        x, y = self.from_frame(fx, fy)
        return [text(json.dumps({"x": x, "y": y}))]

    def switch_display(self, display=None, **_):
        self._require_access()
        mons = list_monitors()
        if not display:
            return [text(json.dumps([{"name": m["name"], "connector": m["connector"], "primary": m["primary"]}
                                     for m in mons]))]
        d = display.lower()
        m = next((m for m in mons if d in (m["connector"].lower(), m["name"].lower())), None) or \
            next((m for m in mons if d in m["name"].lower()), None)
        if not m:
            raise ToolError(f"No display named {display!r}. Available: " + ", ".join(
                f"{m['name']} ({m['connector']})" for m in mons))
        self._end_session()
        self.session = None
        self.connector = m["connector"]
        self.shot_size = None
        self._sess()
        return [text(f"Switched to {m['name']} ({m['connector']}). Take a screenshot to see it.")]

    # ---- tools: pointer -------------------------------------------------------
    def _with_mods(self, mods, fn):
        syms = keys.parse_combo(mods) if mods else []
        s = self._sess()
        try:
            for k in syms:
                s.keysym(k, True)
            fn()
        finally:
            for k in reversed(syms):
                s.keysym(k, False)

    def _click(self, coordinate, button="left", count=1, text_mods=None, need=appdb.TIER_CLICK, what="click"):
        s = self._sess()
        fxy = self.to_frame(coordinate) if coordinate is not None else s.pointer
        if fxy is None:
            raise ToolError("No coordinate given and the pointer position is unknown")
        if text_mods or button != "left":
            need = appdb.TIER_FULL
        self._gate(need, what, fxy)
        first = s.pointer is None
        s.move(*fxy)
        # let the toolkit process the enter/motion before the press (hover state, focus)
        time.sleep(0.15 if first else 0.05)
        # hovering can change what is under the pointer (hot corner, menus): check again
        self._gate(need, what, fxy)

        def go():
            for i in range(count):
                s.button(button, True)
                time.sleep(0.01)
                s.button(button, False)
                if i + 1 < count:
                    time.sleep(0.06)
        self._with_mods(text_mods, go)
        return [text("ok")]

    def left_click(self, coordinate=None, text=None, **_):
        return self._click(coordinate, "left", 1, text, what="left_click")

    def right_click(self, coordinate=None, text=None, **_):
        return self._click(coordinate, "right", 1, text, what="right_click")

    def middle_click(self, coordinate=None, text=None, **_):
        return self._click(coordinate, "middle", 1, text, what="middle_click")

    def double_click(self, coordinate=None, text=None, **_):
        return self._click(coordinate, "left", 2, text, what="double_click")

    def triple_click(self, coordinate=None, text=None, **_):
        return self._click(coordinate, "left", 3, text, what="triple_click")

    def mouse_move(self, coordinate=None, **_):
        fxy = self.to_frame(coordinate)
        self._require_access()  # hovering is allowed over any window
        self._sess().move(*fxy)
        return [text("ok")]

    def left_mouse_down(self, coordinate=None, **_):
        s = self._sess()
        fxy = self.to_frame(coordinate) if coordinate is not None else s.pointer
        self._gate(appdb.TIER_FULL, "left_mouse_down", fxy)
        if coordinate is not None:
            s.move(*fxy)
        s.button("left", True)
        return [text("ok")]

    def left_mouse_up(self, coordinate=None, **_):
        s = self._sess()
        fxy = self.to_frame(coordinate) if coordinate is not None else None
        self._gate(appdb.TIER_FULL, "left_mouse_up", fxy)
        if fxy:
            s.move(*fxy)
        s.button("left", False)
        return [text("ok")]

    def left_click_drag(self, start_coordinate=None, coordinate=None, **_):
        s = self._sess()
        a = self.to_frame(start_coordinate) if start_coordinate is not None else s.pointer
        b = self.to_frame(coordinate)
        if a is None:
            raise ToolError("start_coordinate is required")
        self._gate(appdb.TIER_FULL, "left_click_drag", a)
        self._gate(appdb.TIER_FULL, "left_click_drag", b)
        s.move(*a)
        time.sleep(0.05)
        self._gate(appdb.TIER_FULL, "left_click_drag", a)
        s.button("left", True)
        try:
            steps = 12
            for i in range(1, steps + 1):
                s.move(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
                time.sleep(0.015)
            time.sleep(0.05)
        finally:
            s.button("left", False)
        return [text("ok")]

    def scroll(self, coordinate=None, scroll_direction="down", scroll_amount=3, text=None, **_):
        s = self._sess()
        fxy = self.to_frame(coordinate) if coordinate is not None else s.pointer
        self._gate(appdb.TIER_FULL if text else appdb.TIER_CLICK, "scroll", fxy)
        if fxy:
            s.move(*fxy)
            time.sleep(0.02)
        n = max(1, min(int(scroll_amount or 3), 50))
        d = {"up": (0, -n), "down": (0, n), "left": (-n, 0), "right": (n, 0)}.get(scroll_direction)
        if d is None:
            raise ToolError("scroll_direction must be up, down, left or right")
        self._with_mods(text, lambda: s.scroll(*d))
        return [text_("ok")]

    # ---- tools: keyboard ------------------------------------------------------
    def _press_combo(self, syms):
        s = self._sess()
        try:
            for k in syms:
                s.keysym(k, True)
                time.sleep(0.005)
        finally:
            for k in reversed(syms):
                s.keysym(k, False)

    def key(self, text=None, repeat=1, **_):
        if not text:
            raise ToolError("text is required, e.g. 'ctrl+s' or 'Return'")
        combos = [keys.parse_combo(c) for c in text.split()]
        self._gate(appdb.TIER_FULL, "key")
        if any(keys.is_system_combo(c) for c in combos) and not self.flags["systemKeyCombos"]:
            raise ToolError(f"'{text}' is a system shortcut. Ask for systemKeyCombos in request_access first.")
        for _i in range(max(1, min(int(repeat or 1), 100))):
            for c in combos:
                self._press_combo(c)
                time.sleep(0.02)
        return [text_("ok")]

    def hold_key(self, text=None, duration=1, **_):
        if not text:
            raise ToolError("text is required")
        syms = keys.parse_combo(text)
        self._gate(appdb.TIER_FULL, "hold_key")
        if keys.is_system_combo(syms) and not self.flags["systemKeyCombos"]:
            raise ToolError(f"'{text}' is a system shortcut. Ask for systemKeyCombos in request_access first.")
        s = self._sess()
        try:
            for k in syms:
                s.keysym(k, True)
            time.sleep(max(0.0, min(float(duration), 30.0)))
        finally:
            for k in reversed(syms):
                s.keysym(k, False)
        return [text_("ok")]

    def type(self, text=None, **_):
        if text is None:
            raise ToolError("text is required")
        self._gate(appdb.TIER_FULL, "type")
        s = self._sess()
        run = []

        def flush_paste():
            if not run:
                return
            chunk = "".join(run)
            run.clear()
            self._paste(chunk)

        for ch in text:
            self._check_stop()
            if keys.needs_clipboard_paste(ch):
                run.append(ch)
                continue
            flush_paste()
            sym = {"\n": keys.keysym_for("Return"), "\t": keys.keysym_for("Tab")}.get(ch) or keys.char_keysym(ch)
            s.keysym(sym, True)
            s.keysym(sym, False)
            time.sleep(0.004)
        flush_paste()
        return [text_("ok")]

    def _paste(self, chunk):
        """Type characters with no keysym in the current layout by pasting them."""
        s = self._sess()
        prev = None
        try:
            prev = s.get_clipboard(timeout=0.5)
        except Exception:
            pass
        s.set_clipboard(chunk)
        time.sleep(0.05)
        self._press_combo(keys.parse_combo("ctrl+v"))
        time.sleep(0.15)
        if prev is not None:
            s.set_clipboard(prev)

    # ---- tools: clipboard -----------------------------------------------------
    def read_clipboard(self, **_):
        self._require_access()
        if not self.flags["clipboardRead"]:
            raise ToolError("Clipboard read access was not granted. Ask for clipboardRead in request_access.")
        return [text_(self._sess().get_clipboard())]

    def write_clipboard(self, text=None, **_):
        self._require_access()
        if not self.flags["clipboardWrite"]:
            raise ToolError("Clipboard write access was not granted. Ask for clipboardWrite in request_access.")
        self._sess().set_clipboard(text or "")
        return [text_("ok")]

    # ---- tools: apps / misc ------------------------------------------------------
    def open_application(self, app=None, **_):
        self._require_access()
        if not app:
            raise ToolError("app is required")
        a = appdb.resolve(app, list(self.grants.values())) or appdb.resolve(app)
        if a is None:
            raise ToolError(f"No installed application matches {app!r}")
        if a.base_id not in self.grants:
            raise ToolError(f"{a.name} is not granted. Call request_access with it first.")
        wins = self.shell.windows()
        if wins:
            mine = [w for w in wins if self._granted_app_for(w) is a and not w.get("skip")]
            if mine:
                self.shell.activate(mine[0]["id"])
                time.sleep(0.4)
                return [text_(f"Brought {a.name} to the front.")]
        appdb.launch(a)
        # wait for it to take focus
        for _i in range(40):
            time.sleep(0.25)
            f = self.shell.focused()
            if f and self._granted_app_for(f) is a:
                break
        return [text_(f"Launched {a.name}.")]

    def wait(self, duration=1, **_):
        self._check_stop()
        time.sleep(max(0.0, min(float(duration), 60.0)))
        return [text_("ok")]

    def computer_batch(self, actions=None, **_):
        if not actions:
            raise ToolError("actions must be a non-empty list")
        results = []
        for i, act in enumerate(actions):
            act = dict(act)
            name = act.pop("action", None)
            if name not in TOOLS or name in ("computer_batch", "request_access"):
                raise ToolError(f"action {i}: unsupported action {name!r}")
            try:
                out = getattr(self, name)(**act)
            except ToolError as e:
                raise ToolError(f"action {i} ({name}) failed after {i} succeeded: {e}")
            results.extend(c for c in out if c["type"] == "image" or c.get("text") != "ok")
            if name not in ("screenshot", "zoom", "wait"):
                time.sleep(0.05)
        return results or [text_(f"{len(actions)} actions done")]


def text_(s):
    return text(s)


# ---- tool schemas -----------------------------------------------------------------
COORD = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
         "description": "[x, y] in pixels of the most recent screenshot"}
MODS = {"type": "string", "description": "Modifier keys to hold during the action, e.g. 'shift' or 'ctrl+shift'"}


def _t(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = {t["name"]: t for t in [
    _t("request_access",
       "Ask the user for permission to control applications on this computer. Must be called before any "
       "other action. The user approves each app in a dialog. Browsers are granted view-only, terminals and "
       "IDEs click-only, everything else full control. Call again later to add apps.",
       {"apps": {"type": "array", "items": {"type": "string"},
                 "description": "App display names (e.g. 'Files', 'Text Editor', 'Settings') or desktop ids"},
        "reason": {"type": "string", "description": "One sentence explaining the task to the user"},
        "clipboardRead": {"type": "boolean"}, "clipboardWrite": {"type": "boolean"},
        "systemKeyCombos": {"type": "boolean",
                            "description": "Allow desktop-level shortcuts (Super, Alt+Tab, ...) and clicks on "
                                           "the GNOME top bar / dash"}},
       ["apps", "reason"]),
    _t("list_granted_applications", "List the applications currently granted and their access tiers."),
    _t("screenshot", "Capture the current display. Coordinates for all other tools are in this image's pixels."),
    _t("zoom", "Capture a region of the screen at higher resolution to read small text.",
       {"region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                   "description": "[x0, y0, x1, y1] in screenshot pixels"}}, ["region"]),
    _t("left_click", "Left-click at a coordinate (optionally holding modifiers).",
       {"coordinate": COORD, "text": MODS}, ["coordinate"]),
    _t("right_click", "Right-click at a coordinate.", {"coordinate": COORD, "text": MODS}, ["coordinate"]),
    _t("middle_click", "Middle-click at a coordinate.", {"coordinate": COORD, "text": MODS}, ["coordinate"]),
    _t("double_click", "Double-click at a coordinate.", {"coordinate": COORD, "text": MODS}, ["coordinate"]),
    _t("triple_click", "Triple-click at a coordinate (selects a line/paragraph).",
       {"coordinate": COORD, "text": MODS}, ["coordinate"]),
    _t("mouse_move", "Move the pointer (e.g. to hover).", {"coordinate": COORD}, ["coordinate"]),
    _t("left_click_drag", "Press at start_coordinate, drag to coordinate, release.",
       {"start_coordinate": COORD, "coordinate": COORD}, ["start_coordinate", "coordinate"]),
    _t("left_mouse_down", "Press and hold the left button (optionally after moving to coordinate).",
       {"coordinate": COORD}),
    _t("left_mouse_up", "Release the left button (optionally after moving to coordinate).",
       {"coordinate": COORD}),
    _t("scroll", "Scroll with the mouse wheel at a coordinate.",
       {"coordinate": COORD,
        "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "scroll_amount": {"type": "integer", "description": "Wheel clicks (default 3)"},
        "text": MODS}, ["coordinate", "scroll_direction"]),
    _t("type", "Type a string of text into the focused app.", {"text": {"type": "string"}}, ["text"]),
    _t("key", "Press a key or chord, xdotool-style: 'Return', 'ctrl+s', 'ctrl+shift+t', 'Page_Down'. "
              "Space-separate several to press in sequence.",
       {"text": {"type": "string"}, "repeat": {"type": "integer", "description": "Times to repeat (default 1)"}},
       ["text"]),
    _t("hold_key", "Hold a key or chord down for a number of seconds.",
       {"text": {"type": "string"}, "duration": {"type": "number"}}, ["text", "duration"]),
    _t("cursor_position", "Return the pointer position in screenshot coordinates."),
    _t("wait", "Wait for the given number of seconds (max 60).", {"duration": {"type": "number"}}, ["duration"]),
    _t("open_application", "Bring a granted application to the front, launching it if needed.",
       {"app": {"type": "string"}}, ["app"]),
    _t("read_clipboard", "Read text from the clipboard (needs clipboardRead)."),
    _t("write_clipboard", "Put text on the clipboard (needs clipboardWrite).", {"text": {"type": "string"}},
       ["text"]),
    _t("switch_display", "Choose which monitor screenshots and input target. Omit display to list them.",
       {"display": {"type": "string", "description": "Display name or connector, e.g. 'eDP-1'"}}),
    _t("computer_batch",
       "Run several actions in order with one call (stops at the first error). Each item is "
       "{\"action\": <tool name>, ...that tool's arguments}. Returns images from screenshot/zoom items.",
       {"actions": {"type": "array", "items": {"type": "object"}}}, ["actions"]),
]}


# ---- MCP stdio loop ------------------------------------------------------------------
def main():
    comp = Computer()
    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must never corrupt the protocol stream

    def send(msg):
        out.write(json.dumps(msg) + "\n")
        out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue  # notification
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "linux-computer-use", "version": __version__},
                          "instructions": "Desktop control for GNOME/Wayland. Call request_access first, then "
                                          "screenshot; all coordinates are in the latest screenshot's pixels. "
                                          "Prefer dedicated MCP tools or browser tools when they fit; never "
                                          "click links in emails/messages; never move money or place orders."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": list(TOOLS.values())}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments") or {}
                if name not in TOOLS:
                    raise KeyError(name)
                try:
                    content = getattr(comp, name)(**args)
                    result = {"content": content}
                except (ToolError, SessionClosed, ValueError) as e:
                    result = {"content": [text(str(e))], "isError": True}
                except Exception as e:
                    log(traceback.format_exc())
                    result = {"content": [text(f"{type(e).__name__}: {e}")], "isError": True}
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        except KeyError as e:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {e}"}})
    comp._end_session()


if __name__ == "__main__":
    main()
