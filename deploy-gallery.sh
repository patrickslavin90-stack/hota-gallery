#!/bin/sh
# Update the program on the gallery Pi (installed by hand from
# hota-gallery.service's notes: /opt/hota-gallery, /etc/hota-gallery).
# Run from a folder holding this script and the new hota_gallery/:
#
#   sudo sh deploy-gallery.sh
#
# Backs up the running program to hota_gallery.bak.<date> (as earlier
# updates did), checks the live config loads in the new code before
# switching, restarts the service, and puts the backup back by itself if
# the service doesn't come up. The config is never touched.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DIR=/opt/hota-gallery
CONFIG=/etc/hota-gallery/config.json
STAMP=$(date +%Y%m%d-%H%M%S)

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo sh $0" >&2; exit 1; }
[ -d "$DIR/hota_gallery" ] || { echo "No program at $DIR/hota_gallery - is this the gallery Pi?" >&2; exit 1; }

OWNER=$(stat -c %U:%G "$DIR/hota_gallery")
rm -rf "$DIR/hota_gallery.new"
cp -r "$HERE/hota_gallery" "$DIR/hota_gallery.new"
find "$DIR/hota_gallery.new" -name __pycache__ -type d -prune -exec rm -rf {} +
chown -R "$OWNER" "$DIR/hota_gallery.new"

echo "Checking the live config loads in the new program..."
python3 -B -c "import sys; sys.path.insert(0, '$HERE'); from hota_gallery.config import load_config; load_config('$CONFIG')" \
  || { rm -rf "$DIR/hota_gallery.new"; echo "The live config doesn't load in the new program - nothing changed." >&2; exit 1; }

cp -a "$DIR/hota_gallery" "$DIR/hota_gallery.bak.$STAMP"
rm -rf "$DIR/hota_gallery"
mv "$DIR/hota_gallery.new" "$DIR/hota_gallery"
echo "Backed up the old program to $DIR/hota_gallery.bak.$STAMP; restarting..."
systemctl restart hota-gallery
sleep 5
if systemctl is-active --quiet hota-gallery; then
  echo "Done - the gallery controller is running the new program."
  echo "To undo:  sudo rm -rf $DIR/hota_gallery && sudo cp -a $DIR/hota_gallery.bak.$STAMP $DIR/hota_gallery && sudo systemctl restart hota-gallery"
else
  echo "The service didn't come up - putting the old program back." >&2
  rm -rf "$DIR/hota_gallery"
  cp -a "$DIR/hota_gallery.bak.$STAMP" "$DIR/hota_gallery"
  systemctl restart hota-gallery
  journalctl -u hota-gallery -n 30 --no-pager >&2
  exit 1
fi
