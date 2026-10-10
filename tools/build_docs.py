#!/usr/bin/env python3
"""Builds docs/technical.html - the HOTA-branded HTML version of
docs/TECHNICAL.md - so the two never drift apart. Standard library only;
handles the Markdown this document uses (headings, paragraphs, lists,
tables, fenced code, inline code, bold, links).

    python tools/build_docs.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "TECHNICAL.md"
OUT = ROOT / "docs" / "technical.html"
LOGO = ROOT / "hota_gallery" / "static" / "hota-logo.svg"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def inline(text: str) -> str:
    codes = []

    def keep(m):
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r'(?<![">])(https?://[^\s<)]+)', r'<a href="\1">\1</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def convert(md: str):
    lines = md.splitlines()
    out, toc, i = [], [], 0
    title = ""
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            lang = line[3:].strip() or "text"
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append(f'<pre class="code" data-lang="{html.escape(lang)}"><code>{html.escape(chr(10).join(buf))}</code></pre>')
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1:
                title = text
            else:
                sid = slug(text)
                toc.append((level, text, sid))
                out.append(f'<h{level} id="{sid}">{inline(text)}<a class="anchor" href="#{sid}" aria-label="Link to this section">#</a></h{level}>')
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                # Split on unescaped pipes only; "\|" is a literal pipe inside a cell.
                cells = re.split(r"(?<!\\)\|", lines[i].strip().strip("|"))
                rows.append([c.strip().replace("\\|", "|") for c in cells])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            empty_head = all(not c for c in head)
            t = ['<div class="table"><table>']
            if not empty_head:
                t.append("<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>")
            t.append("<tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
            out.append("".join(t))
            continue
        if re.match(r"^\s*-\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*-\s+", lines[i]):
                items.append(re.sub(r"^\s*-\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue
        if not line.strip():
            i += 1
            continue
        buf = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|```|\||\s*-\s|\d+\.\s)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return title, toc, "\n".join(out)


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HOTA Gallery Lighting Technical Docs</title>
<meta name="description" content="Technical documentation for the HOTA Gallery façade lighting controller and web UI.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0f0e0e; --surface: #1c1a1a; --surface-2: #282727; --line: #343232; --line-2: #484545;
  --text: #f5f5f5; --text-2: #c9c8c8; --muted: #8f8e8e; --accent: #d5134d; --accent-2: #f0336c;
  --pill: 999px; --radius: 16px; color-scheme: dark;
}
:root[data-theme="light"] { color-scheme: dark; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 76px; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.6 "Rubik", system-ui, sans-serif; -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums; }
a { color: var(--accent-2); text-underline-offset: 2px; }
a:hover { color: #fff; }
:focus-visible { outline: 2px solid var(--accent-2); outline-offset: 2px; }
.top { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 14px; height: 56px; padding: 0 20px; background: rgba(15,14,14,.92); backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
.top svg { height: 18px; width: auto; display: block; }
.top span { color: var(--muted); padding-left: 14px; border-left: 1px solid var(--line-2); }
.top .meta { margin-left: auto; border: 0; padding: 0; font-size: 13px; }
.wrap { display: grid; grid-template-columns: 260px minmax(0, 1fr); max-width: 1240px; margin: 0 auto; }
nav.toc { position: sticky; top: 56px; align-self: start; max-height: calc(100vh - 56px); overflow-y: auto; padding: 28px 16px 40px 20px; border-right: 1px solid var(--line); scrollbar-width: thin; scrollbar-color: var(--line-2) transparent; }
nav.toc summary { list-style: none; margin: 0 0 10px; color: var(--muted); font-size: 12px; cursor: default; }
nav.toc summary::-webkit-details-marker { display: none; }
nav.toc a { display: block; padding: 5px 12px; border-radius: var(--pill); color: var(--text-2); text-decoration: none; font-size: 13px; line-height: 1.35; }
nav.toc a.l3 { padding-left: 24px; color: var(--muted); font-size: 12.5px; }
nav.toc a:hover { background: var(--surface-2); color: var(--text); }
nav.toc a.on { background: var(--accent); color: #fff; }
main { padding: 36px 44px 90px; min-width: 0; }
.hero { padding-bottom: 26px; margin-bottom: 8px; border-bottom: 1px solid var(--line); }
.hero .rule { width: 56px; height: 3px; background: var(--accent); border-radius: 2px; margin-bottom: 18px; }
h1 { margin: 0; font-size: clamp(28px, 4vw, 40px); font-weight: 500; line-height: 1.15; letter-spacing: -0.01em; }
.hero p { margin: 10px 0 0; color: var(--text-2); max-width: 70ch; }
h2 { margin: 52px 0 14px; font-size: 24px; font-weight: 500; line-height: 1.25; padding-top: 8px; }
h3 { margin: 30px 0 10px; font-size: 17px; font-weight: 500; color: var(--text); }
h2 .anchor, h3 .anchor { margin-left: 10px; color: var(--line-2); text-decoration: none; font-weight: 400; opacity: 0; transition: opacity .15s; }
h2:hover .anchor, h3:hover .anchor { opacity: 1; }
p, li { color: var(--text-2); max-width: 78ch; }
strong { color: var(--text); font-weight: 500; }
ul, ol { padding-left: 22px; }
li { margin: 4px 0; }
li::marker { color: var(--accent); }
code { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: .88em; background: var(--surface-2); color: #f3d6de; padding: 1px 6px; border-radius: 6px; }
pre.code { position: relative; margin: 16px 0; padding: 16px 18px; background: #151414; border: 1px solid var(--line); border-radius: 12px; overflow-x: auto; font-size: 13px; line-height: 1.55; }
pre.code code { background: none; padding: 0; color: #e8e6e6; font-size: inherit; }
pre.code::before { content: attr(data-lang); position: absolute; top: 8px; right: 12px; font: 11px Rubik, sans-serif; color: var(--muted); }
.table { margin: 16px 0; overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { text-align: left; font-weight: 400; color: var(--muted); font-size: 12px; padding: 10px 14px; border-bottom: 1px solid var(--line); background: #191717; }
td { padding: 9px 14px; border-bottom: 1px solid var(--line); vertical-align: top; color: var(--text-2); }
tr:last-child td { border-bottom: 0; }
td:first-child { color: var(--text); }
.foot { margin-top: 70px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
@media (max-width: 900px) {
  .wrap { grid-template-columns: 1fr; }
  nav.toc { position: static; max-height: none; border-right: 0; border-bottom: 1px solid var(--line); padding: 12px 16px; }
  nav.toc summary { cursor: pointer; margin: 0; padding: 8px 14px; border: 1px solid var(--line-2); border-radius: var(--pill); color: var(--text); font-size: 14px; display: inline-block; }
  nav.toc details[open] summary { margin-bottom: 10px; }
  main { padding: 24px 16px 60px; }
  .top .meta { display: none; }
}
@media print {
  :root { --bg: #fff; --surface: #fff; --surface-2: #f2f2f2; --line: #ddd; --text: #111; --text-2: #333; --muted: #666; }
  .top, nav.toc { display: none; } .wrap { display: block; } main { padding: 0; }
  pre.code { background: #f6f6f6; } pre.code code { color: #111; } code { color: #8a0f35; }
  a { color: #8a0f35; } h2 { break-after: avoid; } .table, pre { break-inside: avoid; }
}
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
</style>
</head>
<body>
<header class="top">__LOGO__<span>Gallery façade lighting</span><span class="meta">Technical documentation</span></header>
<div class="wrap">
  <nav class="toc" aria-label="Contents"><details id="tocBox" open><summary>Contents</summary>__TOC__</details></nav>
  <main>
    <div class="hero"><div class="rule"></div><h1>__TITLE__</h1>__LEAD__</div>
    __BODY__
    <p class="foot">Generated from docs/TECHNICAL.md by tools/build_docs.py. Edit the Markdown, then rebuild.</p>
  </main>
</div>
<script>
// Contents start collapsed on narrow screens and close after a pick there.
(function () {
  var box = document.getElementById("tocBox"), narrow = window.matchMedia("(max-width: 900px)");
  if (narrow.matches) box.open = false;
  // Widening the window always reopens them (on wide screens they can't be closed).
  narrow.addEventListener("change", function () { box.open = !narrow.matches; });
  box.addEventListener("click", function (e) { if (e.target.tagName === "A" && narrow.matches) box.open = false; });
  box.querySelector("summary").addEventListener("click", function (e) { if (!narrow.matches) e.preventDefault(); });
})();
// Highlight the section in view in the contents.
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll("nav.toc a"));
  var byId = {}; links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
  var heads = Array.prototype.slice.call(document.querySelectorAll("main h2, main h3"));
  function update() {
    var cur = heads[0];
    for (var i = 0; i < heads.length; i++) { if (heads[i].getBoundingClientRect().top < 120) cur = heads[i]; }
    links.forEach(function (a) { a.classList.remove("on"); });
    if (cur && byId[cur.id]) byId[cur.id].classList.add("on");
  }
  document.addEventListener("scroll", update, { passive: true }); update();
})();
</script>
</body>
</html>
"""


def main():
    md = SRC.read_text(encoding="utf-8")
    title, toc, body = convert(md)
    # First paragraph becomes the hero lead.
    lead = ""
    m = re.match(r"\s*(<p>.*?</p>)", body, re.S)
    if m:
        lead, body = m.group(1), body[m.end():]
    logo = LOGO.read_text(encoding="utf-8")
    logo = re.sub(r"^<svg ", '<svg role="img" aria-label="HOTA Home of the Arts" ', logo.strip(), count=1)
    toc_html = "".join(f'<a class="l{lvl}" href="#{sid}">{inline(t)}</a>' for lvl, t, sid in toc)
    page = (TEMPLATE.replace("__LOGO__", logo).replace("__TOC__", toc_html)
            .replace("__TITLE__", html.escape(title)).replace("__LEAD__", lead).replace("__BODY__", body))
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(toc)} sections)")


if __name__ == "__main__":
    main()
