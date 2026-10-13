#!/bin/sh
# HOTA outdoor stage kiosk - opens the lighting control full-screen on this
# PC's own display, like the gallery Pi's kiosk-autostart.sh but for a
# regular Linux desktop. Started at login by the autostart entry that
# `install-vos.sh --kiosk <user>` adds; works with GNOME, KDE, XFCE, Cinnamon
# and MATE (anything that honours ~/.config/autostart).

PORT=$(python3 -c "import json; print(json.load(open('/etc/hota-vos/config.json')).get('web_port', 8080))" 2>/dev/null || echo 8080)
URL="http://localhost:$PORT/"

# Keep the screen on: no blanking, no lock (GNOME/Cinnamon/MATE settings,
# then plain X11; each is a no-op where it doesn't apply).
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null
gsettings set org.gnome.settings-daemon.plugins.power idle-dim false 2>/dev/null
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing 2>/dev/null
xset s off -dpms 2>/dev/null

# The service and the desktop start independently, so wait for the
# controller to answer before opening the page (a browser that hits it too
# early shows an error and never retries).
i=0
while [ "$i" -lt 90 ]; do
  python3 -c "import urllib.request; urllib.request.urlopen('$URL', timeout=2)" 2>/dev/null && break
  i=$((i + 1))
  sleep 1
done

# First browser found wins. Relaunch it if it crashes; closing it on
# purpose (Alt+F4) exits the kiosk to the desktop. "HOTA lighting (kiosk)"
# in the applications menu, logging in again or a reboot brings it back.
for b in chromium chromium-browser google-chrome google-chrome-stable microsoft-edge firefox; do
  if command -v "$b" >/dev/null 2>&1; then BROWSER=$b; break; fi
done
if [ -z "${BROWSER:-}" ]; then
  echo "vos-kiosk: no browser found (chromium, google-chrome or firefox)" >&2
  exit 1
fi

while true; do
  case "$BROWSER" in
    firefox)
      firefox --kiosk "$URL" ;;
    *)
      "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
        --disable-session-crashed-bubble --check-for-update-interval=31536000 \
        --overscroll-history-navigation=0 "$URL" ;;
  esac
  # Exit status 0 = closed normally (Alt+F4), anything else = crashed.
  [ $? -eq 0 ] && exit 0
  sleep 2
done
