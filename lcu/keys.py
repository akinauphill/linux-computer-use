"""Key-name parsing: xdotool / Claude computer-use style names -> X keysyms."""
import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

ALIASES = {
    "ctrl": "Control_L", "control": "Control_L", "lctrl": "Control_L", "rctrl": "Control_R",
    "shift": "Shift_L", "lshift": "Shift_L", "rshift": "Shift_R",
    "alt": "Alt_L", "option": "Alt_L", "opt": "Alt_L", "lalt": "Alt_L", "ralt": "Alt_R", "altgr": "ISO_Level3_Shift",
    "super": "Super_L", "win": "Super_L", "windows": "Super_L", "meta": "Super_L", "cmd": "Super_L",
    "command": "Super_L", "logo": "Super_L",
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "space", "backspace": "BackSpace", "bksp": "BackSpace", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "ins": "Insert", "home": "Home", "end": "End",
    "pageup": "Page_Up", "page_up": "Page_Up", "pgup": "Page_Up",
    "pagedown": "Page_Down", "page_down": "Page_Down", "pgdn": "Page_Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "capslock": "Caps_Lock", "menu": "Menu", "print": "Print", "printscreen": "Print",
    "minus": "minus", "plus": "plus", "equal": "equal", "comma": "comma", "period": "period",
    "slash": "slash", "backslash": "backslash", "semicolon": "semicolon", "apostrophe": "apostrophe",
    "quote": "apostrophe", "grave": "grave", "backtick": "grave", "bracketleft": "bracketleft",
    "bracketright": "bracketright",
    "volumeup": "XF86AudioRaiseVolume", "volumedown": "XF86AudioLowerVolume", "mute": "XF86AudioMute",
    "playpause": "XF86AudioPlay",
}

MODIFIER_NAMES = {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Super_L", "Super_R",
                  "ISO_Level3_Shift", "Meta_L", "Meta_R"}


def keysym_for(name):
    """Resolve one key name ('ctrl', 'Return', 'a', 'F5', '/') to a keysym int."""
    if not name:
        raise ValueError("empty key name")
    n = ALIASES.get(name.lower(), name)
    if len(n) == 1:
        return char_keysym(n)
    sym = Gdk.keyval_from_name(n)
    if not sym or sym == 0xFFFFFF:
        # case-insensitive retry: 'f5' -> 'F5', 'return' handled above, 'page_up' -> 'Page_Up'
        for cand in (n.upper(), n.capitalize(), "_".join(p.capitalize() for p in n.split("_"))):
            sym = Gdk.keyval_from_name(cand)
            if sym and sym != 0xFFFFFF:
                return sym
        raise ValueError(f"unknown key name: {name!r}")
    return sym


def char_keysym(ch):
    sym = Gdk.unicode_to_keyval(ord(ch))
    return sym


def parse_combo(combo):
    """'ctrl+shift+t' -> [keysyms] (modifiers first). A lone '+' is the plus key."""
    combo = combo.strip()
    if combo == "+":
        return [keysym_for("plus")]
    parts = [p for p in combo.replace(" ", "").split("+")]
    if combo.endswith("++"):
        parts = parts[:-2] + ["plus"]
    return [keysym_for(p) for p in parts if p]


def is_modifier(sym):
    return Gdk.keyval_name(sym) in MODIFIER_NAMES


def is_system_combo(syms):
    """Combos that act on the desktop rather than the focused app (need systemKeyCombos)."""
    names = {Gdk.keyval_name(s) for s in syms}
    if names & {"Super_L", "Super_R"}:
        return True
    alt = names & {"Alt_L", "Alt_R"}
    ctrl = names & {"Control_L", "Control_R"}
    if alt and names & {"Tab", "F4", "F2", "F1", "Escape", "grave"}:
        return True
    if ctrl and alt and names & {"Delete", "BackSpace", "t", "T"}:
        return True
    if ctrl and alt and any(n and n.startswith(("Left", "Right", "Up", "Down")) for n in names):
        return True
    if names & {"Print"}:
        return True
    return False


def needs_clipboard_paste(ch):
    """Characters we cannot reliably inject as keysyms."""
    if ch in "\n\t":
        return False
    o = ord(ch)
    return o < 0x20 or o > 0x7E
