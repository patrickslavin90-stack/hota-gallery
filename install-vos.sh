#!/bin/sh
# Install the HOTA outdoor stage (VOS) lighting controller on a Linux PC,
# from a folder holding this script, hota_gallery/, config.json and
# hota-vos.service (tools/make_vos_installer.py builds that folder).
#
#   sudo sh install-vos.sh            # sACN goes nowhere (127.0.0.1) until
#                                     # you pick the lighting network card
#   sudo sh install-vos.sh --live     # send sACN straight away, on the
#                                     # card the PC picks automatically
#
# Safe to run again to update: the program is replaced, but an existing
# config (looks, schedules, presets) is kept unless --reset-config is given.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
LIVE=0
RESET=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    --reset-config) RESET=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

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

# Let phones and tablets on the network reach the web page.
PORT=$(python3 -c "import json; print(json.load(open('/etc/hota-vos/config.json')).get('web_port', 8080))")
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && echo "Opened port $PORT in the firewall (ufw)."
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null && echo "Opened port $PORT in the firewall (firewalld)."
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
