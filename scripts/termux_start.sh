#!/data/data/com.termux/files/usr/bin/bash
# Start the VV88 control website in the background on the phone (Termux) and open it in the browser.
#
#   cd ~/VV88 && bash scripts/termux_start.sh
cd "$(dirname "$0")/.." || exit 1
mkdir -p data
PORT="${VV88_PORT:-8088}"
URL="http://127.0.0.1:$PORT"

termux-wake-lock 2>/dev/null || true   # keep Android from pausing it while the screen is off

if pgrep -f "vv88 web" >/dev/null 2>&1; then
  echo "VV88 is already running."
else
  nohup vv88 web --host 127.0.0.1 --port "$PORT" >> data/web.log 2>&1 &
  for _ in $(seq 1 60); do
    curl -s -o /dev/null "$URL/login" && break
    sleep 1
  done
fi

if curl -s -o /dev/null "$URL/login"; then
  echo "Control website: $URL"
  PW="$(grep -m1 '^VV88_WEB_PASSWORD=' .env 2>/dev/null | cut -d= -f2-)"
  [ -n "$PW" ] && echo "Password:        $PW"
  termux-open-url "$URL" 2>/dev/null || true
  echo "Logs: tail -f data/web.log      Stop: bash scripts/termux_stop.sh"
else
  echo "VV88 didn't start. Last lines of data/web.log:"
  tail -n 20 data/web.log
  exit 1
fi
