<script>
  // Minikaart zoals rechtsonder in field_run_simple.gif: bovenaanzicht, genummerde banen, robot als pijltje.
  // Met een missie zoomt hij in op dat veld; anders de hele wereld.
  import { onMount } from 'svelte';
  import { color } from '../theme.js';

  let { scene = null, robot = null, mission = null, trail = [], doneSwaths = [], current = null } = $props();
  let canvas;
  const W = 370, H = 126;
  const css = (c) => `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;

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
    const s = Math.min((W - 40) / (x1 - x0 || 1), (H - 16) / (y1 - y0 || 1));
    const ox = (W - (x1 - x0) * s) / 2, oy = (H - (y1 - y0) * s) / 2;
    return { tx: (x) => ox + (x - x0) * s, ty: (y) => H - oy - (y - y0) * s, s };
  }

  function draw() {
    if (!canvas || !scene) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = css(color.headland);
    ctx.fillRect(0, 0, W, H);
    const v = view();
    if (!v) return;
    const poly = (p, fill, stroke) => {
      ctx.beginPath();
      p.forEach(([x, y], i) => (i ? ctx.lineTo(v.tx(x), v.ty(y)) : ctx.moveTo(v.tx(x), v.ty(y))));
      ctx.closePath();
      if (fill) { ctx.fillStyle = fill; ctx.fill(); }
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.8; ctx.stroke(); }
    };
    for (const s of scene.surfaces) poly(s.poly, css(color[s.kind] || color.grass));
    for (const d of scene.ditches) {
      ctx.beginPath();
      d.line.forEach(([x, y], i) => (i ? ctx.lineTo(v.tx(x), v.ty(y)) : ctx.moveTo(v.tx(x), v.ty(y))));
      ctx.strokeStyle = css(color.water); ctx.lineWidth = Math.max(1, d.width * v.s); ctx.stroke();
    }
    for (const f of scene.fields) poly(f.poly, css(color.field), '#2c3a1e');
    if (!mission) for (const b of scene.buildings) poly(b.poly, css(color.wall), '#333');
    if (mission) {
      const w = Math.max(1, mission.entry.werkbreedte_m * v.s * 0.8);
      mission.swaths.forEach(([a, b], k) => {
        ctx.beginPath(); ctx.moveTo(v.tx(a[0]), v.ty(a[1])); ctx.lineTo(v.tx(b[0]), v.ty(b[1]));
        ctx.strokeStyle = doneSwaths.includes(k) ? css(color.treated) : css(color.lines);
        ctx.lineWidth = doneSwaths.includes(k) ? w : 1; ctx.stroke();
        if (k % Math.max(1, Math.ceil(mission.swaths.length / 12)) === 0 || k === current) {
          ctx.fillStyle = k === current ? '#E66E14' : '#3c3c3c';
          ctx.font = `${k === current ? 'bold ' : ''}9px "Segoe UI", Arial`;
          ctx.textAlign = 'right';
          ctx.fillText(String(k + 1), v.tx(a[0]) - 3, v.ty(a[1]) + 3);
        }
      });
    }
    if (trail.length > 1) {
      ctx.beginPath();
      trail.forEach(([x, y], i) => (i ? ctx.lineTo(v.tx(x), v.ty(y)) : ctx.moveTo(v.tx(x), v.ty(y))));
      ctx.strokeStyle = 'rgba(255,255,255,0.9)'; ctx.lineWidth = 1; ctx.stroke();
    }
    if (robot) {
      const x = v.tx(robot.x), y = v.ty(robot.y), h = (robot.heading * Math.PI) / 180;
      ctx.save(); ctx.translate(x, y); ctx.rotate(h);
      ctx.beginPath(); ctx.moveTo(0, -8); ctx.lineTo(5, 5); ctx.lineTo(-5, 5); ctx.closePath();
      ctx.fillStyle = '#F0C21E'; ctx.fill(); ctx.strokeStyle = '#333'; ctx.lineWidth = 1; ctx.stroke();
      ctx.restore();
    }
  }

  onMount(draw);
  $effect(() => { scene; robot; mission; trail.length; doneSwaths.length; current; draw(); });
</script>

<canvas bind:this={canvas} style="width:{W}px;height:{H}px"></canvas>

<style>
  canvas { display: block; border-radius: 2px; }
</style>
