<script>
  // Ritlog afspelen (uit het veld of uit de sim), kerngetallen tonen en de route in de sim laten narijden.
  import { onDestroy } from 'svelte';
  import { get, post } from '../lib/api.js';

  let { w3d, onFocus } = $props();
  let logs = $state([]);
  let pick = $state('');
  let data = $state(null);
  let idx = $state(0);
  let playing = $state(false);
  let speed = $state(10);
  let msg = $state('');
  let timer = null;

  get('/api/logs').then((l) => (logs = l)).catch((e) => (msg = String(e.message || e)));

  async function load() {
    if (!pick) return;
    const [source, file] = pick.split('/');
    msg = 'laden...';
    data = await get(`/api/logs/${source}/${file}?step=2`);
    idx = 0;
    w3d.setReplay(data.x, data.y);
    const s = data.stats;
    if (s.bbox) onFocus?.((s.bbox[0] + s.bbox[2]) / 2, (s.bbox[1] + s.bbox[3]) / 2,
      Math.max(60, Math.hypot(s.bbox[2] - s.bbox[0], s.bbox[3] - s.bbox[1]) * 1.2));
    msg = s.center_distance_m > 3000 ? `let op: deze rit ligt ${(s.center_distance_m / 1000).toFixed(1)} km van deze wereld` : '';
    show();
  }

  function show() {
    if (!data) return;
    w3d.setGhost(data.x[idx], data.y[idx], data.heading[idx] ?? 0);
  }

  function toggle() {
    playing = !playing;
    clearInterval(timer);
    if (playing) {
      let last = performance.now();
      timer = setInterval(() => {
        const now = performance.now();
        const tWant = data.t[idx] + ((now - last) / 1000) * speed;
        last = now;
        while (idx < data.t.length - 1 && data.t[idx] < tWant) idx++;
        show();
        if (idx >= data.t.length - 1) { playing = false; clearInterval(timer); }
      }, 50);
    }
  }

  async function redrive() {
    const [source, file] = pick.split('/');
    msg = 'route naar de sim-robot...';
    try {
      const r = await post('/api/logs/redrive', { source, file, spacing_m: 2.0, speed_kmh: 3.0 });
      msg = `narijden gestart: ${r.waypoints} waypoints (pure pursuit)`;
    } catch (e) { msg = String(e.message || e); }
  }

  function clear() { data = null; w3d.setReplay(null); playing = false; clearInterval(timer); }
  onDestroy(() => clearInterval(timer));

  const fmt = (v, d = 2) => (v === null || v === undefined ? '-' : (+v).toFixed(d));
  const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
</script>

<div class="sec">Ritlogs</div>
<div class="row">
  <select class="grow" bind:value={pick} onchange={load}>
    <option value="">kies een rit...</option>
    {#each logs as l}<option value={`${l.source}/${l.file}`}>{l.source}: {l.file.replace('rit_', '').replace('.csv', '')}</option>{/each}
  </select>
  {#if data}<button onclick={clear}>Wis</button>{/if}
</div>

{#if data}
  {@const s = data.stats}
  <div class="stats small">
    <div><span class="muted">duur</span> {clock(s.duration_s)} · <span class="muted">afstand</span> {fmt(s.distance_m, 0)} m</div>
    <div><span class="muted">afwijking op de baan</span> mediaan {fmt(s.xte_median_m * 100, 1)} cm · P95 {fmt(s.xte_p95_m * 100, 1)} cm · max {fmt(s.xte_max_m * 100, 0)} cm</div>
  </div>
  <input type="range" min="0" max={data.t.length - 1} bind:value={idx} oninput={show} style="width:100%" />
  <div class="row">
    <button onclick={toggle}>{playing ? 'Pauze' : 'Afspelen'}</button>
    <select bind:value={speed}>{#each [1, 5, 10, 30] as v}<option value={v}>{v}×</option>{/each}</select>
    <span class="grow"></span>
    <span class="small">{clock(data.t[idx])} · {data.mode[idx]}</span>
  </div>
  <div class="vals small">
    <div><span class="muted">koers</span> {fmt(data.heading[idx], 1)}°</div>
    <div><span class="muted">stuur</span> {fmt(data.steer[idx], 1)}°</div>
    <div><span class="muted">snelheid</span> {fmt(data.speed_kmh[idx])} / {fmt(data.target_kmh[idx])} km/h</div>
    <div><span class="muted">afwijking</span> {fmt(data.xte[idx] * 100, 1)} cm</div>
    <div><span class="muted">pitch</span> {fmt(data.pitch[idx], 2)}°</div>
    <div><span class="muted">accu</span> {fmt(data.battery_v[idx], 1)} V</div>
  </div>
  <button class="primary" style="margin-top:8px" onclick={redrive}>Route in de sim narijden</button>
{/if}
{#if msg}<div class="small" style="margin-top:6px">{msg}</div>{/if}
<div class="tiny muted" style="margin-top:6px">Bronnen: sim (runtime/agbot/data/logs), robot (data/logs van OpenAgbot),
  wereld (worlds/&lt;naam&gt;/logs). Echte ritten passen bij de wereld van die plek.</div>

<style>
  .sec { font-weight: 700; margin-bottom: 4px; }
  .stats { margin: 8px 0; line-height: 1.5; }
  .vals { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; margin-top: 6px; }
</style>
