// De Bruut als eenvoudig model in de kleuren van build_agbot.py, op de maten uit de robotconfig.
// Oorsprong = midden achteras op de grond (het regelpunt van OpenAgbot); lokaal x = rechts, y = vooruit, z = op.
// Later te vervangen door de .glb uit FreeCAD (export_glb.py); de API (setPose) blijft gelijk.
import * as THREE from 'three';
import { color } from '../../theme.js';
import { box, cylinderX, cylinderZ, mat } from './materials.js';

// Hoogtes van het frame zoals het FreeCAD-model (agbots/agbot design/agbot_params.py), zodat de aanbouwdelen uit
// FreeCAD op de goede hoogte tegen de balken zitten: bovenplaat wielunit z 0,564, onderbalk 0,619-0,659,
// bovenbalk 0,659-0,699 (vanaf de grond).
const Z_PLATE = 0.560;              // onderkant bovenplaat (4 mm dik, bovenkant 0,564); box() staat op zijn onderkant
const Z_LOWER_BEAM = 0.619;         // onderkant onderbalk

function wheel(diameter) {
  const r = diameter / 2;
  const spin = new THREE.Group();
  spin.add(cylinderX(r, 0.10, color.tire, { segments: 40, threshold: 50 }));
  spin.add(cylinderX(r * 0.47, 0.106, color.rim, { segments: 32, threshold: 50 }));
  spin.add(cylinderX(r * 0.37, 0.14, color.hub, { segments: 32, threshold: 50 }));
  // Noppen (tractorprofiel), zodat je het wiel ziet draaien
  const lugG = new THREE.BoxGeometry(0.1, 0.035, 0.03);
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    const lug = new THREE.Mesh(lugG, mat(color.tire));
    lug.position.set(0, Math.sin(a) * (r - 0.014), Math.cos(a) * (r - 0.014));   // buitenkant nop = r
    lug.rotation.x = -a;
    spin.add(lug);
  }
  spin.position.z = r;
  return spin;
}

function wheelUnit(diameter, steered) {
  const unit = new THREE.Group();
  const steer = new THREE.Group();
  unit.add(steer);
  const spin = wheel(diameter);
  steer.add(spin);
  const r = diameter / 2;
  const top = Z_PLATE;
  for (const sx of [-1, 1]) {
    const plate = box(0.004, 0.24, top - r + 0.06, color.fork);
    plate.position.set(sx * 0.094, 0, r - 0.06);
    steer.add(plate);
  }
  const tp = box(0.24, 0.24, 0.004, color.top_plate);
  tp.position.z = top;
  steer.add(tp);
  const bearing = box(0.09, 0.09, 0.04, color.bearing);
  bearing.position.z = top + 0.004;
  unit.add(bearing);
  if (steered) {
    const motor = box(0.086, 0.086, 0.26, color.stepper);
    motor.position.z = top + 0.17;
    unit.add(motor);
    for (const [dx, dy] of [[-0.06, -0.06], [0.06, -0.06], [-0.06, 0.06], [0.06, 0.06]]) {
      const rod = cylinderZ(0.005, 0.16, color.zinc, { segments: 8, edges: false });
      rod.position.set(dx, dy, top + 0.04);
      unit.add(rod);
    }
  } else {
    const block = box(0.14, 0.19, Z_LOWER_BEAM - top - 0.004, color.wood);   // houten blok tot onder de balk
    block.position.z = top + 0.004;
    unit.add(block);
  }
  return { unit, steer, spin };
}

export class RobotModel {
  constructor(spec) {
    const L = spec.wheelbase, T = spec.track, D = spec.wheel_diameter;
    this.group = new THREE.Group();
    this.group.name = 'robot';
    this.group.rotation.order = 'ZXY';
    const body = new THREE.Group();
    this.group.add(body);
    this.units = {};
    for (const [tag, x, y, steered] of [['RL', -T / 2, 0, false], ['RR', T / 2, 0, false],
      ['FL', -T / 2, L, true], ['FR', T / 2, L, true]]) {
      const u = wheelUnit(D, steered);
      u.unit.position.set(x, y, 0);
      body.add(u.unit);
      this.units[tag] = u;
    }
    const zTop = Z_LOWER_BEAM;
    // Onderste balken (bruin) dwars, bovenste (zwart) in de lengte, zoals Bruut_OpenAgbot.FCStd
    for (const y of [0, L]) {
      for (const dy of [-0.075, 0.075]) {
        const b = box(T + 0.3, 0.04, 0.04, color.beam_lower);
        b.position.set(0, y + dy, zTop);
        body.add(b);
      }
    }
    for (const x of [-T / 2, T / 2]) {
      for (const dx of [-0.05, 0.05]) {
        const b = box(0.04, L + 0.4, 0.04, color.beam_upper);
        b.position.set(x + dx, L / 2, zTop + 0.04);
        body.add(b);
      }
    }
    // GNSS: twee antennes op een mast (GPS1 achter, GPS2 baseline ervoor)
    const [fwd, right] = spec.antenna1;
    const h = spec.antenna_height;
    for (const extra of [0, spec.baseline]) {
      const mast = cylinderZ(0.012, h - zTop - 0.06, color.zinc, { segments: 10, edges: false });
      mast.position.set(right, fwd + extra, zTop + 0.06);
      const dome = new THREE.Mesh(new THREE.SphereGeometry(0.08, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
        mat(color.white));
      dome.rotation.x = Math.PI / 2;
      dome.position.set(right, fwd + extra, h);
      body.add(mast, dome);
    }
    const arm = box(Math.abs(right) + 0.1, 0.04, 0.03, color.zinc);
    arm.position.set(right / 2, fwd, zTop + 0.06);
    body.add(arm);
    // Statusbaken (groen = rijden, oranje = langzaam/wacht, rood = vast/fout)
    this.beacon = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.1, 16),
      new THREE.MeshLambertMaterial({ color: 0x22cc44, emissive: 0x115522 }));
    this.beacon.rotation.x = Math.PI / 2;
    this.beacon.position.set(0, L / 2, zTop + 0.13);
    body.add(this.beacon);
    this.body = body;
  }

  setPose(r) {
    this.group.position.set(r.x, r.y, r.z);
    this.group.rotation.set(THREE.MathUtils.degToRad(r.pitch), THREE.MathUtils.degToRad(r.roll),
      -THREE.MathUtils.degToRad(r.heading));
    this.units.FL.steer.rotation.z = -THREE.MathUtils.degToRad(r.wheel_left_deg);
    this.units.FR.steer.rotation.z = -THREE.MathUtils.degToRad(r.wheel_right_deg);
    for (const tag of ['RL', 'RR', 'FL', 'FR']) this.units[tag].spin.rotation.x = -(r.spin?.[tag] ?? 0);
  }

  setBeacon(state) {
    const c = { green: color.beacon_green, amber: color.beacon_amber, red: color.beacon_red }[state] || color.beacon_green;
    this.beacon.material.color.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
    this.beacon.material.emissive.setRGB(c[0] * 0.4, c[1] * 0.4, c[2] * 0.4, THREE.SRGBColorSpace);
  }
}
