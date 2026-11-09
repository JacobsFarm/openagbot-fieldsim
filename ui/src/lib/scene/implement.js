// Aanbouwdeel op de robot: het .glb uit FreeCAD (tools/export_implements.py), in de kleuren van het bouwscript
// met schaduw en glans; CAD-randen zijn een instelling (materials.setEdges).
// Per stand een model (work = in het werk, raised = geheven/transport); losse delen (de X-slede van de boor) zijn een
// eigen node die we verschuiven. Het glb staat in de FreeCAD-assen in meters; `spec.offset` zet de oorsprong op de robot.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { addEdges, dropEdges } from './materials.js';

const loader = new GLTFLoader();
const EDGE_ANGLE = 35;      // graden: alleen echte randen tekenen, zoals FreeCAD

const looks = new Map();

function simLook(root) {
  const edges = [];
  root.traverse((o) => {
    if (!o.isMesh) return;
    const src = o.material;
    const see = src.transparent || src.opacity < 1;
    const key = `${src.color.getHexString()}|${see ? src.opacity : 1}`;
    if (!looks.has(key)) {
      // donkere en grijze delen zijn meestal staal: iets glanzender; felle kleuren zijn lak of kunststof
      const hsl = src.color.getHSL({});
      const steel = hsl.s < 0.18;
      const m = new THREE.MeshStandardMaterial({ color: src.color, side: THREE.DoubleSide,
        roughness: steel ? 0.42 : 0.6, metalness: steel ? 0.55 : 0.1 });
      if (see) { m.transparent = true; m.opacity = src.opacity; m.depthWrite = false; }
      looks.set(key, m);
    }
    o.material = looks.get(key);
    o.castShadow = !see;
    o.receiveShadow = true;
    if (!see) edges.push(o);
  });
  for (const o of edges) addEdges(o, EDGE_ANGLE);
}

export class ImplementModel {
  constructor(spec) {
    this.spec = spec;
    this.group = new THREE.Group();
    this.group.name = `implement_${spec.id}`;
    this.group.position.set(spec.offset[0], spec.offset[1], 0);
    this.poses = {};
    this.pose = null;
    this.ready = Promise.all(spec.poses.map(async (pose) => {
      const g = await loader.loadAsync(`/api/implements/${spec.id}/${pose}.glb`);
      const root = g.scene;
      simLook(root);
      root.visible = false;
      this.poses[pose] = root;
      this.group.add(root);
    })).then(() => this.setPose(this.pose ?? 'raised'));
  }

  setPose(pose) {
    if (!this.poses[pose]) pose = Object.keys(this.poses)[0];
    this.pose = pose;
    for (const [k, r] of Object.entries(this.poses)) r.visible = k === pose;
  }

  // Live toestand uit de sim (snapshot.implement)
  update(s) {
    if (!s) return;
    const work = this.spec.kind === 'spot' ? s.drilling_s > 0 : s.lowered;
    if ((work ? 'work' : 'raised') !== this.pose) this.setPose(work ? 'work' : 'raised');
    for (const r of Object.values(this.poses)) {
      const c = r.getObjectByName('carriage');
      if (c) c.position.x += ((s.carriage_x ?? 0) - c.position.x) * 0.2;   // slede schuift zichtbaar
    }
  }

  dispose() {
    dropEdges(this.group);
    this.group.traverse((o) => { o.geometry?.dispose(); });
    this.group.removeFromParent();
  }
}
