#!/usr/bin/env bash
# Start a localhost-only VNC desktop for one-time Vagaro authentication.
# Access requires an SSH tunnel; no VNC port is exposed publicly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/data/vagaro_auth_desktop"
PROFILE="$ROOT/data/vagaro_browser_profile"
DISPLAY_NUM=":99"
VNC_PORT=5901
CHROMIUM="/opt/data/home/.local/bin/chromium"
X11VNC="$ROOT/.local-tools/x11vnc/extracted/usr/bin/x11vnc"
X11VNC_LIBS="$ROOT/.local-tools/x11vnc/extracted/usr/lib/aarch64-linux-gnu"

for required in "$CHROMIUM" "$X11VNC" /usr/bin/Xvfb /usr/bin/openssl; do
  if [[ ! -x "$required" ]]; then
    echo "Required executable is unavailable: $required" >&2
    exit 1
  fi
done

install -d -m 700 "$RUNTIME" "$PROFILE"
umask 077

if [[ ! -f "$RUNTIME/vnc_password" ]]; then
  # VNC accepts only the first eight password characters. The user retrieves
  # this one-time password locally over SSH; it is never printed by this script.
  /usr/bin/openssl rand -hex 4 > "$RUNTIME/vnc_password"
  chmod 600 "$RUNTIME/vnc_password"
fi

start_if_not_running() {
  local pid_file="$1"
  shift
  if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
    return
  fi
  "$@" &
  echo $! > "$pid_file"
}

start_if_not_running "$RUNTIME/xvfb.pid" \
  /usr/bin/Xvfb "$DISPLAY_NUM" -screen 0 1440x1000x24 -nolisten tcp \
  >"$RUNTIME/xvfb.log" 2>&1

for _ in $(seq 1 20); do
  [[ -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ]] && break
  sleep 0.25
done
if [[ ! -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ]]; then
  echo "Xvfb did not become ready on $DISPLAY_NUM" >&2
  exit 1
fi

start_if_not_running "$RUNTIME/vnc.pid" \
  env LD_LIBRARY_PATH="$X11VNC_LIBS" "$X11VNC" \
    -display "$DISPLAY_NUM" -localhost -forever -shared -rfbport "$VNC_PORT" \
    -passwdfile "$RUNTIME/vnc_password" -o "$RUNTIME/x11vnc.log"

if [[ ! -f "$RUNTIME/chromium.pid" ]] || ! kill -0 "$(<"$RUNTIME/chromium.pid")" 2>/dev/null; then
  DISPLAY="$DISPLAY_NUM" "$CHROMIUM" \
    --no-sandbox --disable-dev-shm-usage --disable-gpu \
    --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check --disable-sync \
    --window-size=1440,1000 "https://www.vagaro.com/login" \
    >"$RUNTIME/chromium.log" 2>&1 &
  echo $! > "$RUNTIME/chromium.pid"
fi

printf 'Vagaro login desktop is ready on localhost:%s.\n' "$VNC_PORT"
printf 'Use an SSH tunnel, then connect your Mac Screen Sharing app to vnc://localhost:%s.\n' "$VNC_PORT"
printf 'Retrieve the VNC password over your own SSH session from %s.\n' "$RUNTIME/vnc_password"
