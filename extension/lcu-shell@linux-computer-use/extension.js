import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.lcu.Shell">
    <method name="ListWindows"><arg type="s" direction="out" name="json"/></method>
    <method name="FocusedWindow"><arg type="s" direction="out" name="json"/></method>
    <method name="WindowAt">
      <arg type="d" direction="in" name="x"/><arg type="d" direction="in" name="y"/>
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="ActivateWindow">
      <arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/>
    </method>
    <method name="GetPointer">
      <arg type="d" direction="out" name="x"/><arg type="d" direction="out" name="y"/>
    </method>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>`;

function describe(w, focus) {
    if (!w)
        return null;
    const r = w.get_frame_rect();
    const app = Shell.WindowTracker.get_default().get_window_app(w);
    return {
        id: w.get_id(),
        title: w.get_title() ?? '',
        wm_class: w.get_wm_class() ?? '',
        sandboxed_app_id: w.get_sandboxed_app_id?.() ?? '',
        gtk_app_id: w.get_gtk_application_id?.() ?? '',
        app_id: app?.get_id() ?? '',
        app_name: app?.get_name() ?? '',
        pid: w.get_pid(),
        focused: w === focus,
        minimized: w.minimized,
        workspace: w.get_workspace()?.index() ?? -1,
        monitor: w.get_monitor(),
        x: r.x, y: r.y, width: r.width, height: r.height,
    };
}

function normalWindows() {
    // Stacking order, top-most first, current workspace + sticky windows only for hit tests
    const all = global.display.list_all_windows?.() ??
        global.get_window_actors().map(a => a.meta_window);
    return global.display.sort_windows_by_stacking(all)
        .filter(w => !w.skip_taskbar || w.get_window_type() === Meta.WindowType.DIALOG ||
            w.get_window_type() === Meta.WindowType.MODAL_DIALOG)
        .reverse();
}

class Service {
    get Version() {
        return 1;
    }

    ListWindows() {
        const focus = global.display.focus_window;
        return JSON.stringify(normalWindows().map(w => describe(w, focus)));
    }

    FocusedWindow() {
        const focus = global.display.focus_window;
        return JSON.stringify(describe(focus, focus));
    }

    WindowAt(x, y) {
        const focus = global.display.focus_window;
        const ws = global.workspace_manager.get_active_workspace();
        // includes popups/menus: they belong to the app that opened them
        const actors = global.get_window_actors().slice().reverse();
        for (const a of actors) {
            const w = a.meta_window;
            if (!w || w.minimized || !a.visible)
                continue;
            if (!w.is_on_all_workspaces() && w.get_workspace() !== ws)
                continue;
            const r = w.get_frame_rect();
            if (x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height) {
                let owner = w;
                while (owner.get_transient_for?.())
                    owner = owner.get_transient_for();
                const d = describe(w, focus);
                if (owner !== w)
                    d.owner = describe(owner, focus);
                return JSON.stringify(d);
            }
        }
        return 'null';
    }

    ActivateWindow(id) {
        const w = global.display.list_all_windows?.().find(win => win.get_id() === Number(id)) ??
            global.get_window_actors().map(a => a.meta_window).find(win => win.get_id() === Number(id));
        if (!w)
            return false;
        w.activate(global.get_current_time());
        return true;
    }

    GetPointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }
}

export default class LcuShell extends Extension {
    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, new Service());
        this._impl.export(Gio.DBus.session, '/org/lcu/Shell');
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session, 'org.lcu.Shell',
            Gio.BusNameOwnerFlags.REPLACE, null, null);
    }

    disable() {
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._impl?.unexport();
        this._impl = null;
        this._owner = 0;
    }
}
