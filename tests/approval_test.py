"""Drive the real approval dialog (no auto-approve). Run under tests/run_headless.sh:

    tests/run_headless.sh python3 tests/approval_test.py
"""
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lcu import keys  # noqa: E402
from lcu.mutter import MutterSession  # noqa: E402

env = {k: v for k, v in os.environ.items() if k != "LCU_TEST_AUTO_APPROVE"}
srv = subprocess.Popen([sys.executable, "-m", "lcu"], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, text=True)
failures = []


def rpc(i, method, params):
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
    srv.stdin.flush()
    return json.loads(srv.stdout.readline())


def request_with_keys(i, apps, presses, **flags):
    out = {}
    t = threading.Thread(target=lambda: out.update(rpc(i, "tools/call", {"name": "request_access", "arguments": {
        "apps": apps, "reason": "approval test", **flags}})))
    t.start()
    time.sleep(3)  # dialog appears and takes focus
    s = MutterSession()
    s.start()
    for p in presses:
        for k in keys.parse_combo(p):
            s.keysym(k, True)
            time.sleep(0.03)
            s.keysym(k, False)
        time.sleep(0.3)
    if os.environ.get("LCU_SHOT_DIR"):
        s.capture().save(os.path.join(os.environ["LCU_SHOT_DIR"], f"approval-{i}.png"))
    s.stop()
    t.join(15)
    return json.loads(out["result"]["content"][0]["text"])


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failures.append(label)


rpc(0, "initialize", {"protocolVersion": "2025-06-18"})
r = request_with_keys(1, ["Calculator"], ["Return"])  # focus starts on Deny
check(r["granted"] == [] and r["denied"] == ["Calculator"], f"Return on default focus denies: {r}")
r = request_with_keys(2, ["Calculator"], ["Tab", "Return"], clipboardRead=True)  # Deny -> Allow
check([a["id"] for a in r["granted"]] == ["org.gnome.Calculator"] and r["clipboardRead"],
      f"Tab+Return allows app and clipboard: {r}")
srv.stdin.close()
srv.wait(5)
print("ALL PASSED" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
