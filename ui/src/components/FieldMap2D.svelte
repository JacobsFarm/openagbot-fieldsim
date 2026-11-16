<script>
  // Plattegrond: schermvullend, vlak 2D-bovenaanzicht met alleen lijnen. Perceelgrenzen, veldnamen, missie, spoor
  // en robot; geen texturen, schaduw of luchtfoto. Slepen = schuiven, wiel = zoomen, klik = veld kiezen,
  // dubbelklik = hetzelfde als in 3D (veld openen, robot neerzetten of punt zetten). Noorden boven.
  import { onMount } from 'svelte';

  let { scene = null, robot = null, mission = null, trail = [], doneSwaths = [], current = null, selected = null,
    path = null, points = [], starts = [], activeStart = null, layers = {}, follow = $bindable(false), onPick = null, onSelect = null,
    onInfo = null } = $props();
  let box, canvas;
  let w = 1, h = 1;                      // schermmaat (px)
  let cx = 0, cy = 0, s = 1;             // midden van het beeld (m) en schaal (px per m)
  let fitted = null;                     // de wereld waarop al is ingezoomd
  let hover = null, mouse = null, drag = null, raf = 0;

  const C = {
    bg: '#0b1114', grid: 'rgba(255,255,255,0.035)', area: 'rgba(255,255,255,0.16)',
    boundary: 'rgba(255,242,184,0.85)', fill: 'rgba(255,242,184,0.035)', hover: 'rgba(255,242,184,0.09)',
    accent: '#b9e34a', accentFill: 'rgba(185,227,74,0.10)', done: 'rgba(185,227,74,0.30)',
    water: 'rgba(79,179,255,0.55)', building: 'rgba(255,255,255,0.10)', ink: '#e9f0f2', muted: '#8fa1aa',
    amber: '#ffb23e', red: '#ff5a4d', blue: '#4fb3ff', line: 'rgba(255,255,255,0.55)',
  };
  const segCol = { rondje: '#4fb3ff', baan: C.line, bocht: 'rgba(255,255,255,0.28)', overgang: 'rgba(255,255,255,0.28)',
    transport: 'rgba(64,230,217,0.7)', doorgang: C.accent };
  const NICE = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];

  const tx = (x) => w / 2 + (x - cx) * s;
  const ty = (y) => h / 2 - (y - cy) * s;
  const wx = (px) => cx + (px - w / 2) / s;
  const wy = (py) => cy - (py - h / 2) / s;

  function inPolygon(x, y, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  const fieldAt = (x, y) => scene?.fields.find((f) => inPolygon(x, y, f.poly)) ?? null;

  function fit(pts) {
    if (!pts?.length) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    // Ruimte laten voor de HUD: links het dok, onder de rijbalk
    const padX = Math.min(260, w * 0.3), padY = Math.min(240, h * 0.3);
    s = Math.min((w - padX) / (x1 - x0 || 1), (h - padY) / (y1 - y0 || 1));
    cx = (x0 + x1) / 2; cy = (y0 + y1) / 2;
    follow = false;
    schedule();
  }
  export function fitAll() { fit(scene?.area?.length ? scene.area : scene?.fields.flatMap((f) => f.poly)); }
  export function fitField(id) { fit(scene?.fields.find((f) => f.id === id)?.poly); }

  function line(ctx, pts, close = false) {
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = tx(pts[i][0]), y = ty(pts[i][1]);
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    if (close) ctx.closePath();
  }

  function info() {
    const want = 90 / s;
    const m = NICE.reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a));
    onInfo?.({ x: mouse ? wx(mouse[0]) : null, y: mouse ? wy(mouse[1]) : null, field: hover, barM: m, barPx: m * s });
  }

  function schedule() {
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); });
  }

  function draw() {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, w, h);
    if (!scene) return;
    if (follow && robot && !drag) { cx = robot.x; cy = robot.y; }
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';

    // Raster op een rond aantal meters (zelfde stap als de schaalbalk)
    const step = NICE.find((m) => m * s >= 70) ?? 5000;
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.ceil(wx(0) / step) * step; x <= wx(w); x += step) { ctx.moveTo(Math.round(tx(x)) + 0.5, 0); ctx.lineTo(Math.round(tx(x)) + 0.5, h); }
    for (let y = Math.ceil(wy(h) / step) * step; y <= wy(0); y += step) { ctx.moveTo(0, Math.round(ty(y)) + 0.5); ctx.lineTo(w, Math.round(ty(y)) + 0.5); }
    ctx.stroke();

    if (scene.area?.length) {
      ctx.setLineDash([2, 6]); line(ctx, scene.area, true); ctx.strokeStyle = C.area; ctx.stroke(); ctx.setLineDash([]);
    }
    for (const b of scene.buildings ?? []) { line(ctx, b.poly, true); ctx.fillStyle = C.building; ctx.fill(); }
    ctx.strokeStyle = C.water;
    for (const d of scene.ditches ?? []) { line(ctx, d.line); ctx.lineWidth = Math.max(1, d.width * s); ctx.stroke(); }
    for (const sf of scene.surfaces ?? []) if (sf.kind === 'water') { line(ctx, sf.poly, true); ctx.fillStyle = C.water; ctx.fill(); }

    if (layers.farm) {
      ctx.lineWidth = 1;
      for (const z of [...(scene.no_go ?? []), ...(scene.zones ?? []).filter((z) => z.klass === 'verboden')]) {
        line(ctx, z.poly, true); ctx.fillStyle = 'rgba(255,90,77,0.10)'; ctx.fill(); ctx.strokeStyle = C.red; ctx.stroke();
      }
      ctx.setLineDash([4, 4]);
      for (const z of scene.zones ?? []) if (z.klass === 'niet_bewerken') { line(ctx, z.poly, true); ctx.strokeStyle = '#b58cff'; ctx.stroke(); }
      ctx.setLineDash([]);
      for (const p of scene.passages ?? []) { line(ctx, p.line); ctx.strokeStyle = 'rgba(185,227,74,0.5)'; ctx.lineWidth = Math.max(2, p.width * s); ctx.stroke(); }
    }

    // Percelen: dunne grens, het gekozen veld in de accentkleur
    const active = selected ?? mission?.field_id ?? null;
    for (const f of scene.fields) {
      const on = f.id === active, hov = f.id === hover?.id;
      line(ctx, f.poly, true);
      ctx.fillStyle = on ? C.accentFill : hov ? C.hover : C.fill; ctx.fill();
      if (layers.boundaries !== false || on) {
        ctx.strokeStyle = on ? C.accent : C.boundary; ctx.lineWidth = on ? 2.2 : 1.4; ctx.stroke();
      }
    }

    if (mission?.kind === 'veldmissie') {
      ctx.setLineDash([3, 4]); ctx.strokeStyle = 'rgba(255,242,184,0.4)'; ctx.lineWidth = 1;
      for (const poly of mission.allowed ?? []) if (poly[0]) { line(ctx, poly[0], true); ctx.stroke(); }
      ctx.setLineDash([]);
      for (const sg of mission.segments ?? []) {
        line(ctx, sg.pts);
        ctx.strokeStyle = sg.ok ? segCol[sg.type] ?? C.line : C.red;
        ctx.lineWidth = sg.ok ? 1 : 2.4; ctx.stroke();
      }
    } else if (mission?.swaths) {
      const ww = (mission.entry?.werkbreedte_m ?? 0) * s;
      const every = Math.max(1, Math.ceil(16 / (ww || 1)));
      ctx.font = '500 11px "Barlow", "Segoe UI", Arial'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      mission.swaths.forEach(([a, b], k) => {
        const done = doneSwaths.includes(k), now = k === current;
        ctx.beginPath(); ctx.moveTo(tx(a[0]), ty(a[1])); ctx.lineTo(tx(b[0]), ty(b[1]));
        if (done) { ctx.lineCap = 'butt'; ctx.strokeStyle = C.done; ctx.lineWidth = Math.max(1.5, ww); ctx.stroke(); ctx.lineCap = 'round'; }
        ctx.strokeStyle = now ? C.amber : done ? C.accent : C.line; ctx.lineWidth = now ? 2 : 1; ctx.stroke();
        if (k % every === 0 || now) {
          const dx = a[0] - b[0], dy = a[1] - b[1], l = Math.hypot(dx, dy) || 1;
          ctx.fillStyle = now ? C.amber : C.muted;
          ctx.fillText(String(k + 1), tx(a[0]) + (dx / l) * 12, ty(a[1]) - (dy / l) * 12);
        }
      });
    }

    if (path?.length > 1) {
      ctx.setLineDash([6, 5]); line(ctx, path); ctx.strokeStyle = C.amber; ctx.lineWidth = 1.4; ctx.stroke(); ctx.setLineDash([]);
    }
    if (points?.length) {
      const pts = points.map((p) => [p.x, p.y]);
      if (pts.length > 1) { line(ctx, pts); ctx.strokeStyle = 'rgba(255,160,46,0.5)'; ctx.lineWidth = 1; ctx.stroke(); }
      ctx.fillStyle = '#ffa02e';
      for (const p of pts) { ctx.beginPath(); ctx.arc(tx(p[0]), ty(p[1]), 3, 0, Math.PI * 2); ctx.fill(); }
    }
    if (trail.length > 1) { line(ctx, trail); ctx.strokeStyle = C.ink; ctx.lineWidth = 1.3; ctx.stroke(); }

    // Veldnamen, alleen als ze in het veld passen
    if (layers.labels !== false) {
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (const f of scene.fields) {
        const xs = f.poly.map((p) => p[0]), wide = (Math.max(...xs) - Math.min(...xs)) * s;
        const name = String(f.name ?? f.id).toUpperCase();
        ctx.font = '600 14px "Barlow Condensed", "Bahnschrift", "Segoe UI", sans-serif';
        if (ctx.measureText(name).width + 16 > wide) continue;
        const x = tx(f.center[0]), y = ty(f.center[1]);
        ctx.fillStyle = f.id === active ? C.accent : C.ink;
        ctx.fillText(name, x, y - 7);
        ctx.font = '11.5px "Barlow", "Segoe UI", Arial';
        ctx.fillStyle = C.muted;
        ctx.fillText(`${f.area_ha.toFixed(2)} ha`, x, y + 8);
      }
    }

    // Startpunten per veld: genummerd rondje; van het gekozen veld helder, het punt van de geplande missie geel
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = '600 11px "Barlow", "Segoe UI", Arial';
    for (const p of starts) {
      const x = tx(p.x), y = ty(p.y), mine = p.field === active, used = p.id === activeStart;
      ctx.globalAlpha = mine || active == null ? 1 : 0.45;
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2);
      ctx.fillStyle = used ? C.amber : C.blue; ctx.fill();
      ctx.strokeStyle = C.bg; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = C.bg; ctx.fillText(String(p.nr), x, y + 0.5);
      if (p.name && mine && s > 0.8) {
        ctx.fillStyle = C.ink; ctx.textAlign = 'left'; ctx.fillText(p.name, x + 12, y); ctx.textAlign = 'center';
      }
      ctx.globalAlpha = 1;
    }

    if (robot) {
      ctx.save(); ctx.translate(tx(robot.x), ty(robot.y)); ctx.rotate((robot.heading * Math.PI) / 180);
      ctx.beginPath(); ctx.moveTo(0, -11); ctx.lineTo(7, 7); ctx.lineTo(0, 3.5); ctx.lineTo(-7, 7); ctx.closePath();
      ctx.fillStyle = C.accent; ctx.fill(); ctx.strokeStyle = C.bg; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.restore();
    }
  }

  function local(e) {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  function down(e) {
    if (e.button !== 0) return;
    const [px, py] = local(e);
    drag = { px, py, cx, cy, moved: false };
    canvas.setPointerCapture(e.pointerId);
  }

  function move(e) {
    mouse = local(e);
    if (drag) {
      const dx = mouse[0] - drag.px, dy = mouse[1] - drag.py;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true; follow = false; canvas.style.cursor = 'grabbing';
      cx = drag.cx - dx / s; cy = drag.cy + dy / s;
    } else {
      const f = fieldAt(wx(mouse[0]), wy(mouse[1]));
      hover = f ? { id: f.id, name: f.name, area_ha: f.area_ha } : null;
    }
    info(); schedule();
  }

  function up(e) {
    const d = drag;
    drag = null;
    canvas.style.cursor = '';
    if (!d || d.moved) return;
    const [px, py] = local(e);
    const f = fieldAt(wx(px), wy(py));
    if (f) onSelect?.(f.id);
  }

  function dbl(e) {
    const [px, py] = local(e), x = wx(px), y = wy(py);
    const f = fieldAt(x, y);
    onPick?.({ x, y, field: f ? { kind: 'field', id: f.id, name: f.name } : null });
  }

  function wheel(e) {
    e.preventDefault();
    const [px, py] = local(e), x = wx(px), y = wy(py);
    s = Math.min(300, Math.max(0.02, s * Math.exp(-e.deltaY * 0.0015)));
    if (!follow) { cx = x - (px - w / 2) / s; cy = y + (py - h / 2) / s; }   // punt onder de muis blijft staan
    info(); schedule();
  }

  onMount(() => {
    const ro = new ResizeObserver(() => {
      w = box.clientWidth || 1; h = box.clientHeight || 1;
      if (scene && fitted !== scene) { fitted = scene; fitAll(); }
      info(); schedule();
    });
    ro.observe(box);
    canvas.addEventListener('wheel', wheel, { passive: false });
    return () => { ro.disconnect(); cancelAnimationFrame(raf); onInfo?.(null); };
  });

  $effect(() => {
    if (scene && fitted !== scene && w > 1) { fitted = scene; hover = null; fitAll(); info(); }
  });
  $effect(() => {
    scene; robot; mission; trail.length; doneSwaths.length; current; selected; path; points; starts; activeStart; follow;
    layers.boundaries; layers.labels; layers.farm;
    schedule();
  });
</script>

<div class="map2d" bind:this={box}>
  <canvas bind:this={canvas} onpointerdown={down} onpointermove={move} onpointerup={up}
    onpointerleave={() => { mouse = null; hover = null; info(); schedule(); }} ondblclick={dbl}></canvas>
</div>

<style>
  .map2d { position: absolute; inset: 0; background: #0b1114; animation: fade 0.15s; }
  canvas { display: block; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
</style>
