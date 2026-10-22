"""Access-approval dialog (GTK4). Run as a subprocess:

    python3 -m lcu.approve < request.json  > response.json

request:  {"reason": str, "apps": [{"name","id","tier"}], "unresolved": [str],
           "clipboardRead": bool, "clipboardWrite": bool, "systemKeyCombos": bool}
response: {"approved": bool, "apps": [ids], "clipboardRead": bool, "clipboardWrite": bool,
           "systemKeyCombos": bool}
"""
import json
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

TIER_TEXT = {
    "full": "Full control",
    "click": "Click and scroll only (terminal / IDE)",
    "read": "View only (browser)",
}


def main():
    req = json.load(sys.stdin)
    result = {"approved": False, "apps": [], "clipboardRead": False, "clipboardWrite": False,
              "systemKeyCombos": False}
    app = Gtk.Application(application_id=None)

    def activate(application):
        win = Gtk.ApplicationWindow(application=application, title="Allow Claude to use your computer?")
        win.set_default_size(520, -1)
        win.set_resizable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=20, margin_bottom=20,
                      margin_start=24, margin_end=24)
        win.set_child(box)

        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='large' weight='bold'>Claude wants to control these apps</span>")
        box.append(title)
        if req.get("reason"):
            r = Gtk.Label(label=req["reason"], xalign=0, wrap=True)
            r.add_css_class("dim-label")
            box.append(r)

        checks = []
        frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        frame.add_css_class("boxed-list")
        for a in req.get("apps", []):
            row = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
            cb = Gtk.CheckButton(active=True)
            row.append(cb)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            col.append(Gtk.Label(label=a["name"], xalign=0))
            t = Gtk.Label(label=TIER_TEXT.get(a["tier"], a["tier"]), xalign=0)
            t.add_css_class("dim-label")
            t.add_css_class("caption")
            col.append(t)
            row.append(col)
            frame.append(row)
            checks.append((cb, a["id"]))
        if checks:
            box.append(frame)
        if req.get("unresolved"):
            u = Gtk.Label(label="Not found (ignored): " + ", ".join(req["unresolved"]), xalign=0, wrap=True)
            u.add_css_class("dim-label")
            box.append(u)

        extras = []
        for key, label in (("clipboardRead", "Let Claude read the clipboard"),
                           ("clipboardWrite", "Let Claude write to the clipboard"),
                           ("systemKeyCombos", "Allow system shortcuts (Super, Alt+Tab, ...)")):
            if req.get(key):
                cb = Gtk.CheckButton(label=label, active=True)
                box.append(cb)
                extras.append((key, cb))

        note = Gtk.Label(xalign=0, wrap=True)
        note.set_markup("<small>Stop at any time with <b>Super+Alt+Escape</b> or the screen-sharing "
                        "indicator in the top bar. Claude sees your whole screen while this is active.</small>")
        box.append(note)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        deny = Gtk.Button(label="Deny")
        allow = Gtk.Button(label="Allow")
        allow.add_css_class("suggested-action")
        buttons.append(deny)
        buttons.append(allow)
        box.append(buttons)

        def on_allow(_b):
            result["approved"] = True
            result["apps"] = [i for cb, i in checks if cb.get_active()]
            for k, cb in extras:
                result[k] = cb.get_active()
            win.close()

        allow.connect("clicked", on_allow)
        deny.connect("clicked", lambda _b: win.close())
        win.connect("close-request", lambda _w: (application.quit(), False)[1])
        GLib.timeout_add_seconds(int(req.get("timeout", 300)), lambda: (win.close(), False)[1])
        # safe default: Return / Space before anything else answers "Deny"
        win.set_focus(deny)
        win.present()

    app.connect("activate", activate)
    app.run([])
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
