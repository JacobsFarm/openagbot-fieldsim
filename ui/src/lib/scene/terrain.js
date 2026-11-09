// Terrein en alles wat erop gedrapeerd wordt (velden, wegen, sloten, sporen).
import * as THREE from 'three';
import { mat, lineMat } from './materials.js';

export class Heightfield {
  constructor(header, heights) {
    Object.assign(this, header);
    this.z = heights;
  }

  at(x, y) {
    const fx = (x - this.x0) / this.cell, fy = (y - this.y0) / this.cell;
    const ix = Math.min(Math.max(Math.floor(fx), 0), this.nx - 2);
    const iy = Math.min(Math.max(Math.floor(fy), 0), this.ny - 2);
    const tx = Math.min(Math.max(fx - ix, 0), 1), ty = Math.min(Math.max(fy - iy, 0), 1);
    const z = this.z, nx = this.nx;
    const a = z[iy * nx + ix] + (z[iy * nx + ix + 1] - z[iy * nx + ix]) * tx;
    const b = z[(iy + 1) * nx + ix] + (z[(iy + 1) * nx + ix + 1] - z[(iy + 1) * nx + ix]) * tx;
    return a + (b - a) * ty;
  }
}

export function terrainMesh(hf, color) {
  const { nx, ny, x0, y0, cell, z } = hf;
  const pos = new Float32Array(nx * ny * 3);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const k = (j * nx + i) * 3;
      pos[k] = x0 + i * cell;
      pos[k + 1] = y0 + j * cell;
      pos[k + 2] = z[j * nx + i];
    }
  }
  const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
  let n = 0;
  for (let j = 0; j < ny - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      idx[n++] = a; idx[n++] = b; idx[n++] = d;
      idx[n++] = a; idx[n++] = d; idx[n++] = c;
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat(color));
  m.name = 'terrain';
  return m;
}

function densify(pts, maxSeg, closed) {
  const out = [];
  const n = pts.length;
  const last = closed ? n : n - 1;
  for (let i = 0; i < last; i++) {
    const [ax, ay] = pts[i], [bx, by] = pts[(i + 1) % n];
    const steps = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / maxSeg));
    for (let s = 0; s < steps; s++) out.push([ax + (bx - ax) * s / steps, ay + (by - ay) * s / steps]);
  }
  if (!closed) out.push(pts[n - 1]);
  return out;
}

// Veelhoek als vlak dat het reliëf volgt: triangulatie (earcut) en dan driehoeken opdelen tot maxEdge.
export function drapePolygon(poly, hf, color, { offset = 0.03, maxEdge = 4, layer = 1, opacity, z = null } = {}) {
  const contour = densify(poly, maxEdge, true).map(([x, y]) => new THREE.Vector2(x, y));
  const tris = THREE.ShapeUtils.triangulateShape(contour, []);
  const out = [];
  const push = (a, b, c, depth) => {
    const ab = Math.hypot(a.x - b.x, a.y - b.y), bc = Math.hypot(b.x - c.x, b.y - c.y);
    const ca = Math.hypot(c.x - a.x, c.y - a.y);
    if (depth < 8 && Math.max(ab, bc, ca) > maxEdge) {
      const m1 = a.clone().add(b).multiplyScalar(0.5), m2 = b.clone().add(c).multiplyScalar(0.5);
      const m3 = c.clone().add(a).multiplyScalar(0.5);
      push(a, m1, m3, depth + 1); push(m1, b, m2, depth + 1); push(m3, m2, c, depth + 1); push(m1, m2, m3, depth + 1);
      return;
    }
    for (const p of [a, b, c]) out.push(p.x, p.y, z ?? hf.at(p.x, p.y) + offset);
  };
  for (const [i, j, k] of tris) push(contour[i], contour[j], contour[k], 0);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
  g.computeVertexNormals();
  return new THREE.Mesh(g, mat(color, { layer, opacity, side: THREE.DoubleSide }));
}

// Lijn over het terrein (omtrek van een veld, sloothart, gepland pad)
export function drapeLine(pts, hf, { color = null, closed = false, offset = 0.06, maxSeg = 2, dashed = false,
  z = null } = {}) {
  const d = densify(pts, maxSeg, closed);
  if (closed) d.push(d[0]);
  const arr = [];
  for (const [x, y] of d) arr.push(x, y, z ?? hf.at(x, y) + offset);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
  const line = new THREE.Line(g, lineMat(color, { dashed }));
  if (dashed) line.computeLineDistances();
  line.raycast = () => {};
  return line;
}

// Strook met breedte langs een lijn (water in een sloot, bewerkte strook achter de robot)
export function drapeStrip(pts, width, hf, color, { offset = 0.04, z = null, layer = 2, maxSeg = 2 } = {}) {
  const d = densify(pts, maxSeg, false);
  if (d.length < 2) return null;
  const pos = [];
  const idx = [];
  for (let i = 0; i < d.length; i++) {
    const [x, y] = d[i];
    const [px, py] = d[Math.max(0, i - 1)], [qx, qy] = d[Math.min(d.length - 1, i + 1)];
    let dx = qx - px, dy = qy - py;
    const l = Math.hypot(dx, dy) || 1;
    dx /= l; dy /= l;
    const rx = dy * width / 2, ry = -dx * width / 2;
    pos.push(x - rx, y - ry, z ?? hf.at(x - rx, y - ry) + offset, x + rx, y + ry, z ?? hf.at(x + rx, y + ry) + offset);
    if (i > 0) {
      const a = (i - 1) * 2;
      idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return new THREE.Mesh(g, mat(color, { layer, side: THREE.DoubleSide }));
}
