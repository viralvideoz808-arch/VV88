#!/data/data/com.termux/files/usr/bin/bash
# Stop the VV88 control website on the phone (Termux).
cd "$(dirname "$0")/.." || exit 1
PIDFILE=data/web.pid
if [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "VV88 stopped."
else
  echo "VV88 wasn't running (started by termux_start.sh)."
fi
rm -f "$PIDFILE"
termux-wake-unlock 2>/dev/null || true
