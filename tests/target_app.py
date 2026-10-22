"""Throwaway GTK4 window used by the end-to-end test. Logs what happens to it as JSON lines
in $LCU_TARGET_LOG so the test can assert on real effects of injected input."""
import json
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

LOG = os.environ.get("LCU_TARGET_LOG", "/tmp/lcu-target.log")


def emit(**ev):
    with open(LOG, "a") as f:
        f.write(json.dumps(ev) + "\n")


CSS = b"""
.target-red { background: #ff0000; color: white; }
.target-green, .target-green label { background: #00ff00; color: black; }
"""


def activate(app):
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, 800)
    win = Gtk.ApplicationWindow(application=app, title="LCU Test Target")
    win.set_default_size(700, 500)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=30, margin_start=30,
                  margin_end=30, margin_bottom=30)
    win.set_child(box)
    btn = Gtk.Button(label="BIG RED BUTTON")
    btn.set_size_request(300, 120)
    btn.set_halign(Gtk.Align.CENTER)
    btn.add_css_class("target-red")
    btn.connect("clicked", lambda _b: emit(ev="clicked"))
    box.append(btn)
    entry = Gtk.Entry(placeholder_text="type here")
    entry.connect("changed", lambda e: emit(ev="text", value=e.get_text()))
    box.append(entry)
    rclick = Gtk.GestureClick(button=3)
    rclick.connect("pressed", lambda *_a: emit(ev="right_click"))
    btn.add_controller(rclick)
    sc = Gtk.ScrolledWindow(vexpand=True)
    lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    sc.add_css_class("target-green")
    for i in range(200):
        lst.append(Gtk.Label(label=f"row {i}"))
    sc.set_child(lst)
    sc.get_vadjustment().connect("value-changed", lambda a: emit(ev="scroll", value=a.get_value()))
    box.append(sc)
    win.connect("close-request", lambda _w: (emit(ev="closed"), False)[1])
    win.present()
    emit(ev="ready", pid=os.getpid())


GLib.set_prgname("dev.lcu.TestTarget")
GLib.set_application_name("LCU Test Target")
app = Gtk.Application(application_id="dev.lcu.TestTarget")
app.connect("activate", activate)
sys.exit(app.run([]))
