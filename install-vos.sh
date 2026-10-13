#!/bin/sh
# Install the HOTA outdoor stage (VOS) lighting controller on a Linux PC,
# from a folder holding this script, hota_gallery/, config.json and
# hota-vos.service (tools/make_vos_installer.py builds that folder).
#
#   sudo sh install-vos.sh            # sACN goes nowhere (127.0.0.1) until
#                                     # you pick the lighting network card
#   sudo sh install-vos.sh --live     # send sACN straight away, on the
#                                     # card the PC picks automatically
#   sudo sh install-vos.sh --kiosk USER
#                                     # also open the control full-screen
#                                     # on this PC's screen, logging USER in
#                                     # automatically at boot (like the
#                                     # gallery Pi's kiosk)
#
# Safe to run again to update: the program is replaced, but an existing
# config (looks, schedules, presets) is kept unless --reset-config is given.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
LIVE=0
RESET=0
KIOSK_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE=1 ;;
    --reset-config) RESET=1 ;;
    --kiosk)
      [ $# -ge 2 ] || { echo "--kiosk needs the desktop user to log in, e.g. --kiosk $(logname 2>/dev/null || echo USER)" >&2; exit 2; }
      KIOSK_USER=$2; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$KIOSK_USER" ] && ! id "$KIOSK_USER" >/dev/null 2>&1; then
  echo "No user called $KIOSK_USER on this PC." >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo:  sudo sh $0 $*" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 isn't installed. On Ubuntu/Debian: sudo apt install python3" >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || {
  echo "python3 is older than 3.8 - too old for this program." >&2
  exit 1
}
if ! command -v systemctl >/dev/null 2>&1; then
  echo "This PC doesn't use systemd, which this installer needs." >&2
  exit 1
fi

echo "Installing the HOTA outdoor stage lighting controller..."

# Service user, no login, no home.
if ! id hota-vos >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin hota-vos
fi

# Program.
systemctl stop hota-vos 2>/dev/null || true
install -d -o hota-vos -g hota-vos /opt/hota-vos
rm -rf /opt/hota-vos/hota_gallery
cp -r "$HERE/hota_gallery" /opt/hota-vos/hota_gallery
find /opt/hota-vos/hota_gallery -name __pycache__ -type d -prune -exec rm -rf {} +
chown -R hota-vos:hota-vos /opt/hota-vos

# Config. The service user needs write on the directory itself, for the
# atomic save (write .tmp, then rename).
install -d -m 0750 -o hota-vos -g hota-vos /etc/hota-vos
if [ ! -f /etc/hota-vos/config.json ] || [ "$RESET" -eq 1 ]; then
  install -o hota-vos -g hota-vos -m 0640 "$HERE/config.json" /etc/hota-vos/config.json
  if [ "$LIVE" -eq 0 ]; then
    # Loopback: nothing reaches the lights until the network card is set
    # in Settings -> Device.
    python3 - <<'EOF'
import json
p = "/etc/hota-vos/config.json"
c = json.load(open(p))
c["bind_ip"] = "127.0.0.1"
json.dump(c, open(p, "w"), indent=2)
EOF
  fi
else
  echo "Keeping the existing config at /etc/hota-vos/config.json"
fi
python3 -c "import sys; sys.path.insert(0, '/opt/hota-vos'); from hota_gallery.config import load_config; load_config('/etc/hota-vos/config.json')" \
  || { echo "The config didn't validate - not starting." >&2; exit 1; }

# Service, started now and at every boot.
install -m 0644 "$HERE/hota-vos.service" /etc/systemd/system/hota-vos.service
systemctl daemon-reload
systemctl enable --now hota-vos

# Lighting network: let the service (and only it) set the wired port's
# address through NetworkManager, from Settings -> sACN output.
if command -v nmcli >/dev/null 2>&1 && [ -d /etc/polkit-1/rules.d ]; then
  cat > /etc/polkit-1/rules.d/50-hota-vos-network.rules <<'EOF'
// HOTA outdoor stage controller: may change NetworkManager connections, so
// the lighting port's address can be set from its web UI (netconfig.py).
polkit.addRule(function (action, subject) {
  if (subject.user == "hota-vos" &&
      (action.id == "org.freedesktop.NetworkManager.settings.modify.system" ||
       action.id == "org.freedesktop.NetworkManager.network-control")) {
    return polkit.Result.YES;
  }
});
EOF
  echo "The lighting port's address can be set from Settings -> sACN output."
else
  echo "NetworkManager or polkit not found: set the lighting port's address on the PC itself." >&2
fi

# Let phones and tablets on the network reach the web page.
PORT=$(python3 -c "import json; print(json.load(open('/etc/hota-vos/config.json')).get('web_port', 8080))")
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && echo "Opened port $PORT in the firewall (ufw)."
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null && echo "Opened port $PORT in the firewall (firewalld)."
fi

# Kiosk: full-screen browser on this PC's display at login.
if [ -n "$KIOSK_USER" ]; then
  install -m 0755 "$HERE/vos-kiosk.sh" /opt/hota-vos/vos-kiosk.sh
  HOME_DIR=$(getent passwd "$KIOSK_USER" | cut -d: -f6)
  install -d -o "$KIOSK_USER" -g "$(id -gn "$KIOSK_USER")" "$HOME_DIR/.config" "$HOME_DIR/.config/autostart"
  cat > "$HOME_DIR/.config/autostart/hota-vos-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=HOTA outdoor stage kiosk
Exec=/opt/hota-vos/vos-kiosk.sh
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
  chown "$KIOSK_USER":"$(id -gn "$KIOSK_USER")" "$HOME_DIR/.config/autostart/hota-vos-kiosk.desktop"
  # Applications-menu entry to go back into the kiosk after Alt+F4.
  cat > /usr/share/applications/hota-vos-kiosk.desktop <<EOF
[Desktop Entry]
Type=Application
Name=HOTA lighting (kiosk)
Comment=Open the outdoor stage lighting control full-screen
Exec=/opt/hota-vos/vos-kiosk.sh
Icon=video-display
Categories=Utility;
EOF

  # Log that user in automatically, so the screen comes up after a power cut.
  # GDM keeps it in custom.conf on Ubuntu/Fedora, daemon.conf on Debian.
  GDM=""
  for f in /etc/gdm3/custom.conf /etc/gdm3/daemon.conf /etc/gdm/custom.conf; do
    if [ -f "$f" ]; then GDM=$f; break; fi
  done
  if [ -n "$GDM" ]; then
    [ -f "$GDM.before-hota-vos" ] || cp "$GDM" "$GDM.before-hota-vos"  # original kept, comments and all
    python3 - "$GDM" "$KIOSK_USER" <<'EOF'
import configparser, sys
path, user = sys.argv[1], sys.argv[2]
c = configparser.ConfigParser(strict=False, interpolation=None)
c.optionxform = str
c.read(path)
if not c.has_section("daemon"):
    c.add_section("daemon")
c.set("daemon", "AutomaticLoginEnable", "True")
c.set("daemon", "AutomaticLogin", user)
with open(path, "w") as f:
    c.write(f)
EOF
    echo "Auto-login set for $KIOSK_USER (GDM)."
  elif [ -d /etc/lightdm ]; then
    install -d /etc/lightdm/lightdm.conf.d
    printf '[Seat:*]\nautologin-user=%s\n' "$KIOSK_USER" > /etc/lightdm/lightdm.conf.d/50-hota-vos-autologin.conf
    echo "Auto-login set for $KIOSK_USER (LightDM)."
  else
    echo "Couldn't set auto-login for this login screen - turn it on in the desktop's user settings." >&2
  fi
  KIOSK_BROWSER=""
  for b in chromium chromium-browser google-chrome google-chrome-stable microsoft-edge firefox; do
    if command -v "$b" >/dev/null 2>&1; then KIOSK_BROWSER=$b; break; fi
  done
  if [ -z "$KIOSK_BROWSER" ]; then
    echo "No browser found for the kiosk - install one, e.g.  sudo apt install chromium" >&2
  else
    echo "Kiosk set up for $KIOSK_USER with $KIOSK_BROWSER - it opens full-screen at the next login."
    echo "  Alt+F4 exits to the desktop; 'HOTA lighting (kiosk)' in the menu goes back in."
  fi
fi

sleep 3
if systemctl is-active --quiet hota-vos; then
  echo
  echo "Done - the controller is running and will start at every boot."
  for ip in $(hostname -I 2>/dev/null); do
    case "$ip" in *:*) ;; *) echo "  Open:  http://$ip:$PORT/" ;; esac
  done
  echo "  On this PC:  http://localhost:$PORT/"
  if grep -q '"bind_ip": "127.0.0.1"' /etc/hota-vos/config.json; then
    echo
    echo "sACN is going nowhere for now (network interface 127.0.0.1)."
    echo "To go live: Settings -> Device -> Network interface -> this PC's address"
    echo "on the lighting network, Save, then:  sudo systemctl restart hota-vos"
  fi
  echo "Logs:  journalctl -u hota-vos -f"
else
  echo "The service didn't start. See:  journalctl -u hota-vos -n 50" >&2
  exit 1
fi
