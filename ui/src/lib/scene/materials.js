// Materialen van de sim-stijl: PBR met schaduw en luchtreflectie. Eén cache, zodat gelijke kleuren één materiaal
// delen (minder draw-state wisselingen bij honderden bomen en palen).
//
// Grondvlakken (velden, paden, erf, oevers) zijn 'decals' op het terrein: ze schrijven geen diepte en worden in de
// vertex-shader een stukje langs de kijkstraal naar de camera getrokken. Zo liggen ze altijd strak op het reliëf
// (geen flikkeren, geen gaten) en blijven de randen op elke afstand scherp. Volgorde = renderOrder.
import * as THREE from 'three';
import { edgeColor } from '../../theme.js';
import { textures } from './textures.js';

const cache = new Map();

export const ORDER = { terrain: -100, surface: -90, field: -80, mark: -70, bank: -60, overlay: -50, water: -40 };

export function rgb(c) {
  return new THREE.Color().setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
}

// Vast materiaal voor objecten (robot, gebouwen, palen)
export function mat(c, opts = {}) {
  const key = c.join(',') + JSON.stringify(opts);
  if (!cache.has(key)) {
    const m = new THREE.MeshStandardMaterial({ color: rgb(c), side: opts.side ?? THREE.FrontSide,
      roughness: opts.rough ?? 0.78, metalness: opts.metal ?? 0.0, flatShading: !!opts.flat });
    if (opts.opacity !== undefined) {
      m.transparent = true;
      m.opacity = opts.opacity;
      m.depthWrite = false;
    }
    cache.set(key, m);
  }
  return cache.get(key);
}

// ------------------------------------------------------------------ decals op het terrein
const BIAS_VERTEX = /* glsl */`
  #include <project_vertex>
  {
    float gd = length(mvPosition.xyz);
    mvPosition.xyz *= 1.0 - min(0.5, (uBias + gd * 0.012) / max(gd, 0.001));
    gl_Position = projectionMatrix * mvPosition;
  }`;

// Trekt een materiaal naar de camera toe (zelfde plek in beeld, kleinere diepte): `bias` in meters.
export function biasPatch(material, bias = 0.25, extra = null) {
  material.depthWrite = false;
  material.userData.uBias = { value: bias };
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uBias = material.userData.uBias;
    shader.vertexShader = 'uniform float uBias;\n' + shader.vertexShader.replace('#include <project_vertex>', BIAS_VERTEX);
    extra?.(shader);
  };
  material.customProgramCacheKey = () => `bias${extra ? extra.key : ''}`;
  return material;
}

const macro = { value: null };

// Ondergrond met een textuur in wereldmaten (geen uv's nodig): `spec` = { map, size } uit textures.js.
// angle = richting van de rijen (rad, vanaf +x), tint = extra kleur, bias 0 = het terrein zelf.
export function groundMat(spec, { angle = 0, tint = [1, 1, 1], bias = 0.25, rough = 0.95, macroAmount = 0.34 } = {}) {
  macro.value ??= textures().macro;
  const m = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: rough, metalness: 0, side: THREE.DoubleSide });
  const u = {
    uTex: { value: spec.map }, uMacro: macro, uSize: { value: spec.size },
    uRot: { value: new THREE.Vector2(Math.cos(angle), Math.sin(angle)) },
    uTint: { value: new THREE.Color().setRGB(tint[0], tint[1], tint[2]) },
    uMacroAmount: { value: macroAmount }, uBias: { value: bias },
  };
  m.userData.uniforms = u;
  m.depthWrite = bias === 0;
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u);
    shader.vertexShader = 'uniform float uBias;\nvarying vec2 vWxy;\n' + shader.vertexShader.replace(
      '#include <project_vertex>',
      (bias ? BIAS_VERTEX : '#include <project_vertex>') + '\n  vWxy = (modelMatrix * vec4(transformed, 1.0)).xy;');
    shader.fragmentShader = /* glsl */`
      uniform sampler2D uTex; uniform sampler2D uMacro; uniform float uSize; uniform vec2 uRot; uniform vec3 uTint;
      uniform float uMacroAmount; varying vec2 vWxy;
      ` + shader.fragmentShader.replace('#include <map_fragment>', /* glsl */`
      {
        vec2 guv = vec2(dot(vWxy, uRot), dot(vWxy, vec2(-uRot.y, uRot.x))) / uSize;
        vec3 gtex = texture2D(uTex, guv).rgb;
        // tweede, grovere laag tegen zichtbare herhaling op afstand
        vec3 gfar = texture2D(uTex, guv * 0.137 + 0.31).rgb;
        float gmac = texture2D(uMacro, vWxy / 211.0).r * 0.6 + texture2D(uMacro, vWxy / 43.0).r * 0.4;
        diffuseColor.rgb *= mix(gtex, gfar, 0.35) * uTint * (1.0 + (gmac - 0.5) * 2.0 * uMacroAmount);
      }`);
  };
  m.customProgramCacheKey = () => `ground${bias ? 'b' : ''}`;
  return m;
}

// Water: spiegelt de lucht, rimpels schuiven langzaam (world3d verplaatst de normal map per beeld)
let water = null;
export function waterMat() {
  if (!water) {
    const n = textures().waterNormal;
    water = new THREE.MeshStandardMaterial({ color: rgb([0.10, 0.44, 0.72]), roughness: 0.12, metalness: 0.0,
      normalMap: n, normalScale: new THREE.Vector2(0.35, 0.35), envMapIntensity: 2.2, side: THREE.DoubleSide });
  }
  return water;
}

// Lint met een breedte die meegroeit met de afstand (blijft minstens `px` beeldpunten breed): perceelgrenzen,
// banen, geplande route. Geometrie uit terrain.ribbon(); `dash` in meters (0 = doorgetrokken).
export function ribbonMat(c, { width = 0.3, px = 1.6, opacity = 1, dash = 0, bias = 0.35 } = {}) {
  const key = `ribbon${c.join(',')}|${width}|${px}|${opacity}|${dash}|${bias}`;
  if (cache.has(key)) return cache.get(key);
  const m = new THREE.MeshBasicMaterial({ color: rgb(c), side: THREE.DoubleSide, transparent: opacity < 1, opacity,
    depthWrite: false, fog: true });
  const u = { uBias: { value: bias }, uWidth: { value: width / 2 }, uPx: { value: px }, uDash: { value: dash } };
  m.userData.uniforms = u;
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u, { uViewH: viewHeight });
    shader.vertexShader = /* glsl */`
      uniform float uBias; uniform float uWidth; uniform float uPx; uniform float uViewH;
      attribute vec2 aSide; attribute float aDist; varying float vDist;
      ` + shader.vertexShader.replace('#include <begin_vertex>', /* glsl */`
      #include <begin_vertex>
      {
        float rd = distance(cameraPosition, (modelMatrix * vec4(position, 1.0)).xyz);
        // meters per beeldpunt op deze afstand (uViewH = hoogte van het beeld / (2 tan(fov/2)))
        transformed.xy += aSide * max(uWidth, uPx * rd / uViewH);
        vDist = aDist;
      }`).replace('#include <project_vertex>', BIAS_VERTEX);
    shader.fragmentShader = 'uniform float uDash; varying float vDist;\n' + shader.fragmentShader.replace(
      '#include <map_fragment>', 'if (uDash > 0.0 && fract(vDist / uDash) > 0.55) discard;');
  };
  m.customProgramCacheKey = () => 'ribbon';
  cache.set(key, m);
  return m;
}

// world3d zet dit bij elke resize: hoogte in beeldpunten / (2 tan(fov / 2))
export const viewHeight = { value: 1000 };

// ------------------------------------------------------------------ lijnen en CAD-randen
const edgeMat = new THREE.LineBasicMaterial({ color: edgeColor, transparent: true, opacity: 0.55 });
const lineCache = new Map();
const edgeObjects = new Set();
let edgesOn = false;

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

// Randen van robot en werktuig (CAD-stijl) aan of uit; standaard uit in de sim-stijl
export function setEdges(on) {
  edgesOn = on;
  for (const e of edgeObjects) e.visible = on;
}

export function addEdges(mesh, threshold = 30) {
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, threshold), edgeMat);
  edges.raycast = () => {};
  edges.visible = edgesOn;
  edges.userData.isEdge = true;
  edgeObjects.add(edges);
  mesh.add(edges);
  return edges;
}

export function dropEdges(root) {
  root.traverse((o) => { if (o.userData.isEdge) { edgeObjects.delete(o); o.geometry.dispose(); } });
}

// Mesh dat schaduw werpt en ontvangt; opts.edges = CAD-randen erbij (te schakelen met setEdges)
export function solid(geometry, c, opts = {}) {
  const { edges, threshold, ...m } = opts;
  const mesh = new THREE.Mesh(geometry, mat(c, m));
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  if (edges !== false) addEdges(mesh, threshold ?? 30);
  return mesh;
}

export function box(w, d, h, c, opts) {
  // w langs x, d langs y, h langs z; oorsprong in het midden van de onderkant
  const g = new THREE.BoxGeometry(w, d, h);
  g.translate(0, 0, h / 2);
  return solid(g, c, opts);
}

export function cylinderX(r, len, c, opts = {}) {
  const { segments, ...o } = opts;
  const g = new THREE.CylinderGeometry(r, r, len, segments ?? 32);
  g.rotateZ(Math.PI / 2);
  return solid(g, c, o);
}

export function cylinderZ(r, h, c, opts = {}) {
  const { segments, rTop, ...o } = opts;
  const g = new THREE.CylinderGeometry(rTop ?? r, r, h, segments ?? 24);
  g.rotateX(Math.PI / 2);
  g.translate(0, 0, h / 2);
  return solid(g, c, o);
}
