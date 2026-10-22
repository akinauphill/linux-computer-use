"""Without the shell extension and without AT-SPI, the frontmost app can't be identified:
input must be refused (fail closed). Run:
    LCU_HEADLESS_NO_EXT=1 tests/run_headless.sh python3 tests/fallback_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = tempfile.mktemp()
target = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests/target_app.py")],
                          env=dict(os.environ, LCU_TARGET_LOG=log), stderr=subprocess.DEVNULL)
srv = subprocess.Popen([sys.executable, "-m", "lcu"], cwd=ROOT, env=dict(os.environ, LCU_TEST_AUTO_APPROVE="1"),
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
n = 0


def call(name, **args):
    global n
    n += 1
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                                "params": {"name": name, "arguments": args}}) + "\n")
    srv.stdin.flush()
    return json.loads(srv.stdout.readline())["result"]


time.sleep(2.5)
failures = []
call("request_access", apps=["Calculator"], reason="fallback test")  # NOT the target app
call("screenshot")
for name, args in (("left_click", {"coordinate": [678, 330]}), ("type", {"text": "nope"}),
                   ("key", {"text": "Return"})):
    r = call(name, **args)
    msg = r["content"][0]["text"]
    good = r.get("isError") and "cannot identify the frontmost app" in msg
    print(("PASS " if good else "FAIL ") + f"{name} refused: {msg[:110]}")
    if not good:
        failures.append(name)
time.sleep(0.5)
evs = [json.loads(line)["ev"] for line in open(log)] if os.path.exists(log) else []
untouched = not ({"clicked", "text"} & set(evs))
print(("PASS " if untouched else "FAIL ") + f"target received nothing: {evs}")
if not untouched:
    failures.append("target touched")
target.terminate()
srv.stdin.close()
srv.wait(5)
print("ALL PASSED" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
