// De 3D-wereld: terrein, velden, gebouwen, robot, missie en spoor. Z-as omhoog, zoals ENU in de sim.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { background, color, fovDeg, surfaceColor } from '../../theme.js';
import { building, fences, trees } from './buildings.js';
import { lineMat, solid } from './materials.js';
import { ImplementModel } from './implement.js';
import { RobotModel } from './robot.js';
import { drapeLine, drapePolygon, drapeStrip, Heightfield, terrainMesh } from './terrain.js';

// Camerastanden. 'volg' = zoals track_camera() in de veldanimaties: schuin achter en boven de robot.
export const CAMERA_MODES = ['volg', 'vrij', 'boven'];
const FOLLOW = { back: 7.5, up: 5.0, ahead: 2.0 };

export class World3D {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(background);
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(background);
    this.camera = new THREE.PerspectiveCamera(fovDeg, 1, 0.5, 6000);
    this.camera.up.set(0, 0, 1);
    // FreeCAD-headlight: lamp vanuit de camera, plus zacht omgevingslicht. Geen schaduwen.
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xb8b8b0, 1.35));
    this.headlight = new THREE.DirectionalLight(0xffffff, 1.55);
    // Lamp en doel hangen allebei aan de camera: het licht valt altijd mee met de kijkrichting.
    this.camera.add(this.headlight, this.headlight.target);
    this.headlight.position.set(0.4, 0.6, 0);
    this.headlight.target.position.set(0, 0, -1);
    this.scene.add(this.camera);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.screenSpacePanning = false;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.mode = 'volg';
    this.layers = {};
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
    this._raycaster = new THREE.Raycaster();
    this._pickables = [];
    this._resize = new ResizeObserver(() => this.resize());
    this._resize.observe(container);
    this.renderer.domElement.addEventListener('dblclick', (e) => this._pick(e));
    this.controls.addEventListener('start', () => { if (this.mode === 'volg') this.setMode('vrij'); });
    this.resize();
    const loop = () => {
      this._raf = requestAnimationFrame(loop);
      this._updateCamera();
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    this._resize.disconnect();
    this.renderer.dispose();
    this.container.removeChild(this.renderer.domElement);
  }

  resize() {
    const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // ------------------------------------------------------------------ wereld opbouwen
  load(scene, terrainHeader, heights) {
    this.world.clear();
    this.overlay.clear();
    this.clearTrail();
    this.clearStrip();
    this.setPoints(null);
    this.setReplay(null);
    this._pickables = [];
    this.sceneData = scene;
    const hf = new Heightfield(terrainHeader, heights);
    this.hf = hf;
    const add = (name, obj) => { obj.name = name; this.world.add(obj); this.layers[name] = obj; return obj; };

    add('terrain', terrainMesh(hf, color.grass));

    const surfaces = new THREE.Group();
    for (const s of scene.surfaces) {
      // Water met een peil (BGT) ligt vlak; de rest volgt het terrein
      const flat = s.level !== null && s.level !== undefined;
      const m = drapePolygon(s.poly, hf, surfaceColor(s.kind), { offset: 0.02, layer: flat ? 3 : 1, z: flat ? s.level : null,
        maxEdge: flat ? 50 : 4 });
      surfaces.add(m, drapeLine(s.poly, hf, { closed: true, offset: 0.04 }));
    }
    add('surfaces', surfaces);

    const fields = new THREE.Group();
    for (const f of scene.fields) {
      const m = drapePolygon(f.poly, hf, color.field, { offset: 0.03, layer: 2 });
      m.userData = { kind: 'field', id: f.id, name: f.name };
      this._pickables.push(m);
      fields.add(m, drapeLine(f.poly, hf, { closed: true, offset: 0.06 }));
    }
    add('fields', fields);

    const water = new THREE.Group();
    for (const d of scene.ditches) {
      const strip = drapeStrip(d.line, d.width * 0.72, hf, color.water, { z: d.level, layer: 3 });
      if (strip) water.add(strip);
      for (const side of [-1, 1]) {
        // slootkanten als lijn: de FreeCAD-omlijning van het water
        const off = offsetLine(d.line, side * d.width / 2);
        water.add(drapeLine(off, hf, { offset: 0.05 }));
      }
    }
    add('ditches', water);

    const bld = new THREE.Group();
    for (const b of scene.buildings) bld.add(building(b));
    add('buildings', bld);
    add('trees', trees(scene.trees));
    add('fences', fences(scene.fences, hf));

    if (!this.robot) {
      this.robot = new RobotModel(scene.robot);
      this.scene.add(this.robot.group);
    }
    const s = scene.start;
    this.setRobot({ x: s.x, y: s.y, z: hf.at(s.x, s.y), heading: s.heading, pitch: 0, roll: 0,
      wheel_left_deg: 0, wheel_right_deg: 0 });
    this._snap = true;          // eerste beeld direct achter de robot, niet vanaf de oorsprong inzwaaien
    this.setMode('volg');
  }

  setLayerVisible(name, on) {
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
    this._stripColor = new THREE.Color(spec.color);
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
    const col = { open: 0xe66e14, done: 0x2e9e44, missed_speed: 0xd02a2a, out_of_reach: 0x8e44ad };
    const c = new THREE.Color();
    targets.forEach((t, i) => { if (i < inst.count) inst.setColorAt(i, c.set(col[t.status] ?? 0xe66e14)); });
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
        const m = new THREE.MeshLambertMaterial({ color: this._stripColor, side: THREE.DoubleSide,
          transparent: true, opacity: 0.8, depthWrite: false, polygonOffset: true, polygonOffsetFactor: 0,
          polygonOffsetUnits: -16 });
        this.stripGroup.add(new THREE.Mesh(g, m));
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
    }
  }

  focus(x, y, dist = 160) {
    this.setMode('vrij');
    const z = this.hf ? this.hf.at(x, y) : 0;
    this.controls.target.set(x, y, z);
    this.camera.position.set(x - dist * 0.25, y - dist * 0.8, z + dist * 0.6);
  }

  _updateCamera() {
    const r = this.robotPose;
    if (!r || this.mode !== 'volg') return;
    const h = THREE.MathUtils.degToRad(r.heading);
    const fx = Math.sin(h), fy = Math.cos(h);
    const tx = r.x + fx * FOLLOW.ahead, ty = r.y + fy * FOLLOW.ahead;
    const want = new THREE.Vector3(r.x - fx * FOLLOW.back + fy * 2.5, r.y - fy * FOLLOW.back - fx * 2.5,
      r.z + FOLLOW.up);
    const k = this._snap ? 1 : 0.08;
    this._snap = false;
    this.camera.position.lerp(want, k);
    this.controls.target.lerp(new THREE.Vector3(tx, ty, r.z + 0.3), k === 1 ? 1 : 0.15);
  }

  _pick(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const p = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this._raycaster.setFromCamera(p, this.camera);
    const hits = this._raycaster.intersectObjects([...this._pickables, this.layers.terrain].filter(Boolean), false);
    if (hits.length && this.onPick) {
      const h = hits[0];
      this.onPick({ x: h.point.x, y: h.point.y, field: h.object.userData?.kind === 'field' ? h.object.userData : null });
    }
  }

  // ------------------------------------------------------------------ missie en spoor
  setMission(plan) {
    this.overlay.clear();
    if (!plan || !this.hf) return;
    for (const [a, b] of plan.swaths) {
      this.overlay.add(drapeLine([a, b], this.hf, { color: color.lines, offset: 0.07, maxSeg: 4 }));
    }
    for (const [p, label] of [[plan.a, 'A'], [plan.b, 'B']]) {
      const m = new THREE.Mesh(new THREE.ConeGeometry(0.35, 1.2, 4), new THREE.MeshLambertMaterial({ color: 0xe66e14 }));
      m.rotation.x = -Math.PI / 2;
      m.position.set(p[0], p[1], this.hf.at(p[0], p[1]) + 0.7);
      m.userData.label = label;
      this.overlay.add(m);
    }
    this.trailWidth = plan.entry?.werkbreedte_m ?? 0;
  }

  setPlannedPath(points) {
    if (this._path) this.overlay.remove(this._path);
    this._path = null;
    if (!points || points.length < 2 || !this.hf) return;
    this._path = drapeLine(points, this.hf, { color: color.path, offset: 0.1, maxSeg: 1, dashed: true });
    this.overlay.add(this._path);
  }

  // Replay van een ritlog: het gereden pad (oranje) en een 'spook'-pijl op de positie van dat moment
  setReplay(xs, ys) {
    if (this._replay) this.scene.remove(this._replay);
    this._replay = null;
    if (!xs || xs.length < 2 || !this.hf) return;
    const group = new THREE.Group();
    const pts = xs.map((x, i) => [x, ys[i]]);
    group.add(drapeLine(pts, this.hf, { color: [0.90, 0.43, 0.08], offset: 0.12, maxSeg: 50 }));
    const shape = new THREE.Shape([new THREE.Vector2(0, 0.9), new THREE.Vector2(0.45, -0.5), new THREE.Vector2(0, -0.25),
      new THREE.Vector2(-0.45, -0.5)]);
    const g = new THREE.ExtrudeGeometry(shape, { depth: 0.12, bevelEnabled: false });
    this._ghost = solid(g, [0.90, 0.43, 0.08]);
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

  // Puntmissie: oranje paaltjes met de route ertussen (in volgorde)
  setPoints(points) {
    if (this._points) this.scene.remove(this._points);
    this._points = null;
    this._pointsInst = null;
    if (!points?.length || !this.hf) return;
    const group = new THREE.Group();
    const g = new THREE.CylinderGeometry(0.12, 0.12, 0.6, 10);
    g.rotateX(Math.PI / 2);
    g.translate(0, 0, 0.3);
    const inst = new THREE.InstancedMesh(g, new THREE.MeshLambertMaterial({ color: 0xffffff }), points.length);
    this._pointsInst = inst;
    const m = new THREE.Matrix4();
    const orange = new THREE.Color(0xe66e14);
    points.forEach((p, i) => { inst.setMatrixAt(i, m.makeTranslation(p.x, p.y, this.hf.at(p.x, p.y)));
      inst.setColorAt(i, orange); });
    group.add(inst);
    if (points.length > 1) {
      group.add(drapeLine(points.map((p) => [p.x, p.y]), this.hf, { color: [0.90, 0.43, 0.08], offset: 0.15, maxSeg: 3 }));
    }
    this._points = group;
    this.scene.add(group);
  }

  clearTrail() {
    this.trailGroup.clear();
    this.trailPts = [];
    this._trailChunkStart = 0;
  }

  addTrail(points) {
    if (!this.hf || !points.length) return;
    this.trailPts.push(...points);
    // Per 40 punten (ca. 8 m) een nieuw stuk: een bewerkte strook (bij een missie) of een smal wit spoor
    while (this.trailPts.length - this._trailChunkStart >= 40) {
      const chunk = this.trailPts.slice(this._trailChunkStart, this._trailChunkStart + 41);
      this._trailChunkStart += 40;
      const strip = this.trailWidth > 0 && this.implement?.spec.kind !== 'strip'
        ? drapeStrip(chunk, this.trailWidth, this.hf, color.treated, { offset: 0.05, layer: 3 })
        : drapeStrip(chunk, 0.12, this.hf, color.path, { offset: 0.05, layer: 3 });
      if (strip) this.trailGroup.add(strip);
    }
  }
}

function offsetLine(line, d) {
  return line.map((p, i) => {
    const a = line[Math.max(0, i - 1)], b = line[Math.min(line.length - 1, i + 1)];
    let dx = b[0] - a[0], dy = b[1] - a[1];
    const l = Math.hypot(dx, dy) || 1;
    dx /= l; dy /= l;
    return [p[0] + dy * d, p[1] - dx * d];
  });
}

export { lineMat };
