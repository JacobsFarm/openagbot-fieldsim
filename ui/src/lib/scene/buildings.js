// Gebouwen, bomen en hekken in de sim-stijl: eenvoudige vormen, maar met materiaal (baksteen, profielplaat,
// dakpannen), schaduw en wat variatie per boom.
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { color } from '../../theme.js';
import { mat, lineMat } from './materials.js';
import { textures } from './textures.js';

const lib = {};

// Materiaal met een textuur in meters (de uv's van de muren en daken zijn in meters)
function textured(name, mapName, metersU, metersV, opts = {}) {
  if (!lib[name]) {
    const map = textures()[mapName];
    map.repeat.set(1 / metersU, 1 / metersV);
    lib[name] = new THREE.MeshStandardMaterial({ map, roughness: opts.rough ?? 0.85, metalness: opts.metal ?? 0,
      side: opts.side ?? THREE.FrontSide });
  }
  return lib[name];
}

function wallMat(kind) {
  if (kind === 'house') return textured('brick', 'wallBrick', 2.5, 2.0);
  if (kind === 'silo' || kind === 'wall') return textured('concrete', 'wallConcrete', 6, 6);
  if (kind === 'barn') return textured('sheetGreen', 'wallSheet', 3.2, 3.2, { rough: 0.6, metal: 0.2 });
  return textured('sheetLight', 'wallLight', 3.2, 3.2, { rough: 0.6, metal: 0.2 });
}

function roofMat(kind) {
  if (kind === 'house') return textured('tiles', 'roofTiles', 7.2, 5.3, { side: THREE.DoubleSide });
  return textured('roofSheet', 'roofSheet', 4, 4, { rough: 0.55, metal: 0.35, side: THREE.DoubleSide });
}

function shadowed(mesh) {
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

// Rechthoekige footprint: middelpunt, lange as, afmetingen
function rectFrame(poly) {
  const [p0, p1, p2] = poly;
  const ax = [p1[0] - p0[0], p1[1] - p0[1]], ay = [p2[0] - p1[0], p2[1] - p1[1]];
  const la = Math.hypot(...ax), lb = Math.hypot(...ay);
  const cx = poly.reduce((s, p) => s + p[0], 0) / poly.length, cy = poly.reduce((s, p) => s + p[1], 0) / poly.length;
  return la >= lb
    ? { cx, cy, long: [ax[0] / la, ax[1] / la], short: [ay[0] / lb, ay[1] / lb], L: la, W: lb }
    : { cx, cy, long: [ay[0] / lb, ay[1] / lb], short: [ax[0] / la, ax[1] / la], L: lb, W: la };
}

function gableRoof(b) {
  const f = rectFrame(b.poly);
  const alongLong = b.roof.axis !== 'short';
  const dir = alongLong ? f.long : f.short, side = alongLong ? f.short : f.long;
  const len = alongLong ? f.L : f.W, wid = alongLong ? f.W : f.L;
  const z0 = b.base + b.height, z1 = b.base + (b.roof.ridge ?? b.height + wid * 0.35);
  const P = (s, t, z) => [f.cx + dir[0] * s * len / 2 + side[0] * t * wid / 2, f.cy + dir[1] * s * len / 2 + side[1] * t * wid / 2, z];
  const e = 0.35 / (len / 2);   // dakoverstek 35 cm
  const a = P(-1 - e, -1.08, z0 - 0.18), bb = P(1 + e, -1.08, z0 - 0.18), c = P(1 + e, 1.08, z0 - 0.18),
    d = P(-1 - e, 1.08, z0 - 0.18), r0 = P(-1 - e, 0, z1), r1 = P(1 + e, 0, z1);
  const slope = Math.hypot(wid * 0.54, z1 - z0 + 0.18), u1 = len * (1 + e);
  const roof = new THREE.BufferGeometry();
  roof.setAttribute('position', new THREE.Float32BufferAttribute(
    [...a, ...bb, ...r1, ...a, ...r1, ...r0, ...c, ...d, ...r0, ...c, ...r0, ...r1], 3));
  roof.setAttribute('uv', new THREE.Float32BufferAttribute(
    [0, 0, u1, 0, u1, slope, 0, 0, u1, slope, 0, slope, 0, 0, u1, 0, u1, slope, 0, 0, u1, slope, 0, slope], 2));
  roof.computeVertexNormals();
  // Gevels (driehoeken op de korte zijden)
  const g0 = P(-1, -1, z0), g1 = P(-1, 1, z0), g2 = P(-1, 0, z1 - 0.1);
  const g3 = P(1, -1, z0), g4 = P(1, 1, z0), g5 = P(1, 0, z1 - 0.1);
  const gable = new THREE.BufferGeometry();
  gable.setAttribute('position', new THREE.Float32BufferAttribute([...g0, ...g2, ...g1, ...g3, ...g4, ...g5], 3));
  const h = z1 - 0.1 - z0;
  gable.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, wid / 2, h, wid, 0, 0, 0, wid, 0, wid / 2, h], 2));
  gable.computeVertexNormals();
  const wm = wallMat(b.kind).clone();
  wm.side = THREE.DoubleSide;
  const group = new THREE.Group();
  group.add(shadowed(new THREE.Mesh(roof, roofMat(b.kind))), shadowed(new THREE.Mesh(gable, wm)));
  return group;
}

export function building(b) {
  const shape = new THREE.Shape(b.poly.map(([x, y]) => new THREE.Vector2(x, y)));
  const wallH = b.height + 0.4;     // 40 cm de grond in, zodat een helling geen kier geeft
  const g = new THREE.ExtrudeGeometry(shape, { depth: wallH, bevelEnabled: false, curveSegments: 1 });
  g.translate(0, 0, b.base - 0.4);
  const group = new THREE.Group();
  group.name = `building_${b.id}`;
  group.userData = { kind: 'building', id: b.id, name: b.name };
  group.add(shadowed(new THREE.Mesh(g, wallMat(b.kind))));
  const top = b.base + b.height;
  if (b.roof.type === 'gable' && b.poly.length === 4) {
    group.add(gableRoof(b));
  } else if (b.roof.type === 'cone') {
    const cx = b.poly.reduce((s, p) => s + p[0], 0) / b.poly.length;
    const cy = b.poly.reduce((s, p) => s + p[1], 0) / b.poly.length;
    const r = Math.hypot(b.poly[0][0] - cx, b.poly[0][1] - cy);
    const cg = new THREE.ConeGeometry(r * 1.05, (b.roof.ridge ?? top + r) - top, Math.max(12, b.poly.length));
    cg.rotateX(Math.PI / 2);
    cg.translate(cx, cy, top + ((b.roof.ridge ?? top + r) - top) / 2);
    group.add(shadowed(new THREE.Mesh(cg, mat(color.roof_barn, { rough: 0.5, metal: 0.4 }))));
  } else {
    // plat dak: dunne dakrand die iets oversteekt
    const rg = new THREE.ExtrudeGeometry(shape, { depth: 0.16, bevelEnabled: false });
    rg.translate(0, 0, top);
    const flat = b.kind === 'house' ? color.roof_house : b.kind === 'silo' || b.kind === 'wall' ? color.concrete : color.roof_barn;
    group.add(shadowed(new THREE.Mesh(rg, mat(flat, { rough: 0.8 }))));
  }
  return group;
}

// ------------------------------------------------------------------ bomen
function hash(i) {
  const s = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}

// Kroon uit een paar verschoven bollen; hoekpunten krijgen een kleur (onder donker, boven licht) voor diepte
function crownGeometry() {
  const parts = [[0, 0, 0, 1.0], [0.55, 0.2, -0.15, 0.68], [-0.5, 0.3, -0.1, 0.7], [0.1, -0.55, -0.2, 0.66],
    [-0.15, 0.1, 0.55, 0.62]].map(([x, y, z, r]) => {
    const g = new THREE.IcosahedronGeometry(r, 2);
    g.translate(x, y, z);
    return g;
  });
  return shade(mergeGeometries(parts), -1.0, 1.1);
}

function coneGeometry() {
  const parts = [[0.0, 1.0, 0.55], [0.3, 0.8, 0.5], [0.58, 0.55, 0.42]].map(([z, r, h]) => {
    const g = new THREE.ConeGeometry(r, h, 10);
    g.rotateX(Math.PI / 2);
    g.translate(0, 0, z - 0.5 + h / 2);
    return g;
  });
  return shade(mergeGeometries(parts), -0.5, 0.5);
}

function shade(g, z0, z1) {
  const p = g.attributes.position, col = new Float32Array(p.count * 3);
  for (let i = 0; i < p.count; i++) {
    const t = THREE.MathUtils.clamp((p.getZ(i) - z0) / (z1 - z0), 0, 1);
    const jitter = 0.94 + hash(i * 3.7) * 0.12;
    const v = (0.62 + 0.5 * t) * jitter;
    col[i * 3] = v; col[i * 3 + 1] = v; col[i * 3 + 2] = v;
  }
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.computeVertexNormals();
  return g;
}

export function trees(list) {
  // [x, y, z, hoogte, kegel]
  const group = new THREE.Group();
  group.name = 'trees';
  if (!list.length) return group;
  const trunkG = new THREE.CylinderGeometry(0.1, 0.19, 1, 7);
  trunkG.rotateX(Math.PI / 2);
  trunkG.translate(0, 0, 0.5);
  const balls = list.filter((t) => !t[4]), cones = list.filter((t) => t[4]);
  const leaf = new THREE.MeshStandardMaterial({ color: 0xffffff, vertexColors: true, roughness: 0.92 });
  const trunk = new THREE.InstancedMesh(trunkG, mat(color.trunk, { rough: 0.95 }), list.length);
  const ball = new THREE.InstancedMesh(crownGeometry(), leaf, Math.max(1, balls.length));
  const cone = new THREE.InstancedMesh(coneGeometry(), leaf, Math.max(1, cones.length));
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const up = new THREE.Vector3(0, 0, 1), c = new THREE.Color();
  const tint = (base, i) => c.setRGB(base[0] * (0.82 + hash(i) * 0.36), base[1] * (0.86 + hash(i + 9) * 0.28),
    base[2] * (0.8 + hash(i + 17) * 0.4), THREE.SRGBColorSpace);
  list.forEach(([x, y, z, h], i) => {
    const th = h * 0.42, w = Math.max(0.6, h / 11);
    trunk.setMatrixAt(i, m.compose(p.set(x, y, z - 0.1), q.identity(), s.set(w, w, th)));
  });
  balls.forEach(([x, y, z, h], i) => {
    const r = Math.max(0.8, h * 0.34) * (0.9 + hash(i + 3) * 0.2);
    q.setFromAxisAngle(up, hash(i + 1) * Math.PI * 2);
    ball.setMatrixAt(i, m.compose(p.set(x, y, z + h - r * 0.95), q, s.set(r, r, r * (0.86 + hash(i + 5) * 0.2))));
    ball.setColorAt(i, tint(color.crown, i));
  });
  cones.forEach(([x, y, z, h], i) => {
    const r = Math.max(0.6, h * 0.2), ch = h * 0.8;
    q.setFromAxisAngle(up, hash(i + 2) * Math.PI * 2);
    cone.setMatrixAt(i, m.compose(p.set(x, y, z + h - ch / 2), q, s.set(r, r, ch)));
    cone.setColorAt(i, tint(color.crown_dark, i + 40));
  });
  ball.count = balls.length;
  cone.count = cones.length;
  for (const o of [trunk, ball, cone]) { o.castShadow = true; o.receiveShadow = true; o.frustumCulled = false; }
  group.add(trunk, ball, cone);
  return group;
}

// ------------------------------------------------------------------ hekken
export function fences(list, hf) {
  const group = new THREE.Group();
  group.name = 'fences';
  const posts = [];
  const lines = [];
  for (const f of list) {
    for (let i = 0; i < f.line.length - 1; i++) {
      const [ax, ay] = f.line[i], [bx, by] = f.line[i + 1];
      const len = Math.hypot(bx - ax, by - ay);
      const n = Math.max(1, Math.round(len / f.spacing));
      for (let k = 0; k <= n; k++) {
        if (k === 0 && i > 0) continue;
        const x = ax + (bx - ax) * k / n, y = ay + (by - ay) * k / n;
        posts.push([x, y, hf.at(x, y), f.height]);
      }
    }
    for (const frac of [0.45, 0.75, 0.98]) {
      const arr = [];
      for (let i = 0; i < f.line.length - 1; i++) {
        const [ax, ay] = f.line[i], [bx, by] = f.line[i + 1];
        const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / 2));
        for (let k = 0; k <= n; k++) {
          const x = ax + (bx - ax) * k / n, y = ay + (by - ay) * k / n;
          arr.push(x, y, hf.at(x, y) + f.height * frac);
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
      lines.push(new THREE.Line(g, lineMat(color.wire)));
    }
  }
  const pg = new THREE.CylinderGeometry(0.05, 0.06, 1, 7);
  pg.rotateX(Math.PI / 2);
  pg.translate(0, 0, 0.5);
  const inst = new THREE.InstancedMesh(pg, mat(color.post, { rough: 0.95 }), Math.max(1, posts.length));
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  posts.forEach(([x, y, z, h], i) => inst.setMatrixAt(i, m.compose(p.set(x, y, z - 0.1), q, s.set(1, 1, h + 0.15))));
  inst.count = posts.length;
  inst.castShadow = true;
  inst.frustumCulled = false;
  group.add(inst, ...lines);
  return group;
}
