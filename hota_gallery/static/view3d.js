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

    const M = makeMapper(src.elev, src.b3d);
    const { roofZ } = buildBuilding(scene, src.elev, src.b3d, M);
    const fp = src.b3d.footprint;
    const centre = new THREE.Vector3((fp.east + fp.west) / 2000, roofZ * 0.45, -(fp.north + fp.south) / 2000);

    // Light samples: every LED, plus extra samples along strips so tape
    // reads as continuous. Each sample blends two LEDs of one fixture.
    let samples = [], points = null;
    const mat = glowMaterial(renderer.getPixelRatio());
    function buildLights() {
      if (points) { scene.remove(points); points.geometry.dispose(); }
      const layout = src.getLayout(), fixtures = src.getFixtures();
      samples = [];
      const pos = [], size = [];
      fixtures.forEach((f, fi) => {
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
        // Unlit LEDs stay faintly visible as grey fittings.
        if (r + g + b < 24) { r = g = b = 34; }
        arr[s * 3] = Math.min(255, r) / 255; arr[s * 3 + 1] = Math.min(255, g) / 255; arr[s * 3 + 2] = Math.min(255, b) / 255;
      }
      col.needsUpdate = true;
    }

    // Camera presets, named from where you stand.
    const VIEWS = {
      "North-east": [1, 1], "South-east": [1, -1], "South-west": [-1, -1], "North-west": [-1, 1], "Above": null,
    };
    // Distance that fits the whole building (bounding sphere) in the
    // narrower of the two fields of view, so presets work at any window shape.
    const radius = 0.5 * Math.hypot((fp.east - fp.west) / 1000, (fp.north - fp.south) / 1000, roofZ + 4.5);
    function fitDistance() {
      const v = (camera.fov * Math.PI) / 180, hf = 2 * Math.atan(Math.tan(v / 2) * camera.aspect);
      return (radius / Math.sin(Math.min(v, hf) / 2)) * 1.08;
    }
    let lastView = "North-east";
    function setView(name, instant) {
      lastView = name;
      const d = fitDistance(), v = VIEWS[name];
      const to = v
        ? new THREE.Vector3(centre.x + v[0] * d * 0.69, centre.y + d * 0.2, centre.z - v[1] * d * 0.69)
        : new THREE.Vector3(centre.x, centre.y + d, centre.z + 0.01); // north up
      if (instant) { camera.position.copy(to); controls.target.copy(centre); controls.update(); return; }
      const from = camera.position.clone(), t0 = performance.now();
      (function step() {
        const k = Math.min(1, (performance.now() - t0) / 700), e = 1 - Math.pow(1 - k, 3);
        camera.position.lerpVectors(from, to, e); controls.target.lerp(centre, e); controls.update();
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
    setView("North-east", true);
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
