#!/bin/sh
# HOTA Gallery kiosk - labwc autostart. Opens the lighting control UI
# full-screen on this Pi's own display, no browser chrome, no address bar -
# same idea as "The Basement" house-lights touchscreen.
#
# Install (Raspberry Pi OS Bookworm/Trixie, labwc desktop, autologin already
# configured via raspi-config - this script only adds what launches once
# that session starts):
#   cp kiosk-autostart.sh ~/.config/labwc/autostart
#   chmod +x ~/.config/labwc/autostart
#   sudo raspi-config nonint do_blanking 1   # disable screen blanking
#   sudo reboot
#
# Older Raspberry Pi OS (X11/wayfire) needs the equivalent autostart file
# for that session instead - ~/.config/lxsession/LXDE-pi/autostart or
# ~/.config/wayfire.ini's [autostart] section - this file assumes labwc.
#
# The desktop session and the hota-gallery systemd service start
# independently at boot, so on a cold boot the browser can easily win the
# race and hit the port before the service is listening - that lands on a
# "can't reach this page" error that Chromium never retries on its own.
# Wait for it to actually answer first.
wait_for_server() {
  i=0
  while [ "$i" -lt 60 ]; do
    if curl -fs -o /dev/null http://localhost:8080/; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
}
wait_for_server

# If Chromium ever crashes or is closed, relaunch it rather than leaving a
# blank/frozen screen - same "never give up" philosophy as the systemd
# service's own Restart=always.
(
  while true; do
    chromium \
      --kiosk \
      --noerrdialogs \
      --disable-infobars \
      --no-first-run \
      --disable-session-crashed-bubble \
      --check-for-update-interval=31536000 \
      --overscroll-history-navigation=0 \
      http://localhost:8080/
    sleep 2
  done
) &
