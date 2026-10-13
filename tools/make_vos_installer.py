"""Assemble the outdoor stage (VOS) installer folder - copy it to a USB
stick, plug that into the Linux PC, and run `sudo sh install-vos.sh` from
it. See install-vos.sh for what the install does.

    python tools/make_vos_installer.py [output folder] [--config CONFIG]

Default output: hota-vos-installer/ in your Downloads folder; default config
data/vos/config.json. The same installer sets up any of the controllers -
e.g. the main building:

    python tools/make_vos_installer.py ~/Downloads/hota-building-installer --config data/building/config.json
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

README = """HOTA outdoor stage (VOS) lighting controller - installer
=========================================================

On the Linux PC:

1. Plug in this USB stick and open a terminal in this folder
   (in the file manager: right-click the folder -> Open in Terminal).

2. Run:

       sudo sh install-vos.sh

   It installs the controller, starts it, and sets it to start at every
   boot. At the end it prints the address to open, e.g.
   http://10.38.12.58:8080/ - open that from a phone or laptop on the
   same network, or http://localhost:8080/ on the PC itself.

3. For safety the lighting output starts switched to 127.0.0.1, so nothing
   reaches the real lights yet. To go live: Settings -> Device -> Network
   interface -> the PC's address on the lighting network -> Save, then run

       sudo systemctl restart hota-vos

   (Or install with  sudo sh install-vos.sh --live  to send sACN straight
   away on whichever network card the PC picks.)

Kiosk (full-screen control on the PC's own screen, like the gallery):

       sudo sh install-vos.sh --kiosk $USER

   logs that user in automatically at boot and opens the control
   full-screen, with no PIN asked on the PC itself. Needs a browser
   (Chromium, Chrome or Firefox). Reboot to see it.

   Leave the kiosk: Alt+F4 - you're back at the normal desktop.
   Go back in: "HOTA lighting (kiosk)" in the applications menu, or log
   out and in, or reboot. (It only reopens on its own if the browser
   crashes.) To stop it starting at login, delete
   ~/.config/autostart/hota-vos-kiosk.desktop

Paste board (for setting up machines):

   The controller hosts a shared scratchpad at  http://<controller>:8080/paste
   - paste a command on one machine, open the page on another, press Copy.
   paste.py in this folder is the same thing on its own: on any machine
   with Python 3, run  python3 paste.py  and open the address it prints
   (port 8090). Anyone on the network can read it; pastes are kept in
   memory only, so it's for commands, not passwords.

To update later, run the installer from a newer USB copy: the program is
replaced and your looks, schedules and presets are kept.

Logs:  journalctl -u hota-vos -f
"""


def main() -> int:
    args = sys.argv[1:]
    config = ROOT / "data" / "vos" / "config.json"
    if "--config" in args:
        i = args.index("--config")
        config = Path(args[i + 1]).resolve()
        del args[i:i + 2]
    out = Path(args[0]) if args else Path.home() / "Downloads" / "hota-vos-installer"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(ROOT / "hota_gallery", out / "hota_gallery", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(config, out / "config.json")
    # Linux line endings, whatever git checked out on Windows.
    for name in ("install-vos.sh", "hota-vos.service", "vos-kiosk.sh"):
        (out / name).write_bytes((ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    # The paste board on its own, for any machine with Python 3.
    (out / "paste.py").write_bytes((ROOT / "hota_gallery" / "paste.py").read_bytes().replace(b"\r\n", b"\n"))
    (out / "README.txt").write_text(README, encoding="utf-8", newline="\n")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
