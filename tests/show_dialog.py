"""Render the approval dialog (run under tests/run_headless.sh) and save a screenshot to argv[1]."""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lcu.mutter import MutterSession  # noqa: E402

req = {"reason": "Rename the screenshots in ~/Pictures by date and open the newest one.",
       "apps": [{"name": "Files", "id": "org.gnome.Nautilus", "tier": "full"},
                {"name": "Mozilla Firefox", "id": "firefox", "tier": "read"},
                {"name": "Console", "id": "org.gnome.Console", "tier": "click"}],
       "unresolved": ["Photoshop"], "clipboardWrite": True, "systemKeyCombos": True}
p = subprocess.Popen([sys.executable, "-m", "lcu.approve"], cwd=ROOT, stdin=subprocess.PIPE, text=True,
                     stderr=subprocess.DEVNULL)
p.stdin.write(json.dumps(req))
p.stdin.close()
time.sleep(2.5)
s = MutterSession()
s.start()
s.capture().save(sys.argv[1])
s.stop()
p.terminate()
