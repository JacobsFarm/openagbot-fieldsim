<script>
  // Minikaart: bovenaanzicht in de kleuren van de 3D-wereld, banen genummerd, robot als pijl.
  // Met een missie zoomt hij in op dat veld; anders de hele wereld. Noorden boven.
  import { onMount } from 'svelte';
  import { color, css } from '../theme.js';

  let { scene = null, robot = null, mission = null, trail = [], doneSwaths = [], current = null, width = 300,
    height = 186 } = $props();
  let canvas;
  let scale = $state(0);                 // beeldpunten per meter (voor de schaalbalk)

  function view() {
    let pts;
    if (mission) {
      const f = scene?.fields.find((x) => x.id === mission.field_id);
      pts = f ? f.poly : mission.swaths.flat();
    } else {
      pts = scene?.area ?? [];
    }
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const s = Math.min((width - 28) / (x1 - x0 || 1), (height - 28) / (y1 - y0 || 1));
    const ox = (width - (x1 - x0) * s) / 2, oy = (height - (y1 - y0) * s) / 2;
    return { tx: (x) => ox + (x - x0) * s, ty: (y) => height - oy - (y - y0) * s, s };
  }

  function draw() {
    if (!canvas || !scene) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr; canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = css(color.grass.map((v) => v * 0.55));
    ctx.fillRect(0, 0, width, height);
    const v = view();
    if (!v) return;
    scale = v.s;
    const poly = (p, fill, stroke, lw = 1) => {
      ctx.beginPath();
      p.forEach(([x, y], i) => (i ? ctx.lineTo(v.tx(x), v.ty(y)) : ctx.moveTo(v.tx(x), v.ty(y))));
      ctx.closePath();
      if (fill) { ctx.fillStyle = fill; ctx.fill(); }
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
    };
    const soft = (c) => css(c.map((x) => x * 0.8));
    const hard = ['track', 'paved', 'road', 'yard'];
    for (const s of scene.surfaces) if (!hard.includes(s.kind) && s.kind !== 'water') poly(s.poly, soft(color[s.kind] || color.grass));
    for (const f of scene.fields) poly(f.poly, soft(color.field), css(color.boundary, 0.55), 0.8);
    for (const s of scene.surfaces) if (hard.includes(s.kind)) poly(s.poly, soft(color[s.kind]));
    for (const s of scene.surfaces) if (s.kind === 'water') poly(s.poly, css(color.water), css(color.water), 1.2);
    for (const d of scene.ditches) {
      ctx.beginPath();
      d.line.forEach(([x, y], i) => (i ? ctx.lineTo(v.tx(x), v.ty(y)) : ctx.moveTo(v.tx(x), v.ty(y))));
      ctx.strokeStyle = css(color.water); ctx.lineWidth = Math.max(1.6, d.width * v.s); ctx.stroke();
    }
    if (!mission) for (const b of scene.buildings) poly(b.poly, '#d9dcdc', 'rgba(0,0,0,0.5)', 0.6);
    if (mission) {
      const w = Math.max(1, mission.entry.werkbreedte_m * v.s * 0.8);
      mission.swaths.forEach(([a, b], k) => {
        const done = doneSwaths.includes(k);
        ctx.beginPath(); ctx.moveTo(v.tx(a[0]), v.ty(a[1])); ctx.lineTo(v.tx(b[0]), v.ty(b[1]));
        ctx.strokeStyle = done ? css(color.treated, 0.85) : k === current ? '#ffb23e' : 'rgba(255,255,255,0.5)';
        ctx.lineWidth = done ? w : k === current ? 1.6 : 0.8; ctx.stroke();
        if (k % Math.max(1, Math.ceil(mission.swaths.length / 12)) === 0 || k === current) {
          ctx.fillStyle = k === current ? '#ffb23e' : 'rgba(255,255,255,0.75)';
          ctx.font = `${k === current ? '600 ' : ''}10px "Barlow", "Segoe UI", Arial`;
          ctx.textAlign = 'right';
          ctx.fillText(String(k + 1), v.tx(a[0]) - 3, v.ty(a[1]) + 3);
        }
      });
    }
    if (trail.length > 1) {
      ctx.beginPath();
      trail.forEach(([x, y], i) => (i ? ctx.lineTo(v.tx(x), v.ty(y)) : ctx.moveTo(v.tx(x), v.ty(y))));
      ctx.strokeStyle = 'rgba(255,255,255,0.9)'; ctx.lineWidth = 1.1; ctx.stroke();
    }
    if (robot) {
      const x = v.tx(robot.x), y = v.ty(robot.y), h = (robot.heading * Math.PI) / 180;
      ctx.save(); ctx.translate(x, y); ctx.rotate(h);
      ctx.beginPath(); ctx.moveTo(0, -9); ctx.lineTo(6, 6); ctx.lineTo(0, 3); ctx.lineTo(-6, 6); ctx.closePath();
      ctx.fillStyle = '#b9e34a'; ctx.fill(); ctx.strokeStyle = '#10180a'; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.restore();
    }
  }

  // Schaalbalk: een rond getal dat ongeveer 60 px lang is
  const bar = $derived.by(() => {
    if (!scale) return null;
    const want = 60 / scale;
    const m = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a));
    return { m, px: m * scale };
  });

  onMount(draw);
  $effect(() => { scene; robot; mission; trail.length; doneSwaths.length; current; draw(); });
</script>

<div class="map" style="width:{width}px;height:{height}px">
  <canvas bind:this={canvas} style="width:{width}px;height:{height}px"></canvas>
  <div class="north">N</div>
  {#if bar}<div class="scale"><span style="width:{bar.px}px"></span>{bar.m} m</div>{/if}
</div>

<style>
  .map { position: relative; border-radius: 5px; overflow: hidden; border: 1px solid var(--line-strong); }
  canvas { display: block; }
  .north { position: absolute; top: 6px; right: 7px; width: 20px; height: 20px; border-radius: 50%; display: grid;
    place-items: center; font-family: var(--display); font-size: 12px; font-weight: 700; color: var(--accent);
    background: rgba(8, 12, 14, 0.7); }
  .scale { position: absolute; left: 7px; bottom: 5px; display: flex; align-items: center; gap: 6px; font-size: 10.5px;
    color: #fff; text-shadow: 0 1px 2px #000; }
  .scale span { height: 4px; border: 1px solid #fff; border-top: 0; }
</style>
