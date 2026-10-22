"""Launch the target app and probe WindowAt across its frame (headless debugging)."""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu.shell import Shell  # noqa: E402

p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "target_app.py")],
                     stderr=subprocess.DEVNULL)
time.sleep(2.5)
sh = Shell()
print("state", sh.state())
for w in sh.windows() or []:
    print("win", w["title"], w["x"], w["y"], w["width"], w["height"], w["focused"])
    for fx, fy in ((0.5, 0.3), (0.5, 0.5), (0.2, 0.8)):
        x, y = w["x"] + w["width"] * fx, w["y"] + w["height"] * fy
        print("  at", round(x), round(y), "->", sh.window_at(x, y))
p.terminate()
