// Procedurele texturen (canvas): gras, gewassen, grond, verharding, water, daken. Alles naadloos herhaalbaar,
// zodat de sim zonder beeldbestanden werkt (ook in de .exe) en elke soort ondergrond direct te herkennen is.
import * as THREE from 'three';

// ------------------------------------------------------------------ ruis
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Periodieke waarderuis op een rooster van `cells` cellen; uitvoer size x size in 0..1
function valueNoise(size, cells, seed) {
  const r = rng(seed);
  const g = new Float32Array(cells * cells);
  for (let i = 0; i < g.length; i++) g[i] = r();
  const out = new Float32Array(size * size);
  const k = cells / size;
  for (let y = 0; y < size; y++) {
    const fy = y * k, y0 = Math.floor(fy), ty = fy - y0;
    const sy = ty * ty * (3 - 2 * ty);
    const ya = (y0 % cells) * cells, yb = ((y0 + 1) % cells) * cells;
    for (let x = 0; x < size; x++) {
      const fx = x * k, x0 = Math.floor(fx), tx = fx - x0;
      const sx = tx * tx * (3 - 2 * tx);
      const xa = x0 % cells, xb = (x0 + 1) % cells;
      const a = g[ya + xa] + (g[ya + xb] - g[ya + xa]) * sx;
      const b = g[yb + xa] + (g[yb + xb] - g[yb + xa]) * sx;
      out[y * size + x] = a + (b - a) * sy;
    }
  }
  return out;
}

export function fbm(size, cells, octaves, seed) {
  const out = new Float32Array(size * size);
  let amp = 1, total = 0;
  for (let o = 0; o < octaves; o++) {
    const n = valueNoise(size, Math.min(size, cells << o), seed + o * 101);
    for (let i = 0; i < out.length; i++) out[i] += n[i] * amp;
    total += amp;
    amp *= 0.5;
  }
  for (let i = 0; i < out.length; i++) out[i] /= total;
  return out;
}

const clamp = (v) => (v < 0 ? 0 : v > 255 ? 255 : v);
const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

// Canvas vullen met f(x, y, i) -> [r, g, b] (0-255)
function paint(size, f) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = y * size + x;
      const [r, g, b] = f(x, y, i);
      img.data[i * 4] = clamp(r); img.data[i * 4 + 1] = clamp(g); img.data[i * 4 + 2] = clamp(b); img.data[i * 4 + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

function tex(canvas, { srgb = true, aniso = 8 } = {}) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.anisotropy = aniso;
  t.generateMipmaps = true;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  return t;
}

// ------------------------------------------------------------------ ondergronden
// Elke ondergrond: { map, size } met size = meters per herhaling.
const S = 512;

function grass(seed, lo, hi, dry = 0) {
  const a = fbm(S, 6, 4, seed), b = fbm(S, 48, 3, seed + 7), c = fbm(S, 128, 2, seed + 13);
  return paint(S, (x, y, i) => {
    let col = mix(lo, hi, a[i] * 0.55 + b[i] * 0.45);
    const blade = c[i];
    col = mix(col, [col[0] * 0.72, col[1] * 0.78, col[2] * 0.7], blade > 0.6 ? (blade - 0.6) * 1.6 : 0);
    if (dry) col = mix(col, [176, 164, 96], Math.max(0, b[i] - 0.52) * dry);
    return col;
  });
}

function soil(seed, lo, hi, stones = 0) {
  const a = fbm(S, 8, 4, seed), b = fbm(S, 96, 2, seed + 3);
  const r = rng(seed + 99);
  const c = paint(S, (x, y, i) => mix(lo, hi, a[i] * 0.6 + b[i] * 0.4));
  if (stones) {
    const ctx = c.getContext('2d');
    for (let k = 0; k < stones; k++) {
      const v = 120 + r() * 110;
      ctx.fillStyle = `rgba(${v},${v * 0.97},${v * 0.92},${0.35 + r() * 0.5})`;
      const x = r() * S, y = r() * S, w = 1 + r() * 3.2;
      ctx.beginPath(); ctx.ellipse(x, y, w, w * (0.6 + r() * 0.5), r() * 3.14, 0, 6.3); ctx.fill();
    }
  }
  return c;
}

// Gewas in rijen: `rows` rijen per herhaling (langs v), rijen lopen in de u-richting
function crop(seed, rows, soilLo, soilHi, leafLo, leafHi, cover, clumpy = 0.5) {
  const a = fbm(S, 8, 4, seed), b = fbm(S, 64, 3, seed + 5), c = fbm(S, 160, 2, seed + 11);
  return paint(S, (x, y, i) => {
    const earth = mix(soilLo, soilHi, a[i] * 0.5 + b[i] * 0.5);
    const ph = ((y / S) * rows) % 1;
    const d = Math.abs(ph - 0.5) * 2;                    // 0 = hart van de rij, 1 = tussen de rijen
    const width = cover * (1 - clumpy * 0.5 + clumpy * c[i]);
    const t = Math.min(1, Math.max(0, (width - d) / 0.18));
    const leaf = mix(leafLo, leafHi, b[i] * 0.6 + c[i] * 0.4);
    return mix(earth, leaf, t);
  });
}

function asphalt(seed) {
  const a = fbm(S, 6, 3, seed), b = fbm(S, 200, 2, seed + 1);
  return paint(S, (x, y, i) => {
    const v = 58 + a[i] * 22 + (b[i] - 0.5) * 34;
    return [v, v, v * 1.03];
  });
}

function pavers(seed) {
  // betonklinkers in halfsteensverband, 8 x 16 stenen per herhaling
  const a = fbm(S, 8, 3, seed), b = fbm(S, 128, 2, seed + 2);
  const r = rng(seed);
  const tone = Array.from({ length: 16 * 16 }, () => r());
  const bw = S / 8, bh = S / 16;
  return paint(S, (x, y, i) => {
    const row = Math.floor(y / bh);
    const xx = (x + (row % 2) * bw / 2) % S;
    const colI = Math.floor(xx / bw);
    const ex = Math.min(xx % bw, bw - (xx % bw)), ey = Math.min(y % bh, bh - (y % bh));
    const joint = Math.min(ex, ey) < 1.4 ? 0.72 : 1;
    const v = (150 + tone[(row * 8 + colI) % 256] * 30 + (a[i] - 0.5) * 30 + (b[i] - 0.5) * 16) * joint;
    return [v, v * 0.985, v * 0.95];
  });
}

function concrete(seed) {
  const a = fbm(S, 5, 4, seed), b = fbm(S, 180, 2, seed + 4);
  return paint(S, (x, y, i) => {
    const v = 168 + (a[i] - 0.5) * 40 + (b[i] - 0.5) * 18;
    return [v, v, v * 0.98];
  });
}

// Golfplaat of profielplaat: ribbels in de u-richting
function ribbed(seed, base, ribs, depth = 0.22) {
  const a = fbm(S, 6, 3, seed);
  return paint(S, (x, y, i) => {
    const ph = ((x / S) * ribs) % 1;
    const k = 1 - depth + depth * (0.5 + 0.5 * Math.cos(ph * Math.PI * 2));
    const w = 0.93 + a[i] * 0.14;
    return [base[0] * k * w, base[1] * k * w, base[2] * k * w];
  });
}

function bricks(seed, base, mortar) {
  const r = rng(seed);
  const a = fbm(S, 64, 2, seed + 9);
  const rowsN = 32, colsN = 12;
  const tone = Array.from({ length: rowsN * colsN }, () => r());
  const bw = S / colsN, bh = S / rowsN;
  return paint(S, (x, y, i) => {
    const row = Math.floor(y / bh);
    const xx = (x + (row % 2) * bw / 2) % S;
    const colI = Math.floor(xx / bw);
    const ex = Math.min(xx % bw, bw - (xx % bw)), ey = Math.min(y % bh, bh - (y % bh));
    if (Math.min(ex, ey) < 1.3) return mortar;
    const t = 0.82 + tone[row * colsN + colI] * 0.3 + (a[i] - 0.5) * 0.12;
    return [base[0] * t, base[1] * t, base[2] * t];
  });
}

function tiles(seed, base) {
  // dakpannen: rijen met een schaduwrand, de pannen lopen in de v-richting naar de nok
  const a = fbm(S, 48, 2, seed);
  const rowsN = 16, colsN = 24;
  return paint(S, (x, y, i) => {
    const py = ((y / S) * rowsN) % 1, px = ((x / S) * colsN) % 1;
    const k = (0.72 + 0.28 * py) * (0.86 + 0.14 * Math.sin(px * Math.PI)) * (0.92 + a[i] * 0.16);
    return [base[0] * k, base[1] * k, base[2] * k];
  });
}

function waterNormal(seed) {
  const h = fbm(S, 10, 4, seed);
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(S, S);
  const at = (x, y) => h[((y + S) % S) * S + ((x + S) % S)];
  for (let y = 0; y < S; y++) {
    for (let x = 0; x < S; x++) {
      const dx = (at(x + 1, y) - at(x - 1, y)) * 9, dy = (at(x, y + 1) - at(x, y - 1)) * 9;
      const l = Math.hypot(dx, dy, 1), i = (y * S + x) * 4;
      img.data[i] = (-dx / l * 0.5 + 0.5) * 255; img.data[i + 1] = (-dy / l * 0.5 + 0.5) * 255;
      img.data[i + 2] = (1 / l * 0.5 + 0.5) * 255; img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

let lib = null;

export function textures() {
  if (lib) return lib;
  const g = (canvas, size, extra = {}) => ({ map: tex(canvas), size, ...extra });
  lib = {
    grass: g(grass(11, [62, 104, 38], [104, 146, 58]), 7),
    pasture: g(grass(12, [58, 118, 40], [112, 164, 62]), 6),
    verge: g(grass(13, [92, 118, 48], [142, 156, 78], 0.9), 6),
    bank: g(soil(14, [44, 58, 30], [88, 92, 52]), 5),
    fallow: g(grass(15, [96, 104, 52], [150, 142, 86], 1.2), 6),
    soil: g(soil(16, [84, 62, 44], [128, 100, 72], 260), 5),
    track: g(soil(17, [150, 126, 88], [196, 172, 126], 1400), 5),
    yard: g(soil(18, [150, 146, 138], [192, 188, 178], 2600), 5),
    road: g(asphalt(19), 6),
    paved: g(pavers(20), 1.6),
    concrete: g(concrete(21), 6),
    // gewassen: rijen per herhaling en rijafstand bepalen de maat van de tegel
    maize: g(crop(31, 8, [92, 70, 50], [130, 104, 76], [52, 112, 40], [98, 158, 60], 0.56), 6, { rows: true }),
    beet: g(crop(32, 10, [92, 70, 50], [126, 100, 72], [38, 104, 52], [70, 142, 70], 0.78), 5, { rows: true }),
    potato: g(crop(33, 8, [98, 74, 52], [134, 106, 76], [44, 96, 44], [78, 132, 62], 0.84, 0.3), 6, { rows: true }),
    wheat: g(crop(34, 24, [120, 100, 64], [150, 128, 84], [176, 160, 78], [214, 196, 108], 0.96, 0.2), 3, { rows: true }),
    treated: g(crop(35, 12, [70, 96, 44], [96, 124, 56], [120, 150, 70], [158, 178, 92], 0.5, 0.2), 3, { rows: true }),
    waterNormal: tex(waterNormal(41), { srgb: false }),
    macro: tex(paint(256, (() => { const n = fbm(256, 4, 4, 61); return (x, y, i) => { const v = n[i] * 255; return [v, v, v]; }; })()),
      { srgb: false, aniso: 1 }),
    wallBrick: tex(bricks(71, [156, 84, 62], [188, 182, 170])),
    wallSheet: tex(ribbed(72, [70, 96, 78], 16, 0.16)),
    wallLight: tex(ribbed(73, [206, 204, 196], 16, 0.1)),
    roofSheet: tex(ribbed(74, [116, 122, 126], 20, 0.3)),
    roofTiles: tex(tiles(75, [166, 78, 54])),
    wallConcrete: tex(concrete(76)),
  };
  return lib;
}

// Welke textuur bij een gewasnaam (BRP of de fantasiewereld) hoort
export function cropTexture(crop = '') {
  const c = crop.toLowerCase();
  const t = textures();
  if (/ma[iï]s/.test(c)) return t.maize;
  if (/biet/.test(c)) return t.beet;
  if (/aardappel/.test(c)) return t.potato;
  if (/tarwe|gerst|graan|rogge|haver|triticale/.test(c)) return t.wheat;
  if (/braak/.test(c)) return t.fallow;
  if (/gras|weide/.test(c)) return t.pasture;
  return t.pasture;
}

export const surfaceTexture = (kind) => textures()[kind] ?? textures().grass;
