// Meetkunde en tekenstijl van de wereld-editor (bovenaanzicht in lokale meters, x = oost, y = noord).
import { color, css } from '../theme.js';

// Tekenvolgorde (onder -> boven) en de stijl per laag
export const ORDER = ['surfaces', 'fields', 'zones', 'no_go', 'ditches', 'dikes', 'fences', 'passages', 'buildings',
  'trees', 'nests', 'markers'];
export const STYLE = {
  fields: { fill: css(color.field, 0.55), stroke: css(color.boundary, 0.95), lw: 1.6 },
  surfaces: { fill: null, stroke: 'rgba(0,0,0,0.25)', lw: 0.5 },
  zones: { fill: 'rgba(79,179,255,0.20)', stroke: '#4fb3ff', lw: 1.5, dash: [6, 4] },
  no_go: { fill: 'rgba(255,70,60,0.30)', stroke: '#ff4a3d', lw: 2 },
  ditches: { stroke: css(color.water), lw: 2 },
  dikes: { stroke: '#c9a36a', lw: 2 },
  fences: { stroke: '#9e6130', lw: 1.4, dash: [5, 3] },
  passages: { stroke: '#b9e34a', lw: 2.4 },
  buildings: { fill: '#d9dcdc', stroke: 'rgba(0,0,0,0.6)', lw: 0.8 },
  trees: { fill: css(color.crown_dark), stroke: 'rgba(0,0,0,0.4)', r: 1.6 },
  nests: { fill: 'rgba(255,178,62,0.25)', stroke: '#ffb23e', lw: 1.6 },
  markers: { fill: '#ffffff', stroke: '#10180a', lw: 1.2 },
};
export const PREFIX = { fields: 'veld', no_go: 'verboden', zones: 'zone', passages: 'doorgang', ditches: 'sloot',
  dikes: 'dijk', fences: 'hek', nests: 'nest', markers: 'marker', trees: 'boom' };

export const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export function polyArea(p) {
  let s = 0;
  for (let i = 0; i < p.length; i++) {
    const a = p[i], b = p[(i + 1) % p.length];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(s) / 2;
}

export function lineLength(p, closed = false) {
  let s = 0;
  for (let i = 1; i < p.length; i++) s += dist(p[i - 1], p[i]);
  if (closed && p.length > 2) s += dist(p[p.length - 1], p[0]);
  return s;
}

export function inPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// Dichtstbijzijnde punt op een lijnstuk: [x, y, afstand]
export function nearestOnSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const ll = dx * dx + dy * dy;
  const t = ll ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll)) : 0;
  const q = [a[0] + t * dx, a[1] + t * dy];
  return [q[0], q[1], dist(p, q)];
}

export function distToPath(p, pts, closed) {
  let best = Infinity;
  const n = pts.length;
  for (let i = 0; i < (closed ? n : n - 1); i++) best = Math.min(best, nearestOnSegment(p, pts[i], pts[(i + 1) % n])[2]);
  return best;
}

// Ringen van een object: [[ring, hole-index of -1], ...]
export function rings(o) {
  const out = [[o.pts, -1]];
  (o.holes ?? []).forEach((h, i) => out.push([h.pts, i]));
  return out;
}

export function bounds(objs) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const o of objs) for (const [x, y] of o.pts) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  return isFinite(x0) ? { x0, y0, x1, y1 } : null;
}

// Welk object ligt onder de muis? Punten gaan voor lijnen, lijnen voor vlakken, kleine vlakken voor grote.
export function hitTest(p, objs, tolM, usable) {
  let best = null;
  const consider = (o, score) => { if (!best || score < best.score) best = { o, score }; };
  for (const o of objs) {
    if (!usable(o)) continue;
    if (o.geom === 'point') {
      const r = Math.max(tolM * 1.4, o.layer === 'trees' ? 1.2 : 0);
      if (dist(p, o.pts[0]) <= r) consider(o, 0 + dist(p, o.pts[0]) * 1e-3);
    } else if (o.geom === 'line') {
      const w = o.layer === 'passages' ? (o.props.width_m ?? 4) / 2 : o.layer === 'ditches' ? (o.props.width_m ?? 2) / 2 : 0;
      const d = distToPath(p, o.pts, false);
      if (d <= Math.max(tolM, w)) consider(o, 1 + d * 1e-3);
    } else if (o.pts.length >= 3 && inPolygon(p[0], p[1], o.pts)) {
      const rank = { no_go: 2, zones: 3, fields: 4, buildings: 5, surfaces: 6 }[o.layer] ?? 4;
      consider(o, rank + Math.min(0.99, polyArea(o.pts) / 1e7));
    } else if (o.geom === 'polygon' && distToPath(p, o.pts, true) <= tolM) {
      consider(o, 4.5);
    }
  }
  return best?.o ?? null;
}

// Snappen: hoekpunt binnen tolM, anders een rand, anders het raster. Geeft { p, kind }.
export function snap(p, objs, tolM, opts, skip = null) {
  if (opts.vertex) {
    let best = null;
    for (const o of objs) for (const [ring, hi] of rings(o)) for (let i = 0; i < ring.length; i++) {
      if (skip && skip.o === o && skip.hole === hi && skip.i === i) continue;
      const d = dist(p, ring[i]);
      if (d <= tolM && (!best || d < best.d)) best = { d, p: ring[i] };
    }
    if (best) return { p: [best.p[0], best.p[1]], kind: 'punt' };
  }
  if (opts.edge) {
    let best = null;
    for (const o of objs) {
      if (o.geom === 'point') continue;
      for (const [ring, hi] of rings(o)) {
        const n = ring.length, closed = o.geom === 'polygon';
        for (let i = 0; i < (closed ? n : n - 1); i++) {
          const j = (i + 1) % n;
          if (skip && skip.o === o && skip.hole === hi && (skip.i === i || skip.i === j)) continue;
          const q = nearestOnSegment(p, ring[i], ring[j]);
          if (q[2] <= tolM * 0.8 && (!best || q[2] < best.d)) best = { d: q[2], p: [q[0], q[1]] };
        }
      }
    }
    if (best) return { p: best.p, kind: 'rand' };
  }
  if (opts.grid) {
    const g = opts.gridM || 1;
    return { p: [Math.round(p[0] / g) * g, Math.round(p[1] / g) * g], kind: 'raster' };
  }
  return { p, kind: '' };
}

export const round3 = (p) => [Math.round(p[0] * 1000) / 1000, Math.round(p[1] * 1000) / 1000];

export function newId(layer) {
  return `${PREFIX[layer] ?? layer}_${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

// Weergave-benadering (alleen voor de statusregel): lokale meters -> lat/lon
export function approxLatLon(origin, x, y) {
  const lat = origin[0] + y / 111320;
  return [lat, origin[1] + x / (111320 * Math.cos((lat * Math.PI) / 180))];
}

export function fmtLen(m) { return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m.toFixed(m < 20 ? 2 : 1)} m`; }
export function fmtArea(m2) { return m2 >= 1000 ? `${(m2 / 1e4).toFixed(2)} ha` : `${m2.toFixed(0)} m²`; }
