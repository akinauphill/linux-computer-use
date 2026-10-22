#!/bin/bash
# Real-client check: Claude Code calls the installed server inside a private headless shell
# (run via tests/run_headless.sh so no real-desktop pixels are sent anywhere).
#   tests/run_headless.sh tests/claude_roundtrip.sh
set -euo pipefail
gnome-calculator >/dev/null 2>&1 &
sleep 2
CFG='{"mcpServers":{"lcu":{"command":"'"$HOME"'/.local/bin/linux-computer-use","env":{"LCU_TEST_AUTO_APPROVE":"1"}}}}'
claude -p "Use the lcu tools: call request_access with apps [\"Calculator\"] and reason \"round-trip test\", \
then take one screenshot. Reply with exactly: the screenshot's pixel size if you can tell, the title of \
the Calculator window, and which digits are visible on its keypad." \
    --mcp-config "$CFG" --strict-mcp-config \
    --allowedTools "mcp__lcu__request_access mcp__lcu__screenshot"
