// De 3D-wereld: terrein, velden, water, gebouwen, robot, missie en spoor. Z-as omhoog, zoals ENU in de sim.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { color, fovDeg } from '../../theme.js';
import { building, fences, trees } from './buildings.js';
import { groundMat, ORDER, setEdges, solid, viewHeight, waterMat } from './materials.js';
import { ImplementModel } from './implement.js';
import { RobotModel } from './robot.js';
import { Atmosphere } from './sky.js';
import { bufferLine, drapePolygon, drapeStrip, flatDecal, Heightfield, horizon, ribbon, terrainMesh,
  waterBodies } from './terrain.js';
import { cropTexture, surfaceTexture, textures } from './textures.js';

// Camerastanden. 'volg' = schuin achter en boven de robot; 'rond' = rustig rondje om het erf (hoofdmenu).
export const CAMERA_MODES = ['volg', 'vrij', 'boven'];
const FOLLOW = { back: 7.5, up: 5.0, ahead: 2.0 };
const BANK_M = 0.9;                       // oeverbreedte bij water uit de kaart (BGT kent alleen de waterlijn)
// Verharding ligt in beeld bovenop de velden: een kavelpad langs de perceelgrens blijft zo altijd zichtbaar
const HARD = new Set(['track', 'paved', 'road', 'yard']);

export const QUALITY = {
  laag: { pixelRatio: 1, shadows: false, shadowSize: 1024, terrainCell: 2 },
  normaal: { pixelRatio: 1.25, shadows: true, shadowSize: 2048, terrainCell: 1 },
  hoog: { pixelRatio: 2, shadows: true, shadowSize: 4096, terrainCell: 1 },
};

export class World3D {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
    this.maxPixelRatio = 1.25;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, this.maxPixelRatio));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.8;
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(fovDeg, 1, 0.4, 24000);
    this.camera.up.set(0, 0, 1);
    this.scene.add(this.camera);
    this.atmosphere = new Atmosphere(this.renderer, this.scene);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.screenSpacePanning = false;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 2;
    this.controls.maxDistance = 2500;
    this.mode = 'volg';
    this.layers = {};
    this.hidden = new Set(['nogo']);            // lagen die uit staan (blijft zo bij een andere wereld)
    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.overlay = new THREE.Group();
    this.scene.add(this.overlay);
    this.trailGroup = new THREE.Group();
    this.scene.add(this.trailGroup);
    this.stripGroup = new THREE.Group();        // bewerkte strook van het werktuig
    this.scene.add(this.stripGroup);
    this.implement = null;
    this.robot = null;
    this.robotPose = null;
    this.trailPts = [];
    this.trailWidth = 0;
    this.onPick = null;
    this._labels = [];
    this._clock = new THREE.Clock();
    this._tmp = new THREE.Vector3();
    this._resize = new ResizeObserver(() => this.resize());
    this._resize.observe(container);
    this.renderer.domElement.addEventListener('dblclick', (e) => this._pick(e));
    this.controls.addEventListener('start', () => { if (this.mode === 'volg' || this.mode === 'rond') this.setMode('vrij'); });
    this.resize();
    const loop = () => {
      this._raf = requestAnimationFrame(loop);
      const t = this._clock.getElapsedTime();
      this._updateCamera(t);
      this.controls.update();
      this._updateLabels();
      const n = textures().waterNormal;
      n.offset.set(t * 0.012, t * 0.007);
      this.atmosphere.update(t, this.controls.target, this.camera.position.distanceTo(this.controls.target));
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    this._resize.disconnect();
    this.atmosphere.dispose();
    this.renderer.dispose();
    this.container.removeChild(this.renderer.domElement);
  }

  resize() {
    const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, this.maxPixelRatio));
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    viewHeight.value = h / (2 * Math.tan(THREE.MathUtils.degToRad(fovDeg) / 2));
  }

  // ------------------------------------------------------------------ instellingen
  setQuality(name) {
    const q = QUALITY[name] ?? QUALITY.normaal;
    this.maxPixelRatio = q.pixelRatio;
    this.atmosphere.setShadows(this._shadowsWanted ?? q.shadows, q.shadowSize);
    this.resize();
  }

  setShadows(on) { this._shadowsWanted = on; this.atmosphere.setShadows(on); }
  setHour(h) { this.atmosphere.setHour(h); }
  setClouds(on) { this.atmosphere.setClouds(on); }
  setEdges(on) { setEdges(on); }

  // ------------------------------------------------------------------ wereld opbouwen
  load(scene, terrainHeader, heights) {
    this.setAerial(null);
    this.world.traverse((o) => o.geometry?.dispose());
    this.world.clear();
    this.overlay.clear();
    this.clearTrail();
    this.clearStrip();
    this.setPoints(null);
    this.setReplay(null);
    this._labels = [];
    this.layers = {};
    this.sceneData = scene;
    const hf = new Heightfield(terrainHeader, heights);
    this.hf = hf;
    const add = (name, obj) => {
      obj.name = name;
      obj.visible = !this.hidden.has(name);
      this.world.add(obj);
      this.layers[name] = obj;
      return obj;
    };

    // Water: BGT-vlakken met een peil en sloten als hartlijn; beide worden een veelhoek met oevers
    const bodies = [];
    const nogo = new THREE.Group(), far = new THREE.Group();
    for (const s of scene.surfaces) {
      if (s.kind !== 'water') continue;
      const level = s.level ?? s.poly.reduce((a, p) => a + hf.at(p[0], p[1]), 0) / s.poly.length - 0.3;
      bodies.push({ poly: s.poly, level, bank: BANK_M });
      far.add(ribbon(s.poly, hf, color.water, { closed: true, width: 0.02, px: 1.0, z: level + 0.02, order: ORDER.water + 1 }));
      nogo.add(ribbon(s.poly, hf, color.nogo, { closed: true, width: 0.14, px: 1.5, offset: 0.12, order: ORDER.overlay + 2 }));
    }
    for (const d of scene.ditches) {
      // Zelfde profiel als de fysica (terrain._carve_ditch): talud van de insteek naar de bodem, water tot het peil
      const half = d.width / 2, talud = Math.max(0.2, Math.min(d.depth, half * 0.7));
      const waterHalf = Math.max(0.15, half - talud * (1 - (d.water_depth ?? 0.3) / d.depth));
      bodies.push({ poly: bufferLine(d.line, waterHalf), level: d.level, bank: half - waterHalf });
      // van ver weg is een sloot van 2 m minder dan een beeldpunt: dit lint houdt hem zichtbaar
      far.add(ribbon(d.line, hf, color.water, { width: 0.02, px: 1.6, z: d.level + 0.02, order: ORDER.water + 1 }));
      nogo.add(ribbon(bufferLine(d.line, half), hf, color.nogo, { closed: true, width: 0.14, px: 1.5, offset: 0.12,
        order: ORDER.overlay + 2 }));
    }
    const water = waterBodies(bodies, hf);

    const ground = new THREE.Group();
    ground.add(horizon(hf), terrainMesh(hf, water.zShow));
    add('terrain', ground);
    water.group.add(far);
    add('ditches', water.group);
    add('nogo', nogo);

    const surfaces = new THREE.Group();
    const mats = {};
    scene.surfaces.forEach((s, i) => {
      if (s.kind === 'water') return;
      mats[s.kind] ??= groundMat(surfaceTexture(s.kind), { macroAmount: HARD.has(s.kind) ? 0.1 : 0.3 });
      const order = (HARD.has(s.kind) ? ORDER.field + 2 : ORDER.surface) + (i / scene.surfaces.length) * 5;
      surfaces.add(drapePolygon(s.poly, hf, mats[s.kind], { order }));
    });
    add('surfaces', surfaces);

    const fields = new THREE.Group(), bounds = new THREE.Group(), labels = new THREE.Group();
    for (const f of scene.fields) {
      const tex = cropTexture(f.crop);
      const m = drapePolygon(f.poly, hf, groundMat(tex, { angle: tex.rows ? longestEdgeAngle(f.poly) : 0 }),
        { order: ORDER.field, offset: 0.03 });
      m.userData = { kind: 'field', id: f.id, name: f.name };
      fields.add(m);
      bounds.add(ribbon(f.poly, hf, color.boundary, { closed: true, width: 0.3, px: 1.1, offset: 0.08 }));
      if (f.area_ha >= 0.25) {
        const l = label(f.name, `${f.crop ? f.crop + ' · ' : ''}${f.area_ha} ha`);
        l.position.set(f.center[0], f.center[1], hf.at(f.center[0], f.center[1]) + 2.5);
        l.userData.size = Math.sqrt(f.area_ha * 1e4);
        labels.add(l);
        this._labels.push(l);
      }
    }
    add('fields', fields);
    add('boundaries', bounds);
    add('labels', labels);

    const bld = new THREE.Group();
    for (const b of scene.buildings) bld.add(building(b));
    add('buildings', bld);
    add('trees', trees(scene.trees));
    add('fences', fences(scene.fences, hf));
    add('farm', this._farmLayer(scene, hf));

    if (!this.robot) {
      this.robot = new RobotModel(scene.robot);
      this.scene.add(this.robot.group);
    }
    const s = scene.start;
    this.setRobot({ x: s.x, y: s.y, z: hf.at(s.x, s.y), heading: s.heading, pitch: 0, roll: 0,
      wheel_left_deg: 0, wheel_right_deg: 0 });
    this._snap = true;          // eerste beeld direct achter de robot, niet vanaf de oorsprong inzwaaien
    this.setMode(this.mode === 'rond' ? 'rond' : 'volg');
  }

  // Eigen aanpassingen van de boerderij: verboden gebieden (rood), rijzones (blauw), doorgangen (groen, de enige
  // plek over een sloot of dijk), nesten (oranje kring, vol = nu in zijn periode), markers en de kruin van dijken.
  _farmLayer(scene, hf) {
    const g = new THREE.Group();
    const circle = (x, y, r, n = 40) => Array.from({ length: n }, (_, i) => [x + r * Math.cos((i / n) * 2 * Math.PI),
      y + r * Math.sin((i / n) * 2 * Math.PI)]);
    const post = (x, y, h, c) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, h, 10),
        new THREE.MeshStandardMaterial({ color: c, roughness: 0.6 }));
      m.rotation.x = Math.PI / 2;
      m.position.set(x, y, hf.at(x, y) + h / 2);
      m.castShadow = true;
      m.raycast = () => {};
      return m;
    };
    const area = (poly, c, alpha) => {
      const mat = flatDecal(c).clone();
      mat.transparent = true; mat.opacity = alpha; mat.depthWrite = false;
      const m = drapePolygon(poly, hf, mat, { order: ORDER.overlay, offset: 0.07 });
      m.raycast = () => {};
      return m;
    };
    const add = (o) => { if (o) g.add(o); };
    for (const z of scene.no_go ?? []) {
      const c = z.action === 'mijden' ? [1.0, 0.70, 0.24] : [1.0, 0.22, 0.18];
      add(area(z.poly, c, 0.28));
      add(ribbon(z.poly, hf, c, { closed: true, width: 0.25, px: 2.0, offset: 0.12, order: ORDER.overlay + 2 }));
    }
    for (const z of scene.zones ?? []) {
      const c = z.klass === 'verboden' ? [1.0, 0.22, 0.18] : z.klass === 'werken' ? [0.49, 0.86, 0.24]
        : z.klass === 'niet_bewerken' ? [0.71, 0.55, 1.0] : [0.31, 0.70, 1.0];
      if (z.klass === 'verboden') add(area(z.poly, c, 0.25));
      if (z.klass === 'niet_bewerken') add(area(z.poly, c, 0.3));
      add(ribbon(z.poly, hf, c, { closed: true, width: 0.2, px: 1.6, offset: 0.11, dash: 1.0, order: ORDER.overlay + 2 }));
    }
    for (const f of scene.fields) for (const h of f.holes ?? []) {
      add(ribbon(h.poly, hf, h.passable ? [1.0, 0.84, 0.42] : [1.0, 0.42, 0.36], { closed: true, width: 0.2, px: 1.6,
        offset: 0.11, dash: h.passable ? 0.8 : 0, order: ORDER.overlay + 2 }));
    }
    for (const p of scene.passages ?? []) {
      const band = bufferLine(p.line, p.width / 2);
      add(area(band, [0.72, 0.89, 0.29], 0.22));
      add(ribbon(band, hf, [0.72, 0.89, 0.29], { closed: true, width: 0.18, px: 1.8, offset: 0.12, order: ORDER.overlay + 2 }));
      for (const q of [band[0], band[band.length - 1], band[band.length / 2 - 1], band[band.length / 2]]) {
        if (q) g.add(post(q[0], q[1], 1.3, p.confirm ? 0xffb23e : 0xb9e34a));
      }
    }
    for (const n of scene.nests ?? []) {
      const c = [1.0, 0.70, 0.24];
      if (n.active) add(area(circle(n.x, n.y, n.radius), c, 0.3));
      add(ribbon(circle(n.x, n.y, n.radius), hf, c, { closed: true, width: 0.15, px: 1.6, offset: 0.12,
        dash: n.active ? 0 : 0.6, order: ORDER.overlay + 2 }));
      g.add(post(n.x, n.y, 1.6, 0xffb23e));
    }
    for (const m of scene.markers ?? []) {
      g.add(post(m.x, m.y, 1.0, 0xffffff));
      if (m.radius > 0) add(ribbon(circle(m.x, m.y, m.radius, 20), hf, [1, 1, 1], { closed: true, width: 0.08, px: 1.2,
        offset: 0.1, order: ORDER.overlay + 2 }));
    }
    // slootzone: de verboden strook langs sloot en water (oranje rand); wie erin komt stopt
    for (const ring of scene.ditch_zone?.rings ?? []) {
      if (ring.length > 2) add(ribbon(ring, hf, [1.0, 0.48, 0.16], { closed: true, width: 0.22, px: 1.7, offset: 0.11,
        maxSeg: 4, order: ORDER.overlay + 2 }));
    }
    for (const d of scene.dikes ?? []) {
      add(ribbon(d.line, hf, [0.79, 0.64, 0.42], { width: 0.2, px: 1.4, offset: 0.1, order: ORDER.overlay + 1 }));
      add(ribbon(bufferLine(d.line, d.half_base), hf, [0.79, 0.64, 0.42], { closed: true, width: 0.12, px: 1.0, offset: 0.1,
        dash: 1.2, order: ORDER.overlay + 1 }));
    }
    return g;
  }

  // Luchtfoto over het terrein (zelfde tegels als in de editor): [{ img, b: { x0, y0, x1, y1 } }] in meters; null = weg.
  setAerial(tiles) {
    if (this._aerial) {
      this.scene.remove(this._aerial);
      this._aerial.traverse((o) => { o.geometry?.dispose(); o.material?.map?.dispose(); o.material?.dispose(); });
      this._aerial = null;
    }
    if (!tiles?.length || !this.hf) return;
    this._aerial = new THREE.Group();
    for (const t of tiles) this._aerial.add(this._aerialTile(t.img, t.b));
    this.scene.add(this._aerial);
  }

  _aerialTile(bitmap, b) {
    const step = Math.max(1.5, Math.sqrt(((b.x1 - b.x0) * (b.y1 - b.y0)) / 40000));
    const nx = Math.max(2, Math.ceil((b.x1 - b.x0) / step)), ny = Math.max(2, Math.ceil((b.y1 - b.y0) / step));
    const pos = new Float32Array((nx + 1) * (ny + 1) * 3), uv = new Float32Array((nx + 1) * (ny + 1) * 2);
    for (let j = 0, k = 0; j <= ny; j++) for (let i = 0; i <= nx; i++, k++) {
      const x = b.x0 + ((b.x1 - b.x0) * i) / nx, y = b.y0 + ((b.y1 - b.y0) * j) / ny;
      pos[k * 3] = x; pos[k * 3 + 1] = y; pos[k * 3 + 2] = this.hf.at(x, y) + 0.045;
      uv[k * 2] = i / nx; uv[k * 2 + 1] = 1 - j / ny;         // bitmap: bovenkant = noord
    }
    const idx = [];
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const a = j * (nx + 1) + i, c = a + nx + 1;
      idx.push(a, a + 1, c, a + 1, c + 1, c);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    const tex = new THREE.CanvasTexture(bitmap);
    tex.flipY = false; tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8; tex.needsUpdate = true;
    const m = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ map: tex, polygonOffset: true, polygonOffsetFactor: -2,
      polygonOffsetUnits: -2 }));
    m.receiveShadow = true;
    m.renderOrder = ORDER.field + 8;
    m.raycast = () => {};
    return m;
  }

  setLayerVisible(name, on) {
    if (on) this.hidden.delete(name); else this.hidden.add(name);
    if (this.layers[name]) this.layers[name].visible = on;
  }

  // ------------------------------------------------------------------ robot en camera
  setRobot(r) {
    this.robotPose = r;
    if (this.robot) this.robot.setPose(r);
  }

  // ------------------------------------------------------------------ aanbouwdeel
  setImplement(spec) {
    if (this.implement?.spec.id === spec?.id) return;
    this.implement?.dispose();
    this.implement = null;
    this.clearStrip();
    if (!spec || !this.robot) return;
    this.implement = new ImplementModel(spec);
    this.robot.body.add(this.implement.group);
    const c = new THREE.Color(spec.color).convertLinearToSRGB();
    this._stripMat = flatDecal([c.r, c.g, c.b]);
  }

  updateImplement(s) {
    this.implement?.update(s);
    if (!s || !this.hf) return;
    if (s.gen !== this._stripGen) { this.clearStrip(); this._stripGen = s.gen; }
    if (s.strip.points.length) this._addStrip(s.strip.points);
  }

  clearStrip() {
    this.stripGroup.traverse((o) => o.geometry?.dispose());
    this.stripGroup.clear();
    this._stripPts = [];
    this._stripStart = 0;
    this._tail = null;
  }

  // Boor: planten kleuren naar status (open oranje, geboord groen, te snel voorbij rood, buiten bereik paars)
  setTargetStatus(targets) {
    const inst = this._pointsInst;
    if (!inst || !targets) return;
    const col = { open: 0xffa02e, done: 0x7ddc3c, missed_speed: 0xff4a3d, out_of_reach: 0xb06cff };
    const c = new THREE.Color();
    targets.forEach((t, i) => { if (i < inst.count) inst.setColorAt(i, c.set(col[t.status] ?? col.open)); });
    if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
  }

  // Strook als vierhoeken tussen opeenvolgende [lx, ly, rx, ry]; null = werktuig was geheven
  _addStrip(points) {
    this._stripPts.push(...points);
    const flush = (end) => {
      const chunk = this._stripPts.slice(this._stripStart, end + 1);
      const pos = [];
      for (let i = 1; i < chunk.length; i++) {
        const a = chunk[i - 1], b = chunk[i];
        if (!a || !b) continue;
        const z = (x, y) => this.hf.at(x, y) + 0.06;
        const p = [[a[0], a[1]], [a[2], a[3]], [b[0], b[1]], [b[2], b[3]]].map(([x, y]) => [x, y, z(x, y)]);
        pos.push(...p[0], ...p[1], ...p[2], ...p[1], ...p[3], ...p[2]);
      }
      if (pos.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
        g.computeVertexNormals();
        const m = new THREE.Mesh(g, this._stripMat ?? flatDecal(color.treated));
        m.receiveShadow = true;
        m.renderOrder = ORDER.overlay;
        m.raycast = () => {};
        this.stripGroup.add(m);
      }
      this._stripStart = end;
    };
    while (this._stripPts.length - this._stripStart > 30) flush(this._stripStart + 30);
    // het laatste stuk steeds opnieuw tekenen, zodat de strook direct achter het werktuig meegroeit
    if (this._tail) { this.stripGroup.remove(this._tail); this._tail.geometry.dispose(); this._tail = null; }
    const start = this._stripStart, n = this.stripGroup.children.length;
    flush(this._stripPts.length - 1);
    this._stripStart = start;
    if (this.stripGroup.children.length > n) this._tail = this.stripGroup.children[n];
  }

  setBeacon(state) {
    this.robot?.setBeacon(state);
  }

  setMode(mode) {
    if (mode === 'volg' && this.mode !== 'volg') this._snap = true;
    this.mode = mode;
    const r = this.robotPose;
    if (!r) return;
    if (mode === 'boven') {
      this.controls.target.set(r.x, r.y, r.z);
      this.camera.position.set(r.x, r.y - 1, r.z + 120);
    } else if (mode === 'vrij') {
      this.controls.target.set(r.x, r.y, r.z + 0.4);
    } else if (mode === 'rond') {
      this._orbit = { x: r.x, y: r.y, z: r.z, t0: this._clock.getElapsedTime() };
    }
  }

  focus(x, y, dist = 160) {
    this.setMode('vrij');
    const z = this.hf ? this.hf.at(x, y) : 0;
    this.controls.target.set(x, y, z);
    this.camera.position.set(x - dist * 0.25, y - dist * 0.8, z + dist * 0.6);
  }

  _updateCamera(t) {
    const r = this.robotPose;
    if (!r) return;
    if (this.mode === 'rond' && this._orbit) {
      const o = this._orbit, a = 0.6 + (t - o.t0) * 0.035;
      this.camera.position.set(o.x + Math.sin(a) * 120, o.y - Math.cos(a) * 120, o.z + 46);
      this.controls.target.set(o.x, o.y, o.z + 3);
      return;
    }
    if (this.mode !== 'volg') return;
    const h = THREE.MathUtils.degToRad(r.heading);
    const fx = Math.sin(h), fy = Math.cos(h);
    const tx = r.x + fx * FOLLOW.ahead, ty = r.y + fy * FOLLOW.ahead;
    const want = this._tmp.set(r.x - fx * FOLLOW.back + fy * 2.5, r.y - fy * FOLLOW.back - fx * 2.5, r.z + FOLLOW.up);
    const k = this._snap ? 1 : 0.08;
    this._snap = false;
    this.camera.position.lerp(want, k);
    this.controls.target.lerp(want.set(tx, ty, r.z + 0.3), k === 1 ? 1 : 0.15);
  }

  // Veldnamen: zichtbaar zodra het veld groot genoeg in beeld is, weg als je er middenin staat
  _updateLabels() {
    if (!this.layers.labels?.visible) return;
    const menu = this.mode === 'rond';
    const cam = this.camera.position;
    for (const l of this._labels) {
      const d = cam.distanceTo(l.position);
      const k = l.userData.size / d;                 // veldmaat / afstand ~ grootte in beeld
      const a = THREE.MathUtils.smoothstep(k, 0.09, 0.16) * THREE.MathUtils.smoothstep(d, 22, 45);
      l.visible = a > 0.02 && !menu;
      l.material.opacity = a;
    }
  }

  // Dubbelklik: straal door het hoogteraster stappen (snel, ook bij een terrein van een miljoen punten)
  _pick(e) {
    if (!this.hf || !this.onPick) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1), this.camera);
    const o = ray.ray.origin, d = ray.ray.direction;
    if (d.z >= -1e-4) return;
    let s = Math.max(0, (o.z - 40) / -d.z), hit = null;
    for (let i = 0; i < 20000 && s < 6000; i++) {
      const x = o.x + d.x * s, y = o.y + d.y * s, z = o.z + d.z * s;
      if (z <= this.hf.at(x, y)) { hit = [x, y]; break; }
      s += Math.max(0.15, (z - this.hf.at(x, y)) * 0.5);
    }
    if (!hit) return;
    const f = this.layers.fields?.visible === false ? null
      : this.sceneData.fields.find((fd) => inPolygon(hit[0], hit[1], fd.poly));
    this.onPick({ x: hit[0], y: hit[1], field: f ? { kind: 'field', id: f.id, name: f.name } : null });
  }

  // ------------------------------------------------------------------ missie en spoor
  setMission(plan) {
    this.overlay.traverse((o) => o.geometry?.dispose());
    this.overlay.clear();
    this._path = null;
    if (!plan || !this.hf) return;
    if (plan.kind === 'veldmissie') { this._setFieldMission(plan); return; }
    for (const [a, b] of plan.swaths) {
      this.overlay.add(ribbon([a, b], this.hf, color.lines, { width: 0.07, px: 0.7, offset: 0.07, maxSeg: 4 }));
    }
    for (const [p, c] of [[plan.a, 0xb9e34a], [plan.b, 0xffb23e]]) {
      const m = new THREE.Mesh(new THREE.ConeGeometry(0.32, 1.3, 16),
        new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.6, roughness: 0.4 }));
      m.rotation.x = -Math.PI / 2;
      m.position.set(p[0], p[1], this.hf.at(p[0], p[1]) + 0.75);
      m.castShadow = true;
      this.overlay.add(m);
    }
    this.trailWidth = plan.entry?.werkbreedte_m ?? 0;
  }

  // Veldmissie: toegestaan gebied (stippel), randen per soort, rondjes/banen/bochten/overgangen in eigen kleur
  _setFieldMission(plan) {
    const segColor = { rondje: [0.30, 0.62, 1.0], baan: [1.0, 1.0, 1.0], bocht: [1.0, 0.62, 0.15],
      overgang: [0.78, 0.45, 1.0], transport: [0.25, 0.9, 0.85], doorgang: [0.72, 0.89, 0.29] };
    const edgeColor = { water: [0.2, 0.55, 1.0], weg: [0.15, 0.15, 0.15], verhard: [0.6, 0.6, 0.6],
      hek: [0.62, 0.38, 0.18], gebouw: [0.85, 0.2, 0.2], boom: [0.2, 0.6, 0.25], perceel: [1.0, 0.82, 0.1],
      open: [0.55, 0.85, 0.35] };
    for (const poly of plan.allowed) {
      for (const ring of poly) {
        this.overlay.add(ribbon(ring, this.hf, color.boundary, { closed: true, width: 0.1, px: 1.0, offset: 0.09,
          maxSeg: 3, dash: 0.8 }));
      }
    }
    for (const e of plan.edges) {
      this.overlay.add(ribbon(e.pts, this.hf, edgeColor[e.kind] ?? [1, 1, 1], { width: 0.35, px: 2.2, offset: 0.11,
        maxSeg: 3, order: ORDER.overlay + 1 }));
    }
    for (const sg of plan.segments) {
      const c = sg.ok ? segColor[sg.type] ?? [1, 1, 1] : [1.0, 0.15, 0.1];
      this.overlay.add(ribbon(sg.pts, this.hf, c, { width: sg.ok ? (sg.type === 'baan' ? 0.07 : 0.06) : 0.3,
        px: !sg.ok ? 2.6 : sg.implement === 'omlaag' ? 0.9 : sg.type === 'transport' || sg.type === 'doorgang' ? 1.3 : 0.7,
        offset: 0.08, maxSeg: 2, dash: sg.implement === 'omhoog' ? 0.6 : 0, order: ORDER.overlay + 1 }));
    }
    const first = plan.segments[0]?.pts[0];
    if (first) {
      const m = new THREE.Mesh(new THREE.ConeGeometry(0.32, 1.3, 16),
        new THREE.MeshStandardMaterial({ color: 0xb9e34a, emissive: 0xb9e34a, emissiveIntensity: 0.6, roughness: 0.4 }));
      m.rotation.x = -Math.PI / 2;
      m.position.set(first[0], first[1], this.hf.at(first[0], first[1]) + 0.75);
      this.overlay.add(m);
    }
    this.trailWidth = plan.settings?.work_width_m ?? 0;
  }

  setPlannedPath(points) {
    if (this._path) { this.overlay.remove(this._path); this._path.geometry.dispose(); }
    this._path = null;
    if (!points || points.length < 2 || !this.hf) return;
    this._path = ribbon(points, this.hf, color.path, { width: 0.12, px: 1.3, offset: 0.1, maxSeg: 1, dash: 1.2,
      order: ORDER.overlay + 1 });
    this.overlay.add(this._path);
  }

  // Replay van een ritlog: het gereden pad (oranje) en een 'spook'-pijl op de positie van dat moment
  setReplay(xs, ys) {
    if (this._replay) this.scene.remove(this._replay);
    this._replay = null;
    if (!xs || xs.length < 2 || !this.hf) return;
    const group = new THREE.Group();
    const pts = xs.map((x, i) => [x, ys[i]]);
    group.add(ribbon(pts, this.hf, [1.0, 0.48, 0.12], { width: 0.14, px: 1.4, offset: 0.12, maxSeg: 50,
      order: ORDER.overlay + 1 }));
    const shape = new THREE.Shape([new THREE.Vector2(0, 0.9), new THREE.Vector2(0.45, -0.5), new THREE.Vector2(0, -0.25),
      new THREE.Vector2(-0.45, -0.5)]);
    const g = new THREE.ExtrudeGeometry(shape, { depth: 0.12, bevelEnabled: false });
    this._ghost = solid(g, [1.0, 0.48, 0.12], { edges: false });
    this._ghost.rotation.order = 'ZXY';
    group.add(this._ghost);
    this._replay = group;
    this.scene.add(group);
  }

  setGhost(x, y, heading) {
    if (!this._ghost || !this.hf) return;
    this._ghost.position.set(x, y, this.hf.at(x, y) + 0.9);
    this._ghost.rotation.z = -THREE.MathUtils.degToRad(heading);
  }

  // Puntmissie: paaltjes met de route ertussen (in volgorde)
  setPoints(points) {
    if (this._points) this.scene.remove(this._points);
    this._points = null;
    this._pointsInst = null;
    if (!points?.length || !this.hf) return;
    const group = new THREE.Group();
    const g = new THREE.CylinderGeometry(0.1, 0.1, 0.7, 12);
    g.rotateX(Math.PI / 2);
    g.translate(0, 0, 0.35);
    const inst = new THREE.InstancedMesh(g, new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5 }), points.length);
    this._pointsInst = inst;
    inst.castShadow = true;
    inst.frustumCulled = false;
    const m = new THREE.Matrix4();
    const orange = new THREE.Color(0xffa02e);
    points.forEach((p, i) => { inst.setMatrixAt(i, m.makeTranslation(p.x, p.y, this.hf.at(p.x, p.y)));
      inst.setColorAt(i, orange); });
    group.add(inst);
    if (points.length > 1) {
      group.add(ribbon(points.map((p) => [p.x, p.y]), this.hf, [1.0, 0.63, 0.18], { width: 0.08, px: 0.9, offset: 0.15,
        maxSeg: 3, order: ORDER.overlay + 1 }));
    }
    this._points = group;
    this.scene.add(group);
  }

  clearTrail() {
    this.trailGroup.traverse((o) => o.geometry?.dispose());
    this.trailGroup.clear();
    this.trailPts = [];
    this._trailChunkStart = 0;
  }

  addTrail(points) {
    if (!this.hf || !points.length) return;
    this.trailPts.push(...points);
    // Per 40 punten (ca. 8 m) een nieuw stuk: een bewerkte strook (bij een missie) of een smal spoor
    while (this.trailPts.length - this._trailChunkStart >= 40) {
      const chunk = this.trailPts.slice(this._trailChunkStart, this._trailChunkStart + 41);
      this._trailChunkStart += 40;
      const strip = this.trailWidth > 0 && this.implement?.spec.kind !== 'strip'
        ? drapeStrip(chunk, this.trailWidth, this.hf, flatDecal(color.treated), { offset: 0.05 })
        : ribbon(chunk, this.hf, [0.95, 0.97, 1.0], { width: 0.1, px: 0.8, offset: 0.06, maxSeg: 2, order: ORDER.overlay });
      if (strip) this.trailGroup.add(strip);
    }
  }
}

function longestEdgeAngle(poly) {
  let best = 0, angle = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (l > best) { best = l; angle = Math.atan2(b[1] - a[1], b[0] - a[0]); }
  }
  return angle;
}

function inPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// Naamkaartje boven een veld: altijd even groot in beeld, dwars door alles heen leesbaar
function label(title, sub) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const f1 = '600 44px "Barlow Condensed", "Bahnschrift", "Segoe UI", sans-serif';
  const f2 = '500 26px "Barlow", "Segoe UI", sans-serif';
  ctx.font = f1;
  const w1 = ctx.measureText(title.toUpperCase()).width;
  ctx.font = f2;
  const w = Math.ceil(Math.max(w1, ctx.measureText(sub).width)) + 56;
  c.width = w; c.height = 112;
  ctx.fillStyle = 'rgba(11,17,21,0.78)';
  ctx.beginPath(); ctx.roundRect(0, 0, w, 112, 10); ctx.fill();
  ctx.fillStyle = '#B9E34A';
  ctx.fillRect(0, 0, 6, 112);
  ctx.textBaseline = 'middle';
  ctx.font = f1; ctx.fillStyle = '#F2F6F7';
  ctx.fillText(title.toUpperCase(), 28, 40);
  ctx.font = f2; ctx.fillStyle = '#A9B8BF';
  ctx.fillText(sub, 28, 82);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, sizeAttenuation: false, depthTest: false, transparent: true,
    fog: false, toneMapped: false }));
  const k = 0.00034;
  s.scale.set(w * k, 112 * k, 1);
  s.renderOrder = 50;
  s.raycast = () => {};
  return s;
}

export { waterMat };
