#!/usr/bin/env python3
"""Generate a fully standalone, offline layout editor: one HTML file with
the current fixture list baked in as inline JSON, a canvas to drag dots
around on, and a "Download CSV" button - no server, no network, no
connection to the live Pi at all. Deliberately separate from the live
staff-facing UI (index.html): dragging fixtures is a one-time layout
correction task the user does at their own pace, not a control that
should be one accidental mouse-slip away during normal operation.

The downloaded CSV matches import_layout_csv.py's expected columns
exactly, so whatever comes back just drops straight into that importer.

Usage: python tools/build_layout_editor.py [output path, default data/layout_editor.html]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HOTA Gallery - Layout Editor (offline)</title>
<style>
  body { font-family: Arial, Helvetica, sans-serif; background: #111; color: #eee; margin: 0; display: flex; }
  #canvasWrap { flex: 1; display: flex; align-items: center; justify-content: center; padding: 16px; }
  canvas { background: #000; border: 1px solid #333; cursor: grab; }
  #panel { width: 320px; padding: 16px; background: #1a1a1a; border-left: 1px solid #333; }
  h1 { font-size: 16px; margin: 0 0 4px; }
  p.hint { font-size: 12px; color: #999; line-height: 1.5; }
  button { background: #333; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 10px 12px; cursor: pointer; width: 100%; margin: 12px 0 6px; font-size: 14px; }
  button:hover { background: #444; }
  #status { font-size: 12px; color: #6c6; margin-top: 10px; min-height: 18px; }
  #selected { font-size: 12px; color: #e0c84a; margin-top: 10px; }
</style>
</head>
<body>

<div id="canvasWrap"><canvas id="layout" width="900" height="600"></canvas></div>

<div id="panel">
  <h1>Layout Editor (offline)</h1>
  <p class="hint">
    Every dot is its own fixture - click to select/deselect one, or drag on
    empty space to rubber-band select several at once (adds to whatever's
    already selected). Drag any selected dot to move the whole selection
    together - that's how to grab a whole strip: rubber-band around its
    dots first, then drag. This file works completely offline and never
    talks to the Pi - when you're happy, click Download and send the CSV
    back.
  </p>
  <button id="btnDownload">Download CSV</button>
  <button id="btnReset">Reset all positions</button>
  <button id="btnClearSelection">Clear selection</button>
  <div id="selected"></div>
  <div id="status"></div>
</div>

<script>
const FIXTURES = __FIXTURES_JSON__;
let fixtures = FIXTURES.map(fx => ({ ...fx, points: fx.points.map(p => [...p]) }));
const originalFixtures = JSON.parse(JSON.stringify(FIXTURES));

const xs = fixtures.flatMap(f => f.points.map(p => p[0]));
const ys = fixtures.flatMap(f => f.points.map(p => p[1]));
const bbox = { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };

const canvas = document.getElementById('layout');
const ctx = canvas.getContext('2d');
let dotScreenPositions = [];
let movedKeys = new Set();
let selectedKeys = new Set();

function toCanvas(x, y) {
  const pad = 30;
  const w = canvas.width - pad * 2, h = canvas.height - pad * 2;
  const sx = bbox.maxX > bbox.minX ? (x - bbox.minX) / (bbox.maxX - bbox.minX) : 0.5;
  const sy = bbox.maxY > bbox.minY ? (y - bbox.minY) / (bbox.maxY - bbox.minY) : 0.5;
  return [pad + sx * w, pad + sy * h];
}

function drawWallDividers() {
  const labels = ['wall four', 'wall three', 'wall two', 'Wall one'];
  ctx.strokeStyle = '#555';
  ctx.lineWidth = 1;
  ctx.font = '11px Arial';
  ctx.fillStyle = '#777';
  for (let i = 1; i < 4; i++) {
    const x = (canvas.width / 4) * i;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.stroke();
  }
  labels.forEach((label, i) => ctx.fillText(label, (canvas.width / 4) * i + 8, 14));
}

function draw() {
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawWallDividers();
  dotScreenPositions = [];

  // Faint line between each fixture's two geometry points first, behind
  // the dots - lets you visually trace which fixtures' segments touch
  // end-to-end to form one continuous physical strip, which is what you
  // rubber-band around to select and move that whole strip together.
  ctx.strokeStyle = '#2a2a2a';
  ctx.lineWidth = 1;
  fixtures.forEach(fx => {
    const [p0, p1] = fx.points;
    const [ax, ay] = toCanvas(p0[0], p0[1]);
    const [bx, by] = toCanvas(p1[0], p1[1]);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  });

  fixtures.forEach((fx, i) => {
    const n = fx.led_count;
    const moved = movedKeys.has(fx.key);
    const isSelected = selectedKeys.has(fx.key);
    for (let j = 0; j < n; j++) {
      const t = n === 1 ? 0 : j / (n - 1);
      const x = fx.points[0][0] + (fx.points[1][0] - fx.points[0][0]) * t;
      const y = fx.points[0][1] + (fx.points[1][1] - fx.points[0][1]) * t;
      const [cx, cy] = toCanvas(x, y);
      ctx.fillStyle = moved ? '#4a88fb' : '#888';
      ctx.beginPath();
      ctx.arc(cx, cy, moved ? 4 : 3, 0, Math.PI * 2);
      ctx.fill();
      if (isSelected) {
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, 7, 0, Math.PI * 2);
        ctx.stroke();
      }
      dotScreenPositions.push({ x: cx, y: cy, fixtureIndex: i });
    }
  });

  if (rubberBand) {
    const { x0, y0, x1, y1 } = rubberBand;
    ctx.strokeStyle = '#4a88fb';
    ctx.fillStyle = 'rgba(74, 136, 251, 0.15)';
    const rx = Math.min(x0, x1), ry = Math.min(y0, y1), rw = Math.abs(x1 - x0), rh = Math.abs(y1 - y0);
    ctx.fillRect(rx, ry, rw, rh);
    ctx.strokeRect(rx, ry, rw, rh);
  }
}

function canvasPoint(ev) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
  return [(ev.clientX - rect.left) * scaleX, (ev.clientY - rect.top) * scaleY];
}

function hitTest(mx, my) {
  let closest = null, closestDist = 20;
  for (const p of dotScreenPositions) {
    const d = Math.hypot(p.x - mx, p.y - my);
    if (d < closestDist) { closestDist = d; closest = p; }
  }
  return closest;
}

function updateSelectedLabel() {
  const n = selectedKeys.size;
  document.getElementById('selected').textContent =
    n > 0 ? `${n} fixture(s) selected (drag any one of them to move the whole selection)` : '';
}

// mousedown either starts a rubber-band (empty space) or a candidate
// drag/click on a dot - which one it ends up being (a plain toggle-select
// click, or a drag of one/many fixtures) is only decided once the mouse
// has moved far enough, same click-vs-drag threshold trick as before.
let drag = null;
let rubberBand = null;

canvas.addEventListener('mousedown', (ev) => {
  const [mx, my] = canvasPoint(ev);
  const hit = hitTest(mx, my);
  if (!hit) {
    rubberBand = { x0: mx, y0: my, x1: mx, y1: my };
    return;
  }
  const key = fixtures[hit.fixtureIndex].key;
  // Dragging a dot already in the selection moves the whole selection;
  // dragging one that isn't moves just that one fixture, leaving the rest
  // of the selection untouched (and unmoved) until a plain click updates it.
  const members = selectedKeys.has(key) ? fixtures.filter(fx => selectedKeys.has(fx.key)) : [fixtures[hit.fixtureIndex]];
  drag = {
    key, startMx: mx, startMy: my, isDrag: false,
    members,
    basePoints: members.map(fx => fx.points.map(p => [...p])),
  };
});

canvas.addEventListener('mousemove', (ev) => {
  const [mx, my] = canvasPoint(ev);
  if (rubberBand) {
    rubberBand.x1 = mx;
    rubberBand.y1 = my;
    draw();
    return;
  }
  if (!drag) return;
  if (Math.hypot(mx - drag.startMx, my - drag.startMy) > 3) drag.isDrag = true;
  if (!drag.isDrag) return;
  canvas.style.cursor = 'grabbing';
  const pad = 30;
  const w = canvas.width - pad * 2, h = canvas.height - pad * 2;
  const dataDx = ((mx - drag.startMx) / w) * (bbox.maxX - bbox.minX);
  const dataDy = ((my - drag.startMy) / h) * (bbox.maxY - bbox.minY);
  drag.members.forEach((fx, i) => {
    fx.points = drag.basePoints[i].map(p => [p[0] + dataDx, p[1] + dataDy]);
    movedKeys.add(fx.key);
  });
  draw();
});

window.addEventListener('mouseup', () => {
  if (rubberBand) {
    const rx0 = Math.min(rubberBand.x0, rubberBand.x1), rx1 = Math.max(rubberBand.x0, rubberBand.x1);
    const ry0 = Math.min(rubberBand.y0, rubberBand.y1), ry1 = Math.max(rubberBand.y0, rubberBand.y1);
    // A genuine rubber-band drag adds everything inside it to the current
    // selection; a plain click on empty space (no real movement) clears it.
    if (Math.hypot(rx1 - rx0, ry1 - ry0) > 4) {
      dotScreenPositions.forEach(p => {
        if (p.x >= rx0 && p.x <= rx1 && p.y >= ry0 && p.y <= ry1) selectedKeys.add(fixtures[p.fixtureIndex].key);
      });
    } else {
      selectedKeys = new Set();
    }
    updateSelectedLabel();
    rubberBand = null;
    draw();
    return;
  }
  if (drag) {
    if (drag.isDrag) {
      document.getElementById('status').textContent = `${movedKeys.size} fixture(s) moved so far`;
    } else {
      // Plain click, nothing moved - toggle this one fixture's selection.
      if (selectedKeys.has(drag.key)) selectedKeys.delete(drag.key); else selectedKeys.add(drag.key);
      updateSelectedLabel();
    }
  }
  drag = null;
  canvas.style.cursor = 'grab';
  draw();
});

document.getElementById('btnClearSelection').onclick = () => {
  selectedKeys = new Set();
  updateSelectedLabel();
  draw();
};

document.getElementById('btnReset').onclick = () => {
  fixtures = JSON.parse(JSON.stringify(originalFixtures)).map(fx => ({ ...fx, points: fx.points.map(p => [...p]) }));
  movedKeys.clear();
  selectedKeys = new Set();
  document.getElementById('selected').textContent = '';
  document.getElementById('status').textContent = '';
  draw();
};

document.getElementById('btnDownload').onclick = () => {
  const header = 'universe,address,name,led_type,led_count,x1,y1,x2,y2';
  const rows = fixtures.map(fx => {
    const [[x1, y1], [x2, y2]] = fx.points;
    return [fx.universe, fx.address, `"${fx.name}"`, fx.led_type, fx.led_count, x1, y1, x2, y2].join(',');
  });
  const csv = [header, ...rows].join('\\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'layout_edited.csv';
  a.click();
  URL.revokeObjectURL(url);
  document.getElementById('status').textContent = 'Downloaded layout_edited.csv - send this back.';
};

draw();
</script>
</body>
</html>
"""


def main() -> int:
    config_path = PROJECT_ROOT / "data" / "config.json"
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "layout_editor.html"

    # utf-8-sig: tolerate a BOM, same as config.py's own load_config - a
    # Windows tool (PowerShell's Out-File, notably) writes one by default.
    with open(config_path, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    fixtures = []
    for fx in cfg["fixtures"]:
        fixtures.append({
            "key": f"{fx['universe']}:{fx['address']}",
            "universe": fx["universe"],
            "address": fx["address"],
            "name": fx["name"],
            "led_type": fx["led_type"],
            "led_count": fx["led_count"],
            "points": fx["points"],
        })

    html = TEMPLATE.replace("__FIXTURES_JSON__", json.dumps(fixtures))
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path} ({len(fixtures)} fixtures embedded)")
    print("Open it directly in a browser (double-click, no server needed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
