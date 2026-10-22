"""End-to-end test: run the MCP server over stdio against a throwaway GTK window and
assert on what the window actually received. Drives the real desktop (briefly)."""
import base64
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="lcu-e2e-")
LOG = os.path.join(TMP, "target.log")
STOP_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "lcu", "stop")

os.makedirs(os.path.join(TMP, "share/applications"))
with open(os.path.join(TMP, "share/applications/dev.lcu.TestTarget.desktop"), "w") as f:
    f.write(f"""[Desktop Entry]
Type=Application
Name=LCU Test Target
Exec={sys.executable} {HERE}/target_app.py
StartupWMClass=dev.lcu.TestTarget
NoDisplay=false
""")

env = dict(os.environ, LCU_TEST_AUTO_APPROVE="1", LCU_TARGET_LOG=LOG,
           XDG_DATA_DIRS=os.path.join(TMP, "share") + ":" + os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share"))
srv = subprocess.Popen([sys.executable, "-m", "lcu"], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, text=True)
_id = 0
failures = []


def rpc(method, params=None):
    global _id
    _id += 1
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}) + "\n")
    srv.stdin.flush()
    while True:
        msg = json.loads(srv.stdout.readline())
        if msg.get("id") == _id:
            return msg


def call(name, **args):
    r = rpc("tools/call", {"name": name, "arguments": args})["result"]
    return r


def ok(name, **args):
    r = call(name, **args)
    if r.get("isError"):
        raise AssertionError(f"{name}{args} failed: {r['content'][0]['text']}")
    return r


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failures.append(label)


def events():
    try:
        with open(LOG) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def wait_for(pred, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        evs = events()
        if pred(evs):
            return evs
        time.sleep(0.1)
    return events()


def shot():
    r = ok("screenshot")
    img = Image.open(io.BytesIO(base64.b64decode(r["content"][0]["data"]))).convert("RGB")
    img.save(os.path.join(TMP, f"shot-{time.monotonic():.0f}.png"))
    return img


def find_color(img, rgb, tol=40):
    px = img.load()
    xs, ys = [], []
    for y in range(0, img.height, 2):
        for x in range(0, img.width, 2):
            p = px[x, y]
            if all(abs(p[i] - rgb[i]) <= tol for i in range(3)):
                xs.append(x)
                ys.append(y)
    if len(xs) < 50:
        return None
    xs.sort()
    ys.sort()
    return xs[len(xs) // 2], ys[len(ys) // 2], (xs[0], ys[0], xs[-1], ys[-1])


target_pid = None
try:
    init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "e2e", "version": "0"}})
    check(init["result"]["serverInfo"]["name"] == "linux-computer-use", "initialize")
    tools = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    check(len(tools) >= 24, f"tools/list ({len(tools)} tools)")

    r = call("screenshot")
    check(r.get("isError") and "request_access" in r["content"][0]["text"], "screenshot blocked before access")

    r = ok("request_access", apps=["LCU Test Target", "definitely-not-an-app-xyz"], reason="e2e test")
    g = json.loads(r["content"][0]["text"])
    check([a["id"] for a in g["granted"]] == ["dev.lcu.TestTarget"] and g["granted"][0]["tier"] == "full",
          "request_access grants test app at full tier")
    check(g["not_found"] == ["definitely-not-an-app-xyz"], "request_access reports unknown app")

    t0 = time.monotonic()
    ok("open_application", app="LCU Test Target")
    evs = wait_for(lambda e: any(x["ev"] == "ready" for x in e), 10)
    ready = [x for x in evs if x["ev"] == "ready"]
    check(bool(ready), "open_application launched the target")
    target_pid = ready[0]["pid"] if ready else None
    time.sleep(1.0)

    t0 = time.monotonic()
    img = shot()
    print(f"     screenshot {img.size} in {time.monotonic() - t0:.2f}s")
    red = find_color(img, (255, 0, 0))
    check(red is not None, "found the red button in the screenshot")
    cx, cy, rbox = red
    ok("left_click", coordinate=[cx, cy])
    evs = wait_for(lambda e: any(x["ev"] == "clicked" for x in e))
    check(any(x["ev"] == "clicked" for x in evs), f"left_click hit the button at {cx},{cy}")

    r = ok("cursor_position")
    pos = json.loads(r["content"][0]["text"])
    check(abs(pos["x"] - cx) <= 2 and abs(pos["y"] - cy) <= 2, f"cursor_position {pos} ~ ({cx},{cy})")

    ok("right_click", coordinate=[cx, cy])
    evs = wait_for(lambda e: any(x["ev"] == "right_click" for x in e))
    check(any(x["ev"] == "right_click" for x in evs), "right_click delivered")
    ok("key", text="Escape")

    # Tab from the button into the entry, then type (ASCII via keysyms, then non-ASCII via paste)
    ok("left_click", coordinate=[cx, cy])
    ok("key", text="Tab")
    ok("type", text="Hello, World! 123 ~`|{}")
    evs = wait_for(lambda e: any(x.get("value") == "Hello, World! 123 ~`|{}" for x in e))
    last = [x for x in evs if x["ev"] == "text"]
    check(last and last[-1]["value"] == "Hello, World! 123 ~`|{}",
          f"type ASCII incl. shifted symbols -> {last[-1]['value'] if last else None!r}")
    ok("type", text=" café ✓")
    evs = wait_for(lambda e: any(x.get("value", "").endswith("café ✓") for x in e))
    last = [x for x in evs if x["ev"] == "text"]
    check(last and last[-1]["value"].endswith("café ✓"), f"type non-ASCII -> {last[-1]['value'] if last else None!r}")

    ok("key", text="ctrl+a BackSpace")
    evs = wait_for(lambda e: [x for x in e if x["ev"] == "text"][-1]["value"] == "")
    check([x for x in evs if x["ev"] == "text"][-1]["value"] == "", "key sequence ctrl+a BackSpace clears entry")

    r = call("key", text="super")
    check(r.get("isError") and "systemKeyCombos" in r["content"][0]["text"], "system key blocked without grant")

    green = find_color(img, (0, 255, 0))
    check(green is not None, "found the green scroll area")
    gx, gy, _ = green
    ok("scroll", coordinate=[gx, gy], scroll_direction="down", scroll_amount=5)
    evs = wait_for(lambda e: any(x["ev"] == "scroll" and x["value"] > 0 for x in e))
    check(any(x["ev"] == "scroll" and x["value"] > 0 for x in evs), "scroll moved the list")

    r = ok("zoom", region=list(rbox))
    z = Image.open(io.BytesIO(base64.b64decode(r["content"][0]["data"])))
    check(z.width > rbox[2] - rbox[0], f"zoom returns a larger image {z.size}")

    r = call("write_clipboard", text="from-clipboard")
    check(r.get("isError"), "write_clipboard blocked without grant")
    ok("request_access", apps=[], reason="clipboard", clipboardRead=True, clipboardWrite=True)
    ok("write_clipboard", text="from-clipboard")
    r = ok("read_clipboard")
    check(r["content"][0]["text"] == "from-clipboard", "read_clipboard round trip")
    ok("computer_batch", actions=[{"action": "left_click", "coordinate": [cx, cy]}, {"action": "key", "text": "Tab"},
                                  {"action": "key", "text": "ctrl+v"}])
    evs = wait_for(lambda e: [x for x in e if x["ev"] == "text"][-1]["value"] == "from-clipboard")
    check([x for x in evs if x["ev"] == "text"][-1]["value"] == "from-clipboard", "batch + paste from our clipboard")

    has_ext = subprocess.run(["gdbus", "call", "--session", "--dest", "org.lcu.Shell", "--object-path",
                              "/org/lcu/Shell", "--method", "org.lcu.Shell.FocusedWindow"],
                             capture_output=True).returncode == 0
    if has_ext:
        # outside the target window (shell background / other app): must be refused
        r = call("left_click", coordinate=[3, img.height - 3])
        print("     outside click ->", r["content"][0]["text"][:300])
        check(r.get("isError") and ("GNOME Shell UI" in r["content"][0]["text"] or
                                    "not in the granted" in r["content"][0]["text"]),
              "click outside granted window blocked (extension)")
        r = ok("open_application", app="LCU Test Target")
        check("front" in r["content"][0]["text"], "open_application focuses existing window (extension)")
    else:
        print("     (lcu-shell extension not running: skipping window-at-point checks)")

    os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
    open(STOP_FILE, "w").close()
    r = call("screenshot")
    check(r.get("isError") and "stopped" in r["content"][0]["text"], "kill switch revokes access")
    r = call("screenshot")
    check(r.get("isError") and "request_access" in r["content"][0]["text"], "access stays revoked")
except Exception:
    traceback.print_exc()
    failures.append("exception")
finally:
    if target_pid:
        try:
            os.kill(target_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    srv.stdin.close()
    srv.wait(5)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + '; '.join(failures)}  (artifacts: {TMP})")
    sys.exit(1 if failures else 0)
