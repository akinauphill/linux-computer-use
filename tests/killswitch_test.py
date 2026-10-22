"""Press the real Super+Alt+Escape shortcut on the live desktop and check lcu-stop ran."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu import keys  # noqa: E402
from lcu.mutter import MutterSession  # noqa: E402

stop = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "lcu", "stop")
if os.path.exists(stop):
    os.unlink(stop)
s = MutterSession()
s.start()
combo = keys.parse_combo("super+alt+Escape")
for k in combo:
    s.keysym(k, True)
    time.sleep(0.03)
for k in reversed(combo):
    s.keysym(k, False)
    time.sleep(0.03)
s.stop()
for _ in range(30):
    if os.path.exists(stop):
        break
    time.sleep(0.1)
ok = os.path.exists(stop)
print("PASS kill-switch shortcut ran lcu-stop" if ok else "FAIL stop file not created")
if ok:
    os.unlink(stop)
sys.exit(0 if ok else 1)
