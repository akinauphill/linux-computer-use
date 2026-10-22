"""switch_display across two virtual monitors. Run:
    LCU_HEADLESS_EXTRA="--virtual-monitor 1280x800" tests/run_headless.sh python3 tests/display_test.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


call("request_access", apps=["Calculator"], reason="display test")
displays = json.loads(call("switch_display")["content"][0]["text"])
print("displays:", displays)
sizes = []
for d in displays:
    r = call("switch_display", display=d["connector"])
    print(r["content"][0]["text"])
    shot = call("screenshot")
    sizes.append(shot["content"][1]["text"])
    print("  ", sizes[-1])
ok = len(displays) == 2 and len(set(sizes)) == 2
print("ALL PASSED" if ok else "FAILED")
srv.stdin.close()
srv.wait(5)
sys.exit(0 if ok else 1)
