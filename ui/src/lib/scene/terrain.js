// Terrein en alles wat erop ligt: velden, paden, sloten met oevers, linten (grenzen, banen) en stroken.
import * as THREE from 'three';
import { biasPatch, groundMat, lineMat, ORDER, ribbonMat, waterMat } from './materials.js';
import { textures } from './textures.js';

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

  // Normaal van het reliëf (voor alles wat erop gedrapeerd ligt: zelfde belichting als het terrein eronder)
  normal(x, y, out, k = 0) {
    const e = this.cell;
    const dx = (this.at(x + e, y) - this.at(x - e, y)) / (2 * e), dy = (this.at(x, y + e) - this.at(x, y - e)) / (2 * e);
    const l = Math.hypot(dx, dy, 1);
    out[k] = -dx / l; out[k + 1] = -dy / l; out[k + 2] = 1 / l;
  }
}

// Het terrein zelf. `zShow` = hoogtes voor het beeld (onder water en oevers verlaagd, zie waterBodies).
export function terrainMesh(hf, zShow = hf.z) {
  const { nx, ny, x0, y0, cell } = hf;
  const pos = new Float32Array(nx * ny * 3);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const k = (j * nx + i) * 3;
      pos[k] = x0 + i * cell;
      pos[k + 1] = y0 + j * cell;
      pos[k + 2] = zShow[j * nx + i];
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
  const m = new THREE.Mesh(g, groundMat(textures().grass, { bias: 0 }));
  m.name = 'terrain';
  m.receiveShadow = true;
  m.renderOrder = ORDER.terrain;
  return m;
}

// Grond tot aan de horizon, zodat de wereld niet in het niets ophoudt
export function horizon(hf) {
  const { nx, ny, x0, y0, cell, z } = hf;
  let sum = 0, n = 0;
  for (let i = 0; i < nx; i += 8) { sum += z[i] + z[(ny - 1) * nx + i]; n += 2; }
  for (let j = 0; j < ny; j += 8) { sum += z[j * nx] + z[j * nx + nx - 1]; n += 2; }
  // Een vlak met een gat waar het terrein ligt: onder het terrein mag niets zitten, anders steekt het door de sloten
  const x1 = x0 + (nx - 1) * cell, y1 = y0 + (ny - 1) * cell, far = 16000, e = cell * 0.5;
  const shape = new THREE.Shape([new THREE.Vector2(x0 - far, y0 - far), new THREE.Vector2(x1 + far, y0 - far),
    new THREE.Vector2(x1 + far, y1 + far), new THREE.Vector2(x0 - far, y1 + far)]);
  shape.holes.push(new THREE.Path([new THREE.Vector2(x0 + e, y0 + e), new THREE.Vector2(x0 + e, y1 - e),
    new THREE.Vector2(x1 - e, y1 - e), new THREE.Vector2(x1 - e, y0 + e)]));
  const g = new THREE.ShapeGeometry(shape);
  const m = new THREE.Mesh(g, groundMat(textures().grass, { bias: 0, tint: [0.96, 0.98, 0.94] }));
  m.position.set(0, 0, sum / n - 0.06);
  m.receiveShadow = true;
  m.renderOrder = ORDER.terrain - 1;
  m.raycast = () => {};
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

function geometry(pos, idx = null, extra = {}) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  for (const [name, [arr, size]] of Object.entries(extra)) g.setAttribute(name, new THREE.Float32BufferAttribute(arr, size));
  if (idx) g.setIndex(idx);
  return g;
}

// Driehoeken van een veelhoek (earcut), opgedeeld tot maxEdge; roept emit(x, y) per hoekpunt aan
function triangulate(poly, maxEdge, emit) {
  const contour = poly.map(([x, y]) => new THREE.Vector2(x, y));
  if (contour.length < 3) return;
  const tris = THREE.ShapeUtils.triangulateShape(contour, []);
  const max2 = maxEdge * maxEdge * 1.21;
  // Steeds de langste zijde halveren: ook een lange smalle driehoek (earcut maakt waaiers) geeft dan een aantal
  // driehoeken naar oppervlak, niet 4^n splinters. Een gedeelde zijde wordt aan beide kanten op dezelfde punten
  // gesplitst, dus er komen geen kieren.
  const push = (ax, ay, bx, by, cx, cy, depth) => {
    const ab = (ax - bx) ** 2 + (ay - by) ** 2, bc = (bx - cx) ** 2 + (by - cy) ** 2, ca = (cx - ax) ** 2 + (cy - ay) ** 2;
    if (depth < 30 && Math.max(ab, bc, ca) > max2) {
      if (ab >= bc && ab >= ca) {
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        push(ax, ay, mx, my, cx, cy, depth + 1); push(mx, my, bx, by, cx, cy, depth + 1);
      } else if (bc >= ca) {
        const mx = (bx + cx) / 2, my = (by + cy) / 2;
        push(ax, ay, bx, by, mx, my, depth + 1); push(ax, ay, mx, my, cx, cy, depth + 1);
      } else {
        const mx = (cx + ax) / 2, my = (cy + ay) / 2;
        push(ax, ay, bx, by, mx, my, depth + 1); push(mx, my, bx, by, cx, cy, depth + 1);
      }
      return;
    }
    const area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
    if (Math.abs(area) < 1e-6) return;
    emit(ax, ay);
    if (area > 0) { emit(bx, by); emit(cx, cy); } else { emit(cx, cy); emit(bx, by); }
  };
  for (const [i, j, k] of tris) {
    push(contour[i].x, contour[i].y, contour[j].x, contour[j].y, contour[k].x, contour[k].y, 0);
  }
}

// Veelhoek als vlak dat het reliëf volgt (decal: zie materials.js). z = vaste hoogte (water).
export function drapePolygon(poly, hf, material, { offset = 0.02, maxEdge = 3, order = ORDER.surface, z = null } = {}) {
  const out = [], nor = [];
  triangulate(poly, maxEdge, (x, y) => {
    out.push(x, y, z ?? hf.at(x, y) + offset);
    if (z === null) hf.normal(x, y, nor, nor.length); else nor.push(0, 0, 1);
  });
  const m = new THREE.Mesh(geometry(out, null, { normal: [nor, 3] }), material);
  m.receiveShadow = true;
  m.renderOrder = order;
  return m;
}

// Dunne lijn over het terrein (hulplijnen); voor alles wat op afstand zichtbaar moet blijven: ribbon()
export function drapeLine(pts, hf, { color = null, closed = false, offset = 0.06, maxSeg = 2, dashed = false,
  z = null } = {}) {
  const d = densify(pts, maxSeg, closed);
  if (closed) d.push(d[0]);
  const arr = [];
  for (const [x, y] of d) arr.push(x, y, z ?? hf.at(x, y) + offset);
  const line = new THREE.Line(geometry(arr), lineMat(color, { dashed }));
  if (dashed) line.computeLineDistances();
  line.raycast = () => {};
  return line;
}

// Lint over het terrein: op afstand blijft het een paar beeldpunten breed (perceelgrens, baan, route)
export function ribbon(pts, hf, color, { closed = false, offset = 0.05, maxSeg = 3, width = 0.3, px = 1.6, opacity = 1,
  dash = 0, order = ORDER.mark, z = null } = {}) {
  const d = densify(pts, maxSeg, closed);
  if (closed) d.push(d[0]);
  const n = d.length;
  if (n < 2) return null;
  const pos = [], side = [], dist = [], idx = [];
  let run = 0;
  for (let i = 0; i < n; i++) {
    const [x, y] = d[i];
    const p = d[i > 0 ? i - 1 : (closed ? n - 2 : 0)], q = d[i < n - 1 ? i + 1 : (closed ? 1 : n - 1)];
    let dx = q[0] - p[0], dy = q[1] - p[1];
    const l = Math.hypot(dx, dy) || 1;
    dx /= l; dy /= l;
    if (i > 0) run += Math.hypot(x - d[i - 1][0], y - d[i - 1][1]);
    const zz = z ?? hf.at(x, y) + offset;
    pos.push(x, y, zz, x, y, zz);
    side.push(dy, -dx, -dy, dx);
    dist.push(run, run);
    if (i > 0) {
      const a = (i - 1) * 2;
      idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
    }
  }
  const m = new THREE.Mesh(geometry(pos, idx, { aSide: [side, 2], aDist: [dist, 1] }),
    ribbonMat(color, { width, px, opacity, dash }));
  m.renderOrder = order;
  m.frustumCulled = false;
  m.raycast = () => {};
  return m;
}

// Strook met vaste breedte langs een lijn (bewerkte strook achter de robot, spoor)
export function drapeStrip(pts, width, hf, material, { offset = 0.04, z = null, order = ORDER.overlay, maxSeg = 2 } = {}) {
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
  const g = geometry(pos, idx);
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, material);
  m.receiveShadow = true;
  m.renderOrder = order;
  m.raycast = () => {};
  return m;
}

// Vlak materiaal voor stroken en markeringen op de grond (decal)
const flatCache = new Map();
export function flatDecal(color, rough = 0.9) {
  const key = color.join(',');
  if (!flatCache.has(key)) {
    const m = new THREE.MeshStandardMaterial({ color: new THREE.Color().setRGB(color[0], color[1], color[2], THREE.SRGBColorSpace),
      roughness: rough, side: THREE.DoubleSide });
    flatCache.set(key, biasPatch(m, 0.3));
  }
  return flatCache.get(key);
}

// ------------------------------------------------------------------ water met oevers
// Hartlijn met breedte -> veelhoek (linksom), met rechte koppen
export function bufferLine(line, half) {
  const left = [], right = [];
  for (let i = 0; i < line.length; i++) {
    const a = line[Math.max(0, i - 1)], b = line[Math.min(line.length - 1, i + 1)];
    let dx = b[0] - a[0], dy = b[1] - a[1];
    const l = Math.hypot(dx, dy) || 1;
    dx /= l; dy /= l;
    // verstek in de knik: de strook blijft overal even breed
    let k = 1;
    if (i > 0 && i < line.length - 1) {
      const p = line[i - 1], q = line[i + 1], c = line[i];
      const l1 = Math.hypot(c[0] - p[0], c[1] - p[1]) || 1, l2 = Math.hypot(q[0] - c[0], q[1] - c[1]) || 1;
      const cos = ((c[0] - p[0]) * (q[0] - c[0]) + (c[1] - p[1]) * (q[1] - c[1])) / (l1 * l2);
      k = Math.min(2.5, 1 / Math.sqrt(Math.max(0.16, (1 + cos) / 2)));
    }
    right.push([line[i][0] + dy * half * k, line[i][1] - dx * half * k]);
    left.push([line[i][0] - dy * half * k, line[i][1] + dx * half * k]);
  }
  return [...right, ...left.reverse()];
}

function signedArea(p) {
  let a = 0;
  for (let i = 0; i < p.length; i++) { const q = p[(i + 1) % p.length]; a += p[i][0] * q[1] - q[0] * p[i][1]; }
  return a / 2;
}

// Naar buiten gerichte normaal per hoekpunt (met verstek, begrensd) van een gesloten ring
function outward(ring) {
  const n = ring.length, s = signedArea(ring) >= 0 ? 1 : -1;
  return ring.map((p, i) => {
    const a = ring[(i + n - 1) % n], b = ring[(i + 1) % n];
    let e1x = p[0] - a[0], e1y = p[1] - a[1], e2x = b[0] - p[0], e2y = b[1] - p[1];
    const l1 = Math.hypot(e1x, e1y) || 1, l2 = Math.hypot(e2x, e2y) || 1;
    e1x /= l1; e1y /= l1; e2x /= l2; e2y /= l2;
    let nx = (e1y + e2y) * s, ny = -(e1x + e2x) * s;
    const l = Math.hypot(nx, ny);
    if (l < 1e-6) return [e1y * s, -e1x * s];
    nx /= l; ny /= l;
    const k = Math.min(2.2, 1 / Math.max(0.3, nx * e1y * s - ny * e1x * s));
    return [nx * k, ny * k];
  });
}

/**
 * Alle wateren van een wereld in één keer: wateroppervlak op peil, een donkere oever (talud) van de waterlijn naar
 * het maaiveld en een strook gewoon gras erachter. Onder dat alles wordt het terrein in beeld verlaagd, zodat er
 * nooit grond door het water steekt: de sloot is precies zo breed als in de kaart, ook als hij smaller is dan
 * een cel van het hoogteraster.
 *
 * bodies: [{ poly, level, bank }]   bank = breedte van het talud (m)
 * Geeft { group, zShow } terug; zShow zijn de hoogtes voor terrainMesh.
 */
export function waterBodies(bodies, hf) {
  const group = new THREE.Group();
  const zShow = new Float32Array(hf.z);
  if (!bodies.length) return { group, zShow };
  const { nx, ny, x0, y0, cell } = hf;
  const shoulderW = cell * 3 + 0.4;

  // 1. Masker: welke roosterpunten liggen onder water of oever (+ marge)? Getekend op een canvas, want dat vult
  //    holle veelhoeken met duizend punten in een paar milliseconden.
  const ss = Math.max(1, Math.min(2, Math.floor(4096 / Math.max(nx, ny))));
  const c = document.createElement('canvas');
  c.width = nx * ss; c.height = ny * ss;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  const k = ss / cell;
  ctx.lineJoin = 'round';
  bodies.forEach((b, i) => {
    const id = i + 1;
    ctx.fillStyle = ctx.strokeStyle = `rgb(${id & 255},${(id >> 8) & 255},7)`;
    ctx.lineWidth = (b.bank + cell * 1.5) * 2 * k;
    ctx.beginPath();
    b.poly.forEach(([x, y], j) => {
      const px = (x - x0) * k + 0.5, py = (y - y0) * k + 0.5;
      if (j) ctx.lineTo(px, py); else ctx.moveTo(px, py);
    });
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  });
  const img = ctx.getImageData(0, 0, c.width, c.height).data;
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const p = ((j * ss) * c.width + i * ss) * 4;
      if (img[p + 3] < 250 || img[p + 2] !== 7) continue;
      const b = bodies[(img[p] | (img[p + 1] << 8)) - 1];
      if (b) zShow[j * nx + i] = Math.min(zShow[j * nx + i], b.level - 0.6);
    }
  }

  // 2. Geometrie: water, talud en schouder, alles samengevoegd tot drie meshes
  const wPos = [], wUv = [], bPos = [], sPos = [];
  const quad = (arr, a, b, c2, d) => arr.push(...a, ...b, ...c2, ...a, ...c2, ...d);
  for (const b of bodies) {
    triangulate(b.poly, 40, (x, y) => { wPos.push(x, y, b.level); wUv.push(x / 9, y / 9); });
    const ring = densify(b.poly, 2.5, true);
    const out = outward(ring);
    const n = ring.length;
    const P = [], Q = [], R = [];
    for (let i = 0; i < n; i++) {
      const [x, y] = ring[i], [ox, oy] = out[i];
      const qx = x + ox * b.bank, qy = y + oy * b.bank;
      const rx = x + ox * (b.bank + shoulderW), ry = y + oy * (b.bank + shoulderW);
      const floor = b.level + 0.12;
      P.push([x, y, b.level + 0.004]);
      Q.push([qx, qy, Math.max(hf.at(qx, qy), floor) + 0.02]);
      R.push([rx, ry, Math.max(hf.at(rx, ry), floor) + 0.02]);
    }
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      quad(bPos, P[i], Q[i], Q[j], P[j]);
      quad(sPos, Q[i], R[i], R[j], Q[j]);
    }
  }
  const t = textures();
  const add = (pos, material, order, uv = null) => {
    const g = geometry(pos, null, uv ? { uv: [uv, 2] } : {});
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, material);
    m.receiveShadow = true;
    m.renderOrder = order;
    m.raycast = () => {};
    group.add(m);
    return m;
  };
  add(sPos, groundMat(t.grass, { bias: 0.2 }), ORDER.terrain + 1).name = 'shoulder';
  add(bPos, groundMat(t.bank, { bias: 0.22, macroAmount: 0.2 }), ORDER.bank).name = 'bank';
  add(wPos, waterMat(), ORDER.water, wUv).name = 'water';
  return { group, zShow };
}
