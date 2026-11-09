// Gebouwen, bomen en hekken: lekker simpel, in de FreeCAD-look (vlak, zwarte randen).
import * as THREE from 'three';
import { color } from '../../theme.js';
import { mat, solid, lineMat } from './materials.js';

function roofColor(kind) {
  if (kind === 'house') return color.roof_house;
  if (kind === 'silo' || kind === 'wall') return color.concrete;
  return color.roof_barn;
}

function wallColor(kind) {
  return kind === 'silo' || kind === 'wall' ? color.concrete : color.wall;
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
  const e = 0.25 / (len / 2);   // dakoverstek 25 cm
  const a = P(-1 - e, -1.06, z0 - 0.15), bb = P(1 + e, -1.06, z0 - 0.15), c = P(1 + e, 1.06, z0 - 0.15),
    d = P(-1 - e, 1.06, z0 - 0.15), r0 = P(-1 - e, 0, z1), r1 = P(1 + e, 0, z1);
  const roofPos = [...a, ...bb, ...r1, ...a, ...r1, ...r0, ...c, ...d, ...r0, ...c, ...r0, ...r1];
  const roof = new THREE.BufferGeometry();
  roof.setAttribute('position', new THREE.Float32BufferAttribute(roofPos, 3));
  roof.computeVertexNormals();
  // Gevels (driehoeken op de korte zijden)
  const g0 = P(-1, -1, z0), g1 = P(-1, 1, z0), g2 = P(-1, 0, z1 - 0.1);
  const g3 = P(1, -1, z0), g4 = P(1, 1, z0), g5 = P(1, 0, z1 - 0.1);
  const gable = new THREE.BufferGeometry();
  gable.setAttribute('position', new THREE.Float32BufferAttribute([...g0, ...g2, ...g1, ...g3, ...g4, ...g5], 3));
  gable.computeVertexNormals();
  const group = new THREE.Group();
  group.add(solid(roof, roofColor(b.kind), { side: THREE.DoubleSide, threshold: 10 }));
  group.add(solid(gable, wallColor(b.kind), { side: THREE.DoubleSide, threshold: 10 }));
  return group;
}

export function building(b) {
  const shape = new THREE.Shape(b.poly.map(([x, y]) => new THREE.Vector2(x, y)));
  const wallH = b.height + 0.2;     // 20 cm de grond in, zodat een helling geen kier geeft
  const g = new THREE.ExtrudeGeometry(shape, { depth: wallH, bevelEnabled: false, curveSegments: 1 });
  g.translate(0, 0, b.base - 0.2);
  const group = new THREE.Group();
  group.name = `building_${b.id}`;
  group.userData = { kind: 'building', id: b.id, name: b.name };
  group.add(solid(g, wallColor(b.kind), { threshold: 35 }));
  const top = b.base + b.height;
  if (b.roof.type === 'gable' && b.poly.length === 4) {
    group.add(gableRoof(b));
  } else if (b.roof.type === 'cone') {
    const cx = b.poly.reduce((s, p) => s + p[0], 0) / b.poly.length;
    const cy = b.poly.reduce((s, p) => s + p[1], 0) / b.poly.length;
    const r = Math.hypot(b.poly[0][0] - cx, b.poly[0][1] - cy);
    const cg = new THREE.ConeGeometry(r * 1.05, (b.roof.ridge ?? top + r) - top, b.poly.length);
    cg.rotateX(Math.PI / 2);
    cg.translate(cx, cy, top + ((b.roof.ridge ?? top + r) - top) / 2);
    group.add(solid(cg, roofColor(b.kind), { threshold: 40 }));
  } else {
    // plat dak: dunne dakrand
    const rg = new THREE.ExtrudeGeometry(shape, { depth: 0.12, bevelEnabled: false });
    rg.translate(0, 0, top);
    group.add(solid(rg, b.kind === 'house' ? color.roof_house : color.roof_barn, { threshold: 35 }));
  }
  return group;
}

export function trees(list) {
  // [x, y, z, hoogte, kegel]
  const group = new THREE.Group();
  group.name = 'trees';
  const trunkG = new THREE.CylinderGeometry(0.12, 0.18, 1, 6);
  trunkG.rotateX(Math.PI / 2);
  trunkG.translate(0, 0, 0.5);
  const ballG = new THREE.IcosahedronGeometry(1, 1);
  const coneG = new THREE.ConeGeometry(1, 1, 8);
  coneG.rotateX(Math.PI / 2);
  const balls = list.filter((t) => !t[4]), cones = list.filter((t) => t[4]);
  const trunk = new THREE.InstancedMesh(trunkG, mat(color.trunk), list.length);
  const ball = new THREE.InstancedMesh(ballG, flatMat(color.crown), Math.max(1, balls.length));
  const cone = new THREE.InstancedMesh(coneG, flatMat(color.crown_dark), Math.max(1, cones.length));
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  list.forEach(([x, y, z, h], i) => {
    const th = h * 0.35;
    trunk.setMatrixAt(i, m.compose(p.set(x, y, z), q, s.set(Math.max(0.6, h / 12), Math.max(0.6, h / 12), th)));
  });
  balls.forEach(([x, y, z, h], i) => {
    const r = Math.max(0.8, h * 0.33);
    ball.setMatrixAt(i, m.compose(p.set(x, y, z + h - r), q, s.set(r, r, r * 0.95)));
  });
  cones.forEach(([x, y, z, h], i) => {
    const r = Math.max(0.6, h * 0.2), ch = h * 0.75;
    cone.setMatrixAt(i, m.compose(p.set(x, y, z + h - ch / 2), q, s.set(r, r, ch)));
  });
  ball.count = balls.length;
  cone.count = cones.length;
  group.add(trunk, ball, cone);
  return group;
}

function flatMat(c) {
  const m = mat(c).clone();
  m.flatShading = true;
  return m;
}

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
  const pg = new THREE.BoxGeometry(0.1, 0.1, 1);
  pg.translate(0, 0, 0.5);
  const inst = new THREE.InstancedMesh(pg, mat(color.post), Math.max(1, posts.length));
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  posts.forEach(([x, y, z, h], i) => inst.setMatrixAt(i, m.compose(p.set(x, y, z - 0.1), q, s.set(1, 1, h + 0.15))));
  inst.count = posts.length;
  group.add(inst, ...lines);
  return group;
}
