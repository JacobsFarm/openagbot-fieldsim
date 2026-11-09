// Lucht, zon, schaduw en mist. De wereld is z-omhoog (ENU); de luchtshader van three.js rekent y-omhoog, dus de
// kijkrichting en de zon gaan er verwisseld (xzy) in.
import * as THREE from 'three';
import { Sky } from 'three/examples/jsm/objects/Sky.js';

const DAY_FOG = new THREE.Color(0xbcd0de), LOW_FOG = new THREE.Color(0xe2c2a0);
const DAY_SUN = new THREE.Color(0xfff4e2), LOW_SUN = new THREE.Color(0xffb878);

export class Atmosphere {
  constructor(renderer, scene) {
    this.renderer = renderer;
    this.scene = scene;
    this.sky = new Sky();
    this.sky.material.fragmentShader = this.sky.material.fragmentShader.replace(
      'normalize( vWorldPosition - cameraPosition )', 'normalize( vWorldPosition - cameraPosition ).xzy');
    this.sky.scale.setScalar(30000);
    this.sky.frustumCulled = false;
    this.sky.renderOrder = -200;
    const u = this.sky.material.uniforms;
    u.turbidity.value = 4.5;
    u.rayleigh.value = 1.35;
    u.mieCoefficient.value = 0.004;
    u.mieDirectionalG.value = 0.82;
    u.cloudCoverage.value = 0.42;
    u.cloudDensity.value = 0.5;
    u.cloudElevation.value = 0.45;
    scene.add(this.sky);

    this.hemi = new THREE.HemisphereLight(0xd6e6ff, 0x8a8f6a, 1.0);
    this.hemi.position.set(0, 0, 1);
    this.sun = new THREE.DirectionalLight(0xffffff, 3.0);
    this.sun.castShadow = true;
    this.sun.shadow.bias = -0.00006;
    this.sun.shadow.normalBias = 0.04;
    scene.add(this.hemi, this.sun, this.sun.target);
    scene.fog = new THREE.Fog(DAY_FOG.clone(), 250, 5200);

    this.dir = new THREE.Vector3(0.3, -0.5, 0.8).normalize();
    this.pmrem = new THREE.PMREMGenerator(renderer);
    this.envScene = new THREE.Scene();
    this.hour = 17;
    this.shadowSize = 2048;
    this.shadows = true;
    this.clouds = true;
    this.setShadows(true, 2048);
    this.setHour(17);
  }

  // Tijd van de dag (uur, 5-21) -> zonnestand (zomerdag op 52 graden noord, ruwweg)
  setHour(hour) {
    this.hour = hour;
    const t = (hour - 5.2) / 16.3;                               // 0 = zonsopkomst, 1 = zonsondergang
    const el = THREE.MathUtils.degToRad(Math.max(2, 58 * Math.sin(Math.PI * THREE.MathUtils.clamp(t, 0, 1))));
    const az = THREE.MathUtils.degToRad(60 + 240 * THREE.MathUtils.clamp(t, 0, 1));   // kompas: NO -> Z -> NW
    this.dir.set(Math.sin(az) * Math.cos(el), Math.cos(az) * Math.cos(el), Math.sin(el));
    const u = this.sky.material.uniforms;
    u.sunPosition.value.set(this.dir.x, this.dir.z, this.dir.y);
    const k = THREE.MathUtils.smoothstep(Math.sin(el), 0.05, 0.45);      // 0 = lage zon, 1 = volle dag
    this.sun.color.copy(LOW_SUN).lerp(DAY_SUN, k);
    this.sun.intensity = 1.6 + 1.5 * k;
    this.hemi.intensity = 0.75 + 0.45 * k;
    this.scene.fog.color.copy(LOW_FOG).lerp(DAY_FOG, k);
    this._envDirty = true;
  }

  setClouds(on) {
    this.clouds = on;
    this.sky.material.uniforms.cloudCoverage.value = on ? 0.42 : 0;
    this._envDirty = true;
  }

  setShadows(on, size = this.shadowSize) {
    this.shadows = on;
    this.renderer.shadowMap.enabled = true;
    this.sun.castShadow = on;
    if (size !== this.sun.shadow.mapSize.x) {
      this.sun.shadow.mapSize.set(size, size);
      this.sun.shadow.map?.dispose();
      this.sun.shadow.map = null;
    }
    this.shadowSize = size;
  }

  // Omgevingslicht en spiegeling (water, metaal) uit de lucht zelf; alleen opnieuw als de zon verschoven is
  _bakEnvironment() {
    const u = this.sky.material.uniforms;
    u.showSunDisc.value = 0;
    const parent = this.sky.parent;
    this.envScene.add(this.sky);
    const old = this.env;
    this.env = this.pmrem.fromScene(this.envScene, 0, 1, 40000);
    parent.add(this.sky);
    u.showSunDisc.value = 1;
    this.scene.environment = this.env.texture;
    this.scene.environmentIntensity = 0.1;      // de lucht is HDR: een beetje is genoeg
    old?.dispose();
    this._envDirty = false;
  }

  // Per beeld: wolken laten drijven en de schaduwkaart rond het kijkpunt leggen (grootte volgt de zoom)
  update(time, focus, viewDist) {
    this.sky.material.uniforms.time.value = time;
    if (this._envDirty) this._bakEnvironment();
    const half = THREE.MathUtils.clamp(viewDist * 0.85, 14, 420);
    const cam = this.sun.shadow.camera;
    if (Math.abs(half - (this._half ?? 0)) > half * 0.04) {
      this._half = half;
      cam.left = -half; cam.right = half; cam.top = half; cam.bottom = -half;
      cam.near = 250; cam.far = 950;          // de lamp staat 600 m van het kijkpunt
      cam.updateProjectionMatrix();
      this.sun.shadow.normalBias = 0.02 + half * 0.0012;
    }
    // op hele texels van de schaduwkaart leggen, anders 'zwemt' de schaduwrand als de camera meerijdt
    const texel = (this._half * 2) / this.shadowSize * 4;
    const fx = Math.round(focus.x / texel) * texel, fy = Math.round(focus.y / texel) * texel;
    this.sun.target.position.set(fx, fy, focus.z);
    this.sun.position.set(fx + this.dir.x * 600, fy + this.dir.y * 600, focus.z + this.dir.z * 600);
    this.sun.target.updateMatrixWorld();
  }

  dispose() {
    this.env?.dispose();
    this.pmrem.dispose();
  }
}
