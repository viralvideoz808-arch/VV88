#!/data/data/com.termux/files/usr/bin/bash
# Stop the VV88 control website on the phone (Termux).
if pkill -f "vv88 web"; then
  echo "VV88 stopped."
else
  echo "VV88 wasn't running."
fi
termux-wake-unlock 2>/dev/null || true
