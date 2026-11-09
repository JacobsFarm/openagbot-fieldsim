// Materialen in de FreeCAD-look: mat, vlak gekleurd, zwarte randen. Eén cache, zodat gelijke kleuren één materiaal
// delen (minder draw-state wisselingen bij honderden bomen en palen).
import * as THREE from 'three';
import { edgeColor } from '../../theme.js';

const cache = new Map();

export function rgb(c) {
  return new THREE.Color().setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
}

export function mat(c, opts = {}) {
  const key = c.join(',') + JSON.stringify(opts);
  if (!cache.has(key)) {
    const m = new THREE.MeshLambertMaterial({ color: rgb(c), side: opts.side ?? THREE.FrontSide });
    if (opts.opacity !== undefined) {
      m.transparent = true;
      m.opacity = opts.opacity;
      m.depthWrite = false;
    }
    if (opts.layer) {
      // Gedrapeerde vlakken liggen een paar cm boven het terrein; een kleine vaste polygonOffset voorkomt flikkeren.
      // Geen polygonOffsetFactor: die schaalt met de helling in beeld en trok bij een schuine kijkhoek het veld
      // over de onderkant van de wielen en het werktuig heen.
      m.polygonOffset = true;
      m.polygonOffsetFactor = 0;
      m.polygonOffsetUnits = -opts.layer * 4;
    }
    cache.set(key, m);
  }
  return cache.get(key);
}

const edgeMat = new THREE.LineBasicMaterial({ color: edgeColor });
const lineCache = new Map();

export function lineMat(c = null, opts = {}) {
  if (!c) return edgeMat;
  const key = c.join(',') + JSON.stringify(opts);
  if (!lineCache.has(key)) {
    lineCache.set(key, opts.dashed
      ? new THREE.LineDashedMaterial({ color: rgb(c), dashSize: opts.dash ?? 0.6, gapSize: opts.gap ?? 0.5 })
      : new THREE.LineBasicMaterial({ color: rgb(c), transparent: !!opts.opacity, opacity: opts.opacity ?? 1 }));
  }
  return lineCache.get(key);
}

// Mesh met FreeCAD-randen: alle scherpe randen (hoek > threshold) als zwarte lijnen.
export function solid(geometry, c, opts = {}) {
  const mesh = new THREE.Mesh(geometry, mat(c, opts));
  if (opts.edges !== false) {
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, opts.threshold ?? 30), edgeMat);
    edges.raycast = () => {};
    mesh.add(edges);
  }
  return mesh;
}

export function box(w, d, h, c, opts) {
  // w langs x, d langs y, h langs z; oorsprong in het midden van de onderkant
  const g = new THREE.BoxGeometry(w, d, h);
  g.translate(0, 0, h / 2);
  return solid(g, c, opts);
}

export function cylinderX(r, len, c, opts = {}) {
  const g = new THREE.CylinderGeometry(r, r, len, opts.segments ?? 32);
  g.rotateZ(Math.PI / 2);
  return solid(g, c, opts);
}

export function cylinderZ(r, h, c, opts = {}) {
  const g = new THREE.CylinderGeometry(opts.rTop ?? r, r, h, opts.segments ?? 24);
  g.rotateX(Math.PI / 2);
  g.translate(0, 0, h / 2);
  return solid(g, c, opts);
}
