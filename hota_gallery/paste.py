"""Paste board: a shared scratchpad for setting up machines - paste a command
on one computer, open the same page on another, press Copy. Hosted by the
lighting controller at /paste, and runnable on its own anywhere with Python 3:

    python3 paste.py                 # http://<this machine>:8090/
    python3 paste.py --port 9000

Standard library only and self-contained (no imports from the rest of the
package), so this one file can be copied to any machine and run.

Pastes live in memory only - the last 50, gone on restart - and anyone on
the network who can reach the page can read and post. It's for commands,
not passwords or keys; the page says so.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

MAX_ITEMS = 50
MAX_CHARS = 100_000  # a long config file, not a disk image


class PasteBoard:
    def __init__(self, max_items: int = MAX_ITEMS):
        self._items: deque = deque(maxlen=max_items)
        self._lock = threading.Lock()
        self._next_id = 1

    def add(self, text: str, source: str) -> Dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("nothing to paste")
        if len(text) > MAX_CHARS:
            raise ValueError(f"too long ({len(text)} characters, the limit is {MAX_CHARS})")
        with self._lock:
            item = {"id": self._next_id, "text": text, "at": time.time(), "from": source}
            self._next_id += 1
            self._items.appendleft(item)
            return item

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)

    def delete(self, item_id: int) -> None:
        with self._lock:
            self._items = deque((i for i in self._items if i["id"] != item_id), maxlen=self._items.maxlen)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


BOARD = PasteBoard()


def handle(h: BaseHTTPRequestHandler, method: str, board: PasteBoard = BOARD) -> bool:
    """Serve the paste routes on any BaseHTTPRequestHandler. Returns False
    if the path isn't one of ours, so the caller can carry on routing."""
    path = h.path.split("?", 1)[0]
    if method == "GET" and path in ("/paste", "/paste/"):
        return _send(h, 200, PAGE.encode(), "text/html; charset=utf-8")
    if not path.startswith("/api/paste"):
        return False
    try:
        if method == "GET" and path == "/api/paste":
            return _send_json(h, 200, board.list())
        if method == "POST" and path == "/api/paste":
            length = int(h.headers.get("Content-Length", 0))
            if length > MAX_CHARS * 4 + 1024:
                return _send_json(h, 413, {"error": "too long"})
            body = json.loads(h.rfile.read(length).decode("utf-8") or "{}")
            return _send_json(h, 200, board.add(body.get("text", ""), h.client_address[0]))
        if method == "DELETE" and path == "/api/paste":
            board.clear()
            return _send_json(h, 200, [])
        if method == "DELETE" and path.startswith("/api/paste/"):
            board.delete(int(path.rsplit("/", 1)[1]))
            return _send_json(h, 200, board.list())
    except (ValueError, UnicodeDecodeError) as exc:
        return _send_json(h, 400, {"error": str(exc)})
    return _send_json(h, 404, {"error": "not found"})


def _send(h: BaseHTTPRequestHandler, status: int, body: bytes, ctype: str) -> bool:
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)
    return True


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: Any) -> bool:
    return _send(h, status, json.dumps(payload).encode(), "application/json")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paste board</title>
<style>
  :root { --bg:#0f0e0e; --surface:#1c1a1a; --surface-2:#282727; --line:#343232; --text:#f5f5f5; --muted:#a3a1a1; --accent:#d5134d; --warn:#fcbb00; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif; }
  main { max-width:900px; margin:0 auto; padding:20px 16px 60px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 16px; font-size:13px; }
  .warn { border:1px solid rgba(252,187,0,.4); color:var(--warn); background:rgba(252,187,0,.07); border-radius:10px; padding:8px 12px; font-size:13px; margin-bottom:16px; }
  textarea { width:100%; min-height:120px; resize:vertical; background:var(--surface); color:var(--text); border:1px solid var(--line); border-radius:12px; padding:12px; font:13px/1.45 ui-monospace,"Cascadia Code",Consolas,"DejaVu Sans Mono",monospace; }
  textarea:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .row { display:flex; gap:8px; align-items:center; margin:10px 0 22px; flex-wrap:wrap; }
  button { background:var(--surface-2); color:var(--text); border:1px solid var(--line); border-radius:999px; padding:7px 16px; font:inherit; font-size:14px; cursor:pointer; }
  button:hover { border-color:#5a5757; }
  button.primary { background:var(--accent); border-color:var(--accent); font-weight:600; }
  button.ghost { background:none; }
  .hint { color:var(--muted); font-size:12px; }
  .item { background:var(--surface); border:1px solid var(--line); border-radius:12px; margin-bottom:12px; overflow:hidden; }
  .item header { display:flex; align-items:center; gap:8px; padding:8px 10px 8px 14px; border-bottom:1px solid var(--line); font-size:12px; color:var(--muted); }
  .item header .grow { flex:1; }
  .item pre { margin:0; padding:12px 14px; white-space:pre-wrap; word-break:break-word; font:13px/1.45 ui-monospace,"Cascadia Code",Consolas,"DejaVu Sans Mono",monospace; max-height:420px; overflow:auto; }
  .item.copied { border-color:#3ddc97; }
  .empty { color:var(--muted); text-align:center; padding:30px 0; }
  .err { color:#ff8fa8; }
</style>
</head>
<body>
<main>
  <a id="back" href="/" hidden style="color:var(--muted);font-size:13px;text-decoration:none">&larr; Back to the lighting control</a>
  <h1>Paste board</h1>
  <p class="sub">Paste on one machine, copy on another. Ctrl+Enter sends.</p>
  <div class="warn">Anyone on this network can read this page, and it's cleared when the controller restarts. Fine for commands, not for passwords or keys.</div>
  <textarea id="text" placeholder="Paste a command or some config here" spellcheck="false" autofocus></textarea>
  <div class="row">
    <button class="primary" id="send">Send</button>
    <span class="hint" id="status"></span>
    <span style="flex:1"></span>
    <button class="ghost" id="clear">Clear all</button>
  </div>
  <div id="list"></div>
</main>
<script>
(() => {
  const $ = id => document.getElementById(id);
  const text = $("text"), list = $("list"), status = $("status");
  let items = [], lastSig = "";

  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined, cache: "no-store" });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  function ago(t) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  // navigator.clipboard only works on https or localhost; a plain
  // http://10.x.x.x page needs the old execCommand route.
  async function copy(str) {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(str); return; }
    const ta = document.createElement("textarea");
    ta.value = str; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.append(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) throw new Error("copy blocked");
  }
  function render() {
    const sig = items.map(i => i.id).join(",");
    if (sig === lastSig) { list.querySelectorAll("[data-at]").forEach(el => el.textContent = ago(+el.dataset.at)); return; }
    lastSig = sig;
    list.replaceChildren(...(items.length ? items.map(it => {
      const card = document.createElement("div"); card.className = "item";
      const head = document.createElement("header");
      const when = document.createElement("span"); when.dataset.at = it.at; when.textContent = ago(it.at);
      const from = document.createElement("span"); from.className = "grow"; from.textContent = "from " + it.from;
      const lines = it.text.split("\n").length;
      const size = document.createElement("span"); size.textContent = lines > 1 ? lines + " lines" : "";
      const copyBtn = document.createElement("button"); copyBtn.textContent = "Copy";
      copyBtn.onclick = async () => {
        try { await copy(it.text); copyBtn.textContent = "Copied"; card.classList.add("copied"); setTimeout(() => { copyBtn.textContent = "Copy"; card.classList.remove("copied"); }, 1500); }
        catch { copyBtn.textContent = "Select it and Ctrl+C"; }
      };
      const del = document.createElement("button"); del.className = "ghost"; del.textContent = "Delete"; del.title = "Delete this paste";
      del.onclick = async () => { items = await api("DELETE", "/api/paste/" + it.id); render(); };
      head.append(when, from, size, copyBtn, del);
      const pre = document.createElement("pre"); pre.textContent = it.text;
      card.append(head, pre);
      return card;
    }) : [Object.assign(document.createElement("p"), { className: "empty", textContent: "Nothing pasted yet." })]));
  }
  async function refresh() {
    try { items = await api("GET", "/api/paste"); status.textContent = ""; status.className = "hint"; render(); }
    catch { status.textContent = "Can't reach the board, retrying"; status.className = "hint err"; }
  }
  async function send() {
    if (!text.value.trim()) return;
    try { await api("POST", "/api/paste", { text: text.value }); text.value = ""; await refresh(); text.focus(); }
    catch (e) { status.textContent = e.message; status.className = "hint err"; }
  }
  $("send").onclick = send;
  text.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
  $("clear").onclick = async () => {
    if (!items.length || !confirm("Delete every paste?")) return;
    items = await api("DELETE", "/api/paste"); render();
  };
  // On a lighting controller (not the standalone script), link back to the
  // lighting UI - the kiosk screen has no browser back button.
  fetch("/api/status", { cache: "no-store" }).then(r => { if (r.ok && (r.headers.get("content-type") || "").includes("json")) $("back").hidden = false; }).catch(() => {});
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 2000);
})();
</script>
</body>
</html>
"""


class _StandaloneHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            self.path = "/paste"
        if not handle(self, "GET"):
            _send_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        if not handle(self, "POST"):
            _send_json(self, 404, {"error": "not found"})

    def do_DELETE(self) -> None:
        if not handle(self, "DELETE"):
            _send_json(self, 404, {"error": "not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def _local_ips() -> List[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Paste board: share commands between machines on this network.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args(argv)
    httpd = ThreadingHTTPServer((args.host, args.port), _StandaloneHandler)
    print("Paste board running. Open from another machine:")
    for ip in _local_ips():
        print(f"  http://{ip}:{args.port}/")
    print(f"  http://localhost:{args.port}/   (this machine)")
    print("Ctrl+C to stop. Pastes are kept in memory only.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
