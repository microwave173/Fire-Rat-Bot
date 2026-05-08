#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/at_bot
mkdir -p logs dy/chrome_profile

export DISPLAY=:99

if ! pgrep -f "Xvfb :99" >/dev/null; then
  nohup Xvfb :99 -screen 0 1280x900x24 -ac > logs/xvfb.log 2>&1 &
  sleep 1
fi

if ! pgrep -f "fluxbox" >/dev/null; then
  nohup fluxbox > logs/fluxbox.log 2>&1 &
  sleep 1
fi

if ! pgrep -f "x11vnc.*:99" >/dev/null; then
  nohup x11vnc -display :99 -localhost -nopw -forever -shared -rfbport 5901 > logs/x11vnc.log 2>&1 &
  sleep 1
fi

if ! pgrep -f "websockify.*6080" >/dev/null; then
  nohup websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5901 > logs/novnc.log 2>&1 &
  sleep 1
fi

nohup google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/home/ubuntu/at_bot/dy/chrome_profile \
  --no-first-run \
  --no-default-browser-check \
  --no-sandbox \
  https://www.douyin.com/ > logs/chrome.log 2>&1 &

echo "noVNC running. Open through SSH tunnel: http://127.0.0.1:6080/vnc.html"
