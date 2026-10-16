// 3D view (prototype): the four traced elevations folded into a rough
// massing using the structural grid (static/building3d.json, made by
// tools/build_3d.py), with every LED placed on it and lit from the same
// live preview the 2D drawing uses. three.js is vendored and loaded only
// when the view is first opened.
//
// Axes: plan X east / Y north / Z up (mm) -> three.js x = X, y = Z, z = -Y,
// in metres.
(function (global) {
  "use strict";

  const BASE = new URL("vendor/three/", document.currentScript && document.currentScript.src || location.href).href;
  let THREE = null, OrbitControls = null;
  async function loadThree() {
    if (THREE) return;
    THREE = await import(BASE + "three.module.min.js");
    ({ OrbitControls } = await import(BASE + "OrbitControls.js"));
  }

  const OUT_LINE = 12;   // mm in front of the wall for linework (no z-fighting)
  const OUT_LED = 70;    // mm in front of the wall for lights
  const TAPE_STEP = 140; // mm between glow samples along a strip

  // ---------------------------------------------------------------------
  // Geometry helpers
  // ---------------------------------------------------------------------
  function makeMapper(elev, b3d) {
    const k = b3d.px_per_mm;
    const walls = elev.elevations.map(e => ({ e, f: b3d.faces[e.name] })).filter(w => w.f);
    const wallAt = wx => walls.find(w => wx >= w.e.x0 - 200 && wx <= w.e.x1 + 200) || null;
    // World (unfolded drawing) point -> three.js position, pushed `out` mm off the wall.
    function toV(w, wx, wy, out) {
      const sx = wx - w.e.sheet_offset[0];
      const along = w.f.a * sx + w.f.b;
      const z = (b3d.ground_y - wy) / k;
      let X, Y;
      if (w.f.along === "y") { X = w.f.plane.x; Y = along; } else { X = along; Y = w.f.plane.y; }
      X += w.f.normal[0] * out; Y += w.f.normal[1] * out;
      return [X / 1000, z / 1000, -Y / 1000];
    }
    return { walls, wallAt, toV, k };
  }

  function buildBuilding(scene, elev, b3d, M) {
    const group = new THREE.Group();
    const wallMat = new THREE.MeshBasicMaterial({ color: 0x161515, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
    const lineMats = {
      outline: new THREE.LineBasicMaterial({ color: 0xf5f5f5, transparent: true, opacity: 0.85 }),
      bands: new THREE.LineBasicMaterial({ color: 0xf5f5f5, transparent: true, opacity: 0.38 }),
      grid: new THREE.LineBasicMaterial({ color: 0xf5f5f5, transparent: true, opacity: 0.07 }),
    };
    for (const w of M.walls) {
      // Solid wall from the outline, so lights on the far side are hidden.
      for (const poly of w.e.outline) {
        if (poly.length < 3) continue;
        const shape = new THREE.Shape(poly.map(([x, y]) => new THREE.Vector2(x, y)));
        const g = new THREE.ShapeGeometry(shape);
        const pos = g.attributes.position;
        for (let i = 0; i < pos.count; i++) {
          const v = M.toV(w, pos.getX(i), pos.getY(i), 0);
          pos.setXYZ(i, v[0], v[1], v[2]);
        }
        g.computeBoundingSphere();
        group.add(new THREE.Mesh(g, wallMat));
      }
      // Linework, just in front of the wall.
      for (const [key, closed] of [["outline", true], ["bands", true], ["grid", false]]) {
        const pts = [];
        for (const poly of w.e[key]) {
          const n = poly.length;
          for (let i = 0; i < (closed ? n : n - 1); i++) {
            const a = poly[i], b = poly[(i + 1) % n];
            pts.push(...M.toV(w, a[0], a[1], OUT_LINE), ...M.toV(w, b[0], b[1], OUT_LINE));
          }
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
        group.add(new THREE.LineSegments(g, lineMats[key]));
      }
    }
    // Roof cap at the roof level, over the derived footprint.
    const fp = b3d.footprint;
    const roofLevel = (elev.levels || []).find(l => /roof/i.test(l.name));
    const roofZ = roofLevel ? (b3d.ground_y - roofLevel.y) / M.k / 1000 : 31.3;
    const roof = new THREE.Mesh(new THREE.PlaneGeometry((fp.east - fp.west) / 1000, (fp.north - fp.south) / 1000), new THREE.MeshBasicMaterial({ color: 0x1c1a1a, side: THREE.DoubleSide }));
    roof.rotation.x = -Math.PI / 2;
    roof.position.set((fp.east + fp.west) / 2000, roofZ, -(fp.north + fp.south) / 2000);
    group.add(roof);
    // Ground: dark plane and a faint 5 m grid.
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(220, 220), new THREE.MeshBasicMaterial({ color: 0x0b0a0a }));
    ground.rotation.x = -Math.PI / 2; ground.position.set((fp.east + fp.west) / 2000, -4.5, -(fp.north + fp.south) / 2000);
    group.add(ground);
    const grid = new THREE.GridHelper(220, 44, 0x2a2828, 0x1d1c1c);
    grid.position.copy(ground.position); grid.position.y += 0.01;
    group.add(grid);
    scene.add(group);
    return { group, roofZ };
  }

  // Outdoor stage (VOS): every fixture's real 3D position (static/vos3d.json,
  // tools/import_vos.py) on a model of the stage built from the architect's
  // as-built drawings (static/vos-building.json + vos-wall.png,
  // tools/build_vos_model.py): the Voronoi wall folded onto its plan, the
  // canopy ring and glazing grid, its two legs, the lawn and stage floor.
  // Without the building file it falls back to just the lights.
  function stageKey(f) { return `${f.universe}:${f.address}`; }
  const lerpTable = (t, x) => {
    if (x <= t[0][0]) return t[0][1];
    for (let i = 1; i < t.length; i++) if (t[i][0] >= x) {
      const [x0, y0] = t[i - 1], [x1, y1] = t[i];
      return y0 + (y1 - y0) * (x - x0) / ((x1 - x0) || 1);
    }
    return t[t.length - 1][1];
  };
  function hull(pts) { // convex hull, monotone chain
    pts = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const p of pts) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
    for (const p of pts.reverse()) { while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }
  function buildStage(scene, v3d, vb) {
    const group = new THREE.Group();
    const toV = p => [p[0] / 1000, p[2] / 1000, -p[1] / 1000];
    const groundZ = vb ? vb.ground_z : 0;
    let minX = v3d.extent.min_x, maxX = v3d.extent.max_x, maxZ = v3d.extent.max_z, minY = -6000;

    if (vb) {
      // Wall: one strip of quads along the unfolded wall, folded at the two
      // corners, textured with the elevation (alpha cuts the top edge).
      const w = vb.wall, fold = w.fold_u, a = w.wing_angle_deg * Math.PI / 180;
      const plan = u => Math.abs(u) <= fold ? [u, 0] : [Math.sign(u) * (fold + (Math.abs(u) - fold) * Math.cos(a)), -(Math.abs(u) - fold) * Math.sin(a)];
      const us = [-fold, fold];
      for (let u = w.u0; u < w.u1; u += 400) us.push(u);
      us.push(w.u1);
      us.sort((p, q) => p - q);
      const pos = [], uv = [], idx = [];
      us.forEach((u, i) => {
        const [X, Y] = plan(u), t = (u - w.u0) / (w.u1 - w.u0);
        pos.push(...toV([X, Y, w.v_bottom]), ...toV([X, Y, w.v_top]));
        uv.push(t, 0, t, 1);
        if (i) idx.push(2 * i - 2, 2 * i, 2 * i + 1, 2 * i - 2, 2 * i + 1, 2 * i - 1);
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      g.setIndex(idx);
      const tex = new THREE.TextureLoader().load(BASE.replace(/vendor\/three\/$/, "") + w.texture);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = 8;
      // Dusk: the stone is dim, so the lights are what you see.
      group.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, color: 0x5c5955, alphaTest: 0.5, side: THREE.DoubleSide })));
      for (const [u, v] of [[w.u0, 0], [w.u1, 0]]) minX = Math.min(minX, plan(u)[0]), maxX = Math.max(maxX, plan(u)[0]);
      minY = Math.min(minY, plan(w.u0)[1], plan(w.u1)[1]);
      maxZ = Math.max(maxZ, ...w.top.map(q => q[1]));

      // Canopy: glazing as a faint surface over its plan outline, at the
      // height the section and elevation give; then the grid and ring.
      const c = vb.canopy;
      const zAt = (X, Y) => lerpTable(c.profile, Y) - lerpTable(c.sag, X);
      const span = c.sag[c.sag.length - 1][0];
      const outline = hull(c.lines.flat().filter(q => Math.abs(q[0]) <= span).map(q => [q[0], q[1]]));
      const shape = new THREE.Shape(outline.map(([X, Y]) => new THREE.Vector2(X, Y)));
      const sg = new THREE.ShapeGeometry(shape, 24);
      const sp = sg.attributes.position;
      for (let i = 0; i < sp.count; i++) {
        const X = sp.getX(i), Y = sp.getY(i), v = toV([X, Y, zAt(X, Y)]);
        sp.setXYZ(i, v[0], v[1], v[2]);
      }
      sg.computeBoundingSphere();
      group.add(new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ color: 0x8fa3b8, transparent: true, opacity: 0.08, side: THREE.DoubleSide, depthWrite: false })));
      const seg = [];
      for (const pl of c.lines) for (let i = 0; i < pl.length - 1; i++) seg.push(...toV(pl[i]), ...toV(pl[i + 1]));
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.Float32BufferAttribute(seg, 3));
      group.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: 0xc9c6c0, transparent: true, opacity: 0.6 })));
      // The ring (a big steel beam in the photos) and the two legs.
      const metal = new THREE.MeshBasicMaterial({ color: 0x5e5c58 });
      const ring = outline.map(([X, Y]) => new THREE.Vector3(...toV([X, Y, zAt(X, Y)])));
      group.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(ring, true), 400, 0.4, 8, true), metal));
      for (const leg of c.legs) {
        const curve = new THREE.CatmullRomCurve3(leg.map(q => new THREE.Vector3(...toV(q))));
        group.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 64, 0.55, 10, false), metal));
      }
      minY = Math.min(minY, ...outline.map(q => q[1]));
      maxZ = Math.max(maxZ, ...c.lines.flat().map(q => q[2]));

      // Stage floor behind the opening, at stage level.
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(26, 14), new THREE.MeshBasicMaterial({ color: 0x141312 }));
      floor.rotation.x = -Math.PI / 2; floor.position.set(0, 0.01, -7);
      group.add(floor);
    } else {
      const plan = new THREE.BufferGeometry();
      plan.setAttribute("position", new THREE.Float32BufferAttribute(v3d.plan.flatMap(([x, y]) => toV([x, y, 10])), 3));
      group.add(new THREE.Line(plan, new THREE.LineBasicMaterial({ color: 0xf5f5f5, transparent: true, opacity: 0.35 })));
    }

    // Lawn and a faint 5 m grid, at ground level in front of the stage.
    const cx = (minX + maxX) / 2000, cz = -minY / 2000;
    const gy = groundZ / 1000;
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(220, 220), new THREE.MeshBasicMaterial({ color: vb ? 0x0d120c : 0x0b0a0a }));
    ground.rotation.x = -Math.PI / 2; ground.position.set(cx, gy - 0.02, cz);
    group.add(ground);
    const grid = new THREE.GridHelper(220, 44, 0x232a21, 0x161b15);
    grid.position.set(cx, gy, cz);
    group.add(grid);
    scene.add(group);
    const height = maxZ / 1000;
    return { toV, centre: new THREE.Vector3(cx, height * 0.4, cz), radius: 0.5 * Math.hypot((maxX - minX) / 1000, height, -minY / 1000) };
  }

  // Main building (HOTA Central side): the street-view sketch
  // (building-scene.js) stood up as simple blocks - the wall, the roof
  // behind it and the fly tower with its HOTA sign and mural side - with
  // the billboard, the HOTA Central sign and a bar fitting per fixture,
  // each washing the wall below it in its live colour. Metres: sketch x,
  // up = -sketch y, the street at +z.
  function canvasTexture(w, h, paint) {
    const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
    paint(cv.getContext("2d"), w, h);
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    return t;
  }
  function buildStreet(scene, S) {
    const group = new THREE.Group();
    const W = S.WALL, wallW = W.x1 - W.x0, wallH = -W.top;
    const edgeMat = new THREE.LineBasicMaterial({ color: 0xf5f5f5, transparent: true, opacity: 0.55 });
    const block = (x0, x1, y0, y1, z0, z1, color) => {
      const g = new THREE.BoxGeometry(x1 - x0, y1 - y0, z1 - z0);
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color }));
      m.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(g), edgeMat);
      e.position.copy(m.position);
      group.add(m, e);
      return m;
    };
    const face = (tex, x0, x1, y0, y1, z, rotY = 0, cx = null) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), new THREE.MeshBasicMaterial({ map: tex }));
      m.position.set(cx != null ? cx : (x0 + x1) / 2, (y0 + y1) / 2, z);
      m.rotation.y = rotY;
      group.add(m);
      return m;
    };

    // Wall block, roof behind it, fly tower set back.
    const DEPTH = 26, TOWER = { x0: 8.8, x1: 22.1, z0: -22, z1: -7, h: 15.6 }, ROOF_H = 10.6;
    block(W.x0, W.x1, 0, wallH, -DEPTH, 0, 0x232120);
    block(W.x0 + 0.3, W.x1 - 0.3, wallH, ROOF_H - 0.4, -DEPTH, -1.2, 0x1c1b1a);
    block(TOWER.x0, TOWER.x1, 0, TOWER.h, TOWER.z0, TOWER.z1, 0x2a2827);

    // Wall front: parapet fascia, a dark bay per bar, faint joints.
    const PX = 64;
    face(canvasTexture(Math.round(wallW * PX), Math.round(wallH * PX), (c, w, h) => {
      const X = x => (x - W.x0) * PX, Y = y => (y - W.top) * PX;
      c.fillStyle = "#2b2927"; c.fillRect(0, 0, w, h);
      c.strokeStyle = "rgba(245,245,245,.35)"; c.lineWidth = 2;
      c.beginPath(); c.moveTo(0, Y(W.fascia)); c.lineTo(w, Y(W.fascia)); c.stroke();
      for (const x of S.BAYS) {
        c.fillStyle = "#1c1b1a"; c.fillRect(X(x - S.BAY_W / 2), Y(W.fascia), S.BAY_W * PX, h - Y(W.fascia));
        c.strokeRect(X(x - S.BAY_W / 2), Y(W.fascia), S.BAY_W * PX, h - Y(W.fascia));
      }
      c.strokeStyle = "rgba(245,245,245,.08)"; c.lineWidth = 1.5;
      for (const x of S.BAYS) for (let y = -6.8; y < 0; y += 1.4) { c.beginPath(); c.moveTo(X(x - S.BAY_W / 2), Y(y)); c.lineTo(X(x + S.BAY_W / 2), Y(y)); c.stroke(); }
    }), W.x0, W.x1, 0, wallH, 0.01);

    // Tower front: the HOTA sign above the roof.
    const logo = S.logo;
    const tw = TOWER.x1 - TOWER.x0, th = TOWER.h - ROOF_H;
    face(canvasTexture(Math.round(tw * PX), Math.round(th * PX), (c, w, h) => {
      c.fillStyle = "#2a2827"; c.fillRect(0, 0, w, h);
      if (logo) { const lw = w * 0.78, lh = lw * (logo.naturalHeight / logo.naturalWidth || 51 / 343); c.globalAlpha = 0.8; c.drawImage(logo, (w - lw) / 2, h * 0.45 - lh / 2, lw, lh); }
    }), TOWER.x0, TOWER.x1, ROOF_H, TOWER.h, TOWER.z1 + 0.01);
    // Tower west side: the mural, from the sketch's colour blocks.
    const td = TOWER.z1 - TOWER.z0;
    face(canvasTexture(Math.round(td * PX), Math.round(th * PX), (c, w, h) => {
      c.fillStyle = "#2a2827"; c.fillRect(0, 0, w, h);
      // Sketch x 4.0 (back) .. 8.8 (front corner), y -15.6 (top) .. -10.3 (roof).
      const U = x => (x - 4.0) / 4.8 * w, V = y => (y + 15.6) / 5.3 * h;
      for (const [rgb, pts] of S.MURAL) {
        c.beginPath(); pts.forEach(([x, y], i) => (i ? c.lineTo(U(x), V(y)) : c.moveTo(U(x), V(y)))); c.closePath();
        c.fillStyle = `rgba(${rgb},.55)`; c.fill();
      }
    }), TOWER.z0, TOWER.z1, ROOF_H, TOWER.h, 0, -Math.PI / 2, TOWER.x0 - 0.01).position.z = (TOWER.z0 + TOWER.z1) / 2;

    // Billboard (HOTA logo) and the HOTA Central blade sign.
    const B = S.BILLBOARD, bw = B.x1 - B.x0, bh = B.y1 - B.y0;
    face(canvasTexture(Math.round(bw * PX), Math.round(bh * PX), (c, w, h) => {
      c.fillStyle = "#0f0e0e"; c.fillRect(0, 0, w, h);
      c.strokeStyle = "rgba(245,245,245,.8)"; c.lineWidth = 4; c.strokeRect(2, 2, w - 4, h - 4);
      if (logo) { const lw = w * 0.62, lh = lw * (logo.naturalHeight / logo.naturalWidth || 51 / 343); c.globalAlpha = 0.75; c.drawImage(logo, (w - lw) / 2, (h - lh) / 2, lw, lh); }
    }), B.x0, B.x1, -B.y1, -B.y0, 0.12);
    const L = S.BLADE;
    block(L.x0, L.x1, -L.y1, -L.y0, 0, 0.3, 0x0f0e0e);
    face(canvasTexture(Math.round((L.x1 - L.x0) * PX), Math.round((L.y1 - L.y0) * PX), (c, w, h) => {
      c.fillStyle = "#0f0e0e"; c.fillRect(0, 0, w, h);
      c.save(); c.translate(w / 2, 0.35 * PX); c.rotate(Math.PI / 2);
      c.fillStyle = "rgba(245,245,245,.85)"; c.textBaseline = "middle";
      const fs = w * 0.42;
      c.font = `700 ${fs}px Rubik, sans-serif`; c.fillText("HOTA", 0, 0);
      const hw = c.measureText("HOTA ").width;
      c.font = `400 ${fs}px Rubik, sans-serif`; c.fillText("central", hw, 0);
      c.restore();
    }), L.x0, L.x1, -L.y1, -L.y0, 0.31);

    // Street and a faint 5 m grid.
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(220, 220), new THREE.MeshBasicMaterial({ color: 0x0b0a0a }));
    ground.rotation.x = -Math.PI / 2; ground.position.set(wallW / 2, -0.02, 0);
    const grid = new THREE.GridHelper(220, 44, 0x1d1c1b, 0x151414);
    grid.position.set(wallW / 2, 0, 0);
    group.add(ground, grid);

    // Bars and their washes, coloured each frame by paint().
    const washTex = canvasTexture(128, 256, (c, w, h) => {
      const g = c.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, "rgba(255,255,255,.85)"); g.addColorStop(0.35, "rgba(255,255,255,.4)"); g.addColorStop(1, "rgba(255,255,255,.04)");
      c.fillStyle = g;
      c.beginPath(); c.moveTo(w * 0.31, 0); c.lineTo(w * 0.69, 0); c.lineTo(w, h); c.lineTo(0, h); c.closePath(); c.fill();
    });
    const barGeo = new THREE.BoxGeometry(S.BAR_LEN, S.BAR_H, 0.22);
    let fittings = [];
    function setFixtures(list) { // [{fi, x, y}] in sketch metres
      for (const f of fittings) { group.remove(f.bar, f.wash); f.bar.material.dispose(); f.wash.material.dispose(); f.wash.geometry.dispose(); }
      fittings = list.map(({ fi, x, y }) => {
        const top = -y, wh = top;
        const bar = new THREE.Mesh(barGeo, new THREE.MeshBasicMaterial({ color: 0x3f3c3c }));
        bar.position.set(x, top, 0.2);
        const wash = new THREE.Mesh(new THREE.PlaneGeometry(2.9, wh), new THREE.MeshBasicMaterial({
          map: washTex, color: 0x000000, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
        }));
        wash.position.set(x, top / 2, 0.05);
        wash.renderOrder = 2; // after the (opaque) wall, from any angle
        group.add(bar, wash);
        return { fi, bar, wash };
      });
    }
    function paint(colourOf) { // colourOf(fi) -> [r,g,b] 0-255 or null
      for (const f of fittings) {
        const c = colourOf(f.fi);
        const lit = c && c[0] + c[1] + c[2] > 24;
        if (lit) f.bar.material.color.setRGB(Math.min(255, c[0]) / 255, Math.min(255, c[1]) / 255, Math.min(255, c[2]) / 255, THREE.SRGBColorSpace);
        else f.bar.material.color.setHex(0x3f3c3c);
        if (lit) f.wash.material.color.setRGB(Math.min(255, c[0]) / 255, Math.min(255, c[1]) / 255, Math.min(255, c[2]) / 255, THREE.SRGBColorSpace);
        else f.wash.material.color.setHex(0x000000);
      }
    }
    scene.add(group);
    return { setFixtures, paint, centre: new THREE.Vector3(wallW / 2, 6, -4), radius: 0.5 * Math.hypot(wallW, TOWER.h, DEPTH * 0.5) };
  }

  // Glow sprites: soft core + wide halo, additive, depth-tested (the
  // building hides lights round the back) but not depth-writing.
  function glowMaterial(pixelRatio) {
    return new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uScale: { value: 1 }, uPR: { value: pixelRatio } },
      vertexShader: `
        attribute vec3 color; attribute float size; varying vec3 vColor;
        uniform float uScale; uniform float uPR;
        void main() {
          vColor = color;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = clamp(size * uScale * uPR / -mv.z, 1.5 * uPR, 90.0 * uPR);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        varying vec3 vColor;
        void main() {
          float d = length(gl_PointCoord - 0.5) * 2.0;
          if (d > 1.0) discard;
          float core = smoothstep(0.32, 0.0, d);
          float halo = exp(-d * d * 4.0) * 0.32;
          float a = core + halo;
          gl_FragColor = vec4(vColor * a, a);
        }`,
    });
  }

  // ---------------------------------------------------------------------
  // View
  //   src: { elev, b3d, getLayout(), getFixtures(), getPreview() }
  // ---------------------------------------------------------------------
  async function create(container, src) {
    await loadThree();
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
    renderer.setClearColor(0x0f0e0e, 1);
    container.append(renderer.domElement);
    renderer.domElement.className = "view3d-canvas";
    renderer.domElement.setAttribute("aria-label", "3D model of the gallery with live lights. Drag to orbit, scroll to zoom.");

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0x0f0e0e, 90, 220);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 600);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI * 0.495; // stay above ground
    controls.minDistance = 8; controls.maxDistance = 220;

    const street = src.street ? buildStreet(scene, src.street) : null;
    const stage = !street && src.v3d ? buildStage(scene, src.v3d, src.vb) : null;
    let M = null, roofZ = 0, fp = null, centre;
    if (street) {
      centre = street.centre;
      renderer.domElement.setAttribute("aria-label", "3D view of the HOTA Central wall with live lights. Drag to swing left and right, scroll to zoom.");
      // Keep to the street side: swing left and right of the wall, not round the back.
      controls.minAzimuthAngle = -1.25; controls.maxAzimuthAngle = 1.25;
      controls.maxDistance = 120;
    } else if (stage) {
      centre = stage.centre;
      renderer.domElement.setAttribute("aria-label", "3D view of the outdoor stage facade with live lights. Drag to orbit, scroll to zoom.");
    } else {
      M = makeMapper(src.elev, src.b3d);
      ({ roofZ } = buildBuilding(scene, src.elev, src.b3d, M));
      fp = src.b3d.footprint;
      centre = new THREE.Vector3((fp.east + fp.west) / 2000, roofZ * 0.45, -(fp.north + fp.south) / 2000);
    }

    // Light samples: every LED, plus extra samples along strips so tape
    // reads as continuous. Each sample blends two LEDs of one fixture.
    let samples = [], points = null;
    const mat = glowMaterial(renderer.getPixelRatio());
    function buildLights() {
      if (points) { scene.remove(points); points.geometry.dispose(); }
      const layout = src.getLayout(), fixtures = src.getFixtures();
      samples = [];
      const pos = [], size = [];
      const bars = [];
      fixtures.forEach((f, fi) => {
        if (street) {
          // A bar fitting and its wash (buildStreet), plus a glow at the bar.
          const p = layout && layout.fixtures[`${f.universe}:${f.address}`];
          if (!p) return;
          bars.push({ fi, x: p[0][0], y: p[0][1] });
          samples.push({ fi, i0: 0, i1: 0, t: 0 }); pos.push(p[0][0], -p[0][1], 0.35); size.push(2400);
          return;
        }
        if (stage) {
          // One pixel per fixture; dots drawn bigger than strip pixels.
          const p = src.v3d.fixtures[stageKey(f)];
          if (!p) return;
          const dot = /dots$/i.test(f.zone || "");
          for (let i = 0; i < f.led_count; i++) {
            samples.push({ fi, i0: i, i1: i, t: 0 }); pos.push(...stage.toV(p)); size.push(dot ? 2600 : 900);
            // Spill: a big faint glow on the stone round each dot and every
            // third strip pixel, so a look reads as light on the wall.
            if (src.vb && (dot || fi % 3 === 0)) {
              const q = stage.toV(p);
              samples.push({ fi, i0: i, i1: i, t: 0, k: dot ? 0.22 : 0.12, spill: true });
              pos.push(q[0], q[1] - (dot ? 0 : 0.6), q[2] + 0.05); size.push(dot ? 16000 : 11000);
            }
          }
          return;
        }
        const p = layout && layout.fixtures[`${f.universe}:${f.address}`];
        if (!p) return;
        const w = M.wallAt((p[0][0] + p[1][0]) / 2);
        if (!w) return;
        const n = f.led_count, dish = f.led_type === "RGB";
        const ledAt = i => { const t = n > 1 ? i / (n - 1) : 0; return [p[0][0] + (p[1][0] - p[0][0]) * t, p[0][1] + (p[1][1] - p[0][1]) * t]; };
        for (let i = 0; i < n; i++) {
          const a = ledAt(i);
          samples.push({ fi, i0: i, i1: i, t: 0 }); pos.push(...M.toV(w, a[0], a[1], OUT_LED)); size.push(dish ? 2600 : 1300);
          if (i < n - 1 && !dish) {
            const b = ledAt(i + 1);
            const lenMM = Math.hypot(b[0] - a[0], b[1] - a[1]) / M.k;
            const extra = Math.min(12, Math.floor(lenMM / TAPE_STEP));
            for (let s = 1; s <= extra; s++) {
              const t = s / (extra + 1);
              samples.push({ fi, i0: i, i1: i + 1, t });
              pos.push(...M.toV(w, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, OUT_LED)); size.push(1100);
            }
          }
        }
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(samples.length * 3), 3));
      g.setAttribute("size", new THREE.Float32BufferAttribute(size, 1));
      points = new THREE.Points(g, mat);
      points.frustumCulled = false;
      scene.add(points);
      if (street) street.setFixtures(bars);
    }
    buildLights();

    function paintLights() {
      const prev = src.getPreview();
      const col = points.geometry.attributes.color;
      const arr = col.array;
      for (let s = 0; s < samples.length; s++) {
        const S = samples[s], P = prev && prev[S.fi];
        let r = 0, g = 0, b = 0;
        if (P) {
          const A = P.colors[S.i0] || [0, 0, 0, 0], B = P.colors[S.i1] || A, t = S.t;
          const w = A[3] + (B[3] - A[3]) * t;
          r = A[0] + (B[0] - A[0]) * t + w; g = A[1] + (B[1] - A[1]) * t + w * 0.96; b = A[2] + (B[2] - A[2]) * t + w * 0.88;
        }
        if (S.k) { r *= S.k; g *= S.k; b *= S.k; }
        // Unlit LEDs stay faintly visible as grey fittings (spill stays dark).
        else if (r + g + b < 24) { r = g = b = 34; }
        arr[s * 3] = Math.min(255, r) / 255; arr[s * 3 + 1] = Math.min(255, g) / 255; arr[s * 3 + 2] = Math.min(255, b) / 255;
      }
      col.needsUpdate = true;
      if (street) street.paint(fi => {
        const c = prev && prev[fi] && prev[fi].colors[0];
        return c ? [c[0] + c[3], c[1] + c[3] * 0.96, c[2] + c[3] * 0.88] : null;
      });
    }

    // Camera presets, named from where you stand.
    // Gallery: [east, north] unit direction. Stage: [across, depth] - the
    // audience is at -Y (three.js +z), so "Front" looks straight at the wall.
    const VIEWS = street
      ? {
          // Standing across the street, then off to either end of the wall.
          "Street": { pos: [16, 1.7, 40], target: [16, 6.5, 0] },
          "From the left": { pos: [-22, 1.8, 26], target: [7, 6, -4] },
          "From the right": { pos: [52, 1.8, 26], target: [25, 6, -4] },
        }
      : stage
      ? {
          // Eye level out on the lawn, like the photos; then the overviews.
          "Audience": { pos: [0, 1.7, 54], target: [0, 7.5, 0] },
          "Lawn left": { pos: [-34, 1.8, 38], target: [0, 6.5, 2] },
          "Lawn right": { pos: [34, 1.8, 38], target: [0, 6.5, 2] },
          "Front": [0, -1], "Above": null,
        }
      : { "North-east": [1, 1], "South-east": [1, -1], "South-west": [-1, -1], "North-west": [-1, 1], "Above": null };
    // Distance that fits the whole building (bounding sphere) in the
    // narrower of the two fields of view, so presets work at any window shape.
    const radius = street ? street.radius : stage ? stage.radius : 0.5 * Math.hypot((fp.east - fp.west) / 1000, (fp.north - fp.south) / 1000, roofZ + 4.5);
    function fitDistance() {
      const v = (camera.fov * Math.PI) / 180, hf = 2 * Math.atan(Math.tan(v / 2) * camera.aspect);
      return (radius / Math.sin(Math.min(v, hf) / 2)) * 1.08;
    }
    let lastView = Object.keys(VIEWS)[0];
    function setView(name, instant) {
      lastView = name;
      const d = fitDistance(), v = VIEWS[name];
      const aim = v && v.target ? new THREE.Vector3(...v.target) : centre;
      const to = v && v.pos ? new THREE.Vector3(...v.pos)
        : v ? new THREE.Vector3(centre.x + v[0] * d * 0.69, centre.y + d * 0.2, centre.z - v[1] * d * 0.69)
        : new THREE.Vector3(centre.x, centre.y + d, centre.z + 0.01); // north up
      if (instant) { camera.position.copy(to); controls.target.copy(aim); controls.update(); return; }
      const from = camera.position.clone(), aimFrom = controls.target.clone(), t0 = performance.now();
      (function step() {
        const k = Math.min(1, (performance.now() - t0) / 700), e = 1 - Math.pow(1 - k, 3);
        camera.position.lerpVectors(from, to, e); controls.target.lerpVectors(aimFrom, aim, e); controls.update();
        if (k < 1) requestAnimationFrame(step);
      })();
    }
    let sized = false;
    function resize() {
      const r = container.getBoundingClientRect();
      if (!r.width || !r.height) return;
      renderer.setSize(r.width, r.height, false);
      camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
      if (!sized) { sized = true; setView(lastView, true); }
      mat.uniforms.uScale.value = r.height * 0.5 / Math.tan((camera.fov * Math.PI) / 360) / 1000;
    }
    const ro = new ResizeObserver(resize); ro.observe(container); resize();

    let active = false, raf = 0;
    function loop() {
      if (!active) return;
      controls.update();
      paintLights();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(loop);
    }
    setView(lastView, true);
    return {
      views: Object.keys(VIEWS),
      setView,
      rebuild: buildLights,
      setActive(on) {
        active = on;
        cancelAnimationFrame(raf);
        if (on) { sized = false; resize(); loop(); }
      },
      dispose() { active = false; cancelAnimationFrame(raf); ro.disconnect(); controls.dispose(); renderer.dispose(); renderer.domElement.remove(); },
    };
  }

  global.HotaView3D = { create };
})(window);
