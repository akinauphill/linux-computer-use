"""Installed applications (.desktop entries): lookup, tier classification, launching."""
import difflib
import re

import gi

gi.require_version("GioUnix", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

try:
    from gi.repository import GioUnix
    DesktopAppInfo = GioUnix.DesktopAppInfo
except Exception:  # older PyGObject
    DesktopAppInfo = Gio.DesktopAppInfo

TIER_FULL, TIER_CLICK, TIER_READ = "full", "click", "read"

# Matched against desktop id / executable / wm_class (lower-case substrings).
BROWSER_HINTS = ("firefox", "chrome", "chromium", "brave", "vivaldi", "opera", "epiphany", "org.gnome.epiphany",
                 "microsoft-edge", "zen", "librewolf", "floorp", "qutebrowser", "falkon", "midori", "tor-browser",
                 "waterfox", "thorium", "helium", "orion")
TERMINAL_HINTS = ("terminal", "kgx", "ptyxis", "alacritty", "kitty", "wezterm", "foot", "konsole",
                  "tilix", "terminator", "ghostty", "xterm", "urxvt", "st-256color", "blackbox", "warp", "tabby",
                  "rio", "contour")
IDE_HINTS = ("code", "vscodium", "codium", "cursor", "windsurf", "jetbrains", "idea", "pycharm", "webstorm",
             "clion", "goland", "rider", "rustrover", "android-studio", "zed", "sublime", "builder", "kate",
             "neovide", "emacs", "gvim", "lapce", "fleet", "geany", "kdevelop", "qtcreator", "eclipse", "netbeans")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


class App:
    def __init__(self, info):
        self.info = info
        self.id = info.get_id() or ""  # e.g. org.gnome.TextEditor.desktop
        self.name = info.get_display_name() or info.get_name() or self.id
        self.executable = info.get_executable() or ""
        self.categories = [c for c in (info.get_categories() or "").split(";") if c]
        self.wm_class = info.get_startup_wm_class() or ""
        self.tier = classify(self)

    @property
    def base_id(self):
        return self.id[:-8] if self.id.endswith(".desktop") else self.id

    def keys(self):
        """Strings a window's wm_class / app-id might match."""
        exe = self.executable.rsplit("/", 1)[-1]
        return {k for k in (_norm(self.base_id), _norm(self.base_id.rsplit(".", 1)[-1]), _norm(self.name),
                            _norm(exe), _norm(self.wm_class)) if k}

    def to_json(self):
        return {"name": self.name, "id": self.base_id, "tier": self.tier}


def classify(app):
    cats = set(app.categories)
    hay = " ".join([app.id.lower(), app.executable.lower(), app.wm_class.lower()])
    if "WebBrowser" in cats or any(h in hay for h in BROWSER_HINTS if len(h) > 3) and "Network" in cats:
        return TIER_READ
    if "TerminalEmulator" in cats or any(h in hay for h in TERMINAL_HINTS):
        return TIER_CLICK
    if "IDE" in cats or ("Development" in cats and "TextEditor" in cats) or \
            any(re.search(rf"(^|[^a-z]){re.escape(h)}([^a-z]|$)", hay) for h in IDE_HINTS):
        return TIER_CLICK
    return TIER_FULL


def tier_for_window(wm_class, app_id=""):
    """Tier for a running window whose .desktop entry we could not match."""
    hay = f"{wm_class} {app_id}".lower()
    if any(h in hay for h in BROWSER_HINTS):
        return TIER_READ
    if any(h in hay for h in TERMINAL_HINTS) or any(re.search(rf"(^|[^a-z]){re.escape(h)}([^a-z]|$)", hay)
                                                    for h in IDE_HINTS):
        return TIER_CLICK
    return TIER_FULL


def all_apps():
    apps = []
    for info in Gio.AppInfo.get_all():
        try:
            if info.should_show() or info.get_id():
                apps.append(App(info))
        except Exception:
            continue
    return apps


def resolve(query, apps=None):
    """Best .desktop match for a human name, id, or executable. None if nothing plausible."""
    apps = apps if apps is not None else all_apps()
    q = query.strip()
    qn = _norm(q.removesuffix(".desktop"))
    if not qn:
        return None
    visible = [a for a in apps if a.info.should_show()] or apps
    for pool in (visible, apps):
        for a in pool:  # exact id / name / exe
            if qn in (_norm(a.base_id), _norm(a.name), _norm(a.executable.rsplit("/", 1)[-1])):
                return a
        for a in pool:
            if qn == _norm(a.base_id.rsplit(".", 1)[-1]) or qn == _norm(a.wm_class):
                return a
    # keyword/substring then fuzzy over names
    subs = [a for a in visible if qn in _norm(a.name) or qn in _norm(a.base_id)]
    if subs:
        return min(subs, key=lambda a: len(a.name))
    for a in visible:
        kw = (a.info.get_keywords() or []) if hasattr(a.info, "get_keywords") else []
        if any(qn == _norm(k) for k in kw):
            return a
    names = {_norm(a.name): a for a in visible}
    close = difflib.get_close_matches(qn, list(names), n=1, cutoff=0.75)
    return names[close[0]] if close else None


def match_window(wm_class, app_id, candidates):
    """Which of `candidates` (Apps) owns a window with this wm_class / GNOME app id."""
    keys = {_norm(wm_class), _norm(app_id.removesuffix(".desktop") if app_id else "")}
    keys |= {_norm((app_id or "").removesuffix(".desktop").rsplit(".", 1)[-1])}
    keys.discard("")
    for a in candidates:
        if app_id and a.id == app_id:
            return a
    for a in candidates:
        if keys & a.keys():
            return a
    return None


def launch(app, uris=None):
    ctx = Gio.AppLaunchContext()
    if uris:
        app.info.launch_uris(uris, ctx)
    else:
        app.info.launch([], ctx)


def get_by_id(app_id):
    try:
        info = DesktopAppInfo.new(app_id if app_id.endswith(".desktop") else app_id + ".desktop")
    except (TypeError, GLib.Error):
        info = None
    return App(info) if info else None
