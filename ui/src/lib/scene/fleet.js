// Vloot in 3D: de echte robots uit /ws (`fleet.robots`) als model in de wereld, elk met een naamkaartje, een
// gekleurde ring op de grond en zijn gereden spoor. De telemetrie komt 5x per seconde; daartussen schuift het
// model vloeiend naar de laatste stand, zodat het beeld niet schokt.
import * as THREE from 'three';
import { ORDER } from './materials.js';
import { RobotModel } from './robot.js';
import { ribbon } from './terrain.js';

const SMOOTH = 7;            // 1/s: hoe snel het model de gemeten stand inhaalt
const JUMP_M = 6;            // verder weg dan dit = direct neerzetten (eerste meting, robot verplaatst)
const CHUNK = 40;            // punten per stuk spoor

const rgb01 = (hex) => { const c = new THREE.Color(hex); return [c.r, c.g, c.b].map((v) => +v.toFixed(3)); };
const lerpAngle = (a, b, k) => a + ((((b - a) % 360) + 540) % 360 - 180) * k;

// Naamkaartje: altijd even groot in beeld en dwars door alles heen leesbaar
function tag(sprite, title, sub, colorHex, dim) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const f1 = '600 40px "Barlow Condensed", "Bahnschrift", "Segoe UI", sans-serif';
  const f2 = '500 24px "Barlow", "Segoe UI", sans-serif';
  ctx.font = f1;
  const w1 = ctx.measureText(title.toUpperCase()).width;
  ctx.font = f2;
  const w = Math.ceil(Math.max(w1, ctx.measureText(sub).width)) + 52;
  c.width = w; c.height = 100;
  ctx.fillStyle = 'rgba(11,17,21,0.82)';
  ctx.beginPath(); ctx.roundRect(0, 0, w, 100, 10); ctx.fill();
  ctx.fillStyle = dim ? '#5f717a' : colorHex;
  ctx.fillRect(0, 0, 7, 100);
  ctx.textBaseline = 'middle';
  ctx.font = f1; ctx.fillStyle = dim ? '#8FA1AA' : '#F2F6F7';
  ctx.fillText(title.toUpperCase(), 26, 36);
  ctx.font = f2; ctx.fillStyle = '#A9B8BF';
  ctx.fillText(sub, 26, 74);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  sprite.material.map?.dispose();
  sprite.material.map = t;
  sprite.material.needsUpdate = true;
  const k = 0.00034;
  sprite.scale.set(w * k, 100 * k, 1);
}

export class FleetLayer {
  constructor(scene3) {
    this.group = new THREE.Group();
    this.group.name = 'fleet';
    scene3.add(this.group);
    this.items = new Map();
    this.hf = null;
    this.spec = null;
  }

  // Nieuwe wereld: ander terrein en andere meters. De sporen komen opnieuw van de server.
  setWorld(hf, robotSpec) {
    this.hf = hf;
    this.spec = robotSpec;
    for (const id of [...this.items.keys()]) this._remove(id);
  }

  // Stand van de camera voor 'volg': de vloeiende stand van deze robot, of null als hij niet in beeld is
  pose(id) {
    const it = this.items.get(id);
    return it && it.root.visible ? it.cur : null;
  }

  set(robots) {
    if (!this.hf || !this.spec) return;
    const seen = new Set();
    for (const r of robots) {
      if (!r.enabled) continue;
      seen.add(r.id);
      let it = this.items.get(r.id);
      if (!it) it = this._add(r);
      const p = r.pose, t = r.telemetry;
      it.root.visible = !!p?.in_world;
      if (p?.in_world) {
        it.target = { x: p.x, y: p.y, heading: p.heading, v: (t?.speed_kmh ?? 0) / 3.6,
          wl: t?.wheel_left_deg ?? 0, wr: t?.wheel_right_deg ?? 0 };
      }
      it.online = r.online;
      const sub = !r.online ? 'offline' : `${Math.abs(t?.speed_kmh ?? 0).toFixed(1)} km/h · ${t?.fix_label || 'geen fix'}`;
      const key = `${r.name}|${sub}|${r.color}`;
      if (key !== it.tagKey) {
        it.tagKey = key;
        tag(it.tag, r.name, sub, r.color, !r.online);
        it.ring.material.color.set(r.online ? r.color : '#5f717a');
      }
      it.model.setBeacon(!r.online || t?.fault ? 'red' : t?.fix !== 4 || t?.geofence ? 'amber' : 'green');
      if (r.color !== it.color) { it.color = r.color; this._redrawTrail(it); }
      const tr = r.trail;
      if (tr) {
        // `from` telt vanaf het begin van de rit; wij hebben alles vanaf trailBase. Sluit het niet aan (spoor
        // gewist, of de server stuurt het opnieuw), dan beginnen we opnieuw.
        if (tr.gen !== it.trailGen || tr.from < it.trailBase + it.trailPts.length) {
          it.trailGen = tr.gen;
          it.trailBase = tr.from;
          it.trailPts = [];
          this._redrawTrail(it);
        }
        if (tr.points.length) { it.trailPts.push(...tr.points); this._growTrail(it); }
      }
    }
    for (const id of [...this.items.keys()]) if (!seen.has(id)) this._remove(id);
  }

  update(dt) {
    const k = 1 - Math.exp(-dt * SMOOTH);
    for (const it of this.items.values()) {
      const t = it.target;
      if (!t || !it.root.visible) continue;
      const c = it.cur;
      if (!c.ready || Math.hypot(t.x - c.x, t.y - c.y) > JUMP_M) {
        Object.assign(c, { x: t.x, y: t.y, heading: t.heading, wheel_left_deg: t.wl, wheel_right_deg: t.wr, ready: true });
      } else {
        c.x += (t.x - c.x) * k; c.y += (t.y - c.y) * k;
        c.heading = lerpAngle(c.heading, t.heading, k);
        c.wheel_left_deg += (t.wl - c.wheel_left_deg) * k;
        c.wheel_right_deg += (t.wr - c.wheel_right_deg) * k;
      }
      c.z = this.hf.at(c.x, c.y);
      if (it.online) {                                   // wielen laten draaien op de gemeten snelheid
        const d = (t.v * dt) / (this.spec.wheel_diameter / 2);
        for (const w of ['RL', 'RR', 'FL', 'FR']) c.spin[w] += d;
      }
      it.model.setPose(c);
      it.ring.position.set(c.x, c.y, c.z + 0.07);
      it.tag.position.set(c.x, c.y, c.z + 2.4);
    }
  }

  _add(r) {
    const root = new THREE.Group();
    const model = new RobotModel(this.spec);
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.25, 1.42, 48),
      new THREE.MeshBasicMaterial({ color: r.color, transparent: true, opacity: 0.9, depthWrite: false, side: THREE.DoubleSide,
        fog: false, toneMapped: false }));
    ring.renderOrder = ORDER.overlay + 3;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ sizeAttenuation: false, depthTest: false, transparent: true,
      fog: false, toneMapped: false }));
    sprite.center.set(0.5, 0);
    sprite.renderOrder = 60;
    sprite.raycast = () => {};
    const trail = new THREE.Group();
    root.add(model.group, ring, sprite);
    this.group.add(root, trail);
    const it = { root, model, ring, tag: sprite, trail, trailPts: [], trailBase: 0, trailDrawn: 0, tail: null, trailGen: null, color: r.color,
      tagKey: '', target: null, online: false,
      cur: { x: 0, y: 0, z: 0, heading: 0, pitch: 0, roll: 0, wheel_left_deg: 0, wheel_right_deg: 0,
        spin: { RL: 0, RR: 0, FL: 0, FR: 0 }, ready: false } };
    root.visible = false;
    this.items.set(r.id, it);
    return it;
  }

  _remove(id) {
    const it = this.items.get(id);
    if (!it) return;
    it.trail.traverse((o) => o.geometry?.dispose());
    it.ring.geometry.dispose();
    it.tag.material.map?.dispose();
    this.group.remove(it.root, it.trail);
    this.items.delete(id);
  }

  _redrawTrail(it) {
    it.trail.traverse((o) => o.geometry?.dispose());
    it.trail.clear();
    it.trailDrawn = 0;
    it.tail = null;
    this._growTrail(it);
  }

  // Het spoor groeit in vaste stukken van CHUNK punten; het laatste, nog onvolledige stuk (tot aan de robot)
  // wordt bij elk nieuw punt opnieuw getekend.
  _growTrail(it) {
    const c = rgb01(it.color);
    const opts = { width: 0.12, px: 1.2, offset: 0.07, maxSeg: 2, order: ORDER.overlay + 1 };
    if (it.tail) { it.tail.geometry.dispose(); it.trail.remove(it.tail); it.tail = null; }
    while (it.trailPts.length - it.trailDrawn > CHUNK) {
      const mesh = ribbon(it.trailPts.slice(it.trailDrawn, it.trailDrawn + CHUNK + 1), this.hf, c, opts);
      it.trailDrawn += CHUNK;
      if (mesh) it.trail.add(mesh);
    }
    if (it.trailPts.length - it.trailDrawn >= 2) {
      it.tail = ribbon(it.trailPts.slice(it.trailDrawn), this.hf, c, opts);
      if (it.tail) it.trail.add(it.tail);
    }
  }
}
