<script>
  // GPS-punten en onkruidplanten: zetten (dubbelklik), importeren, volgorde, in de sim rijden, naar de robot.
  import { get, post } from '../lib/api.js';

  let { scene, w3d, placing = $bindable(false), reload = 0 } = $props();
  let data = $state({ points: [], length_m: 0 });
  let field = $state('');
  let count = $state(60);
  let speed = $state(2.5);
  let robotUrl = $state('http://192.168.4.1:8000');
  let msg = $state('');

  async function refresh() { show(await get('/api/points')); }
  function show(d) { data = d; w3d?.setPoints(d.points); }
  $effect(() => { reload; refresh(); });

  async function run(fn) {
    msg = '';
    try { await fn(); } catch (e) { msg = String(e.message || e); }
  }

  async function importFile(e) {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    await run(async () => {
      show(await post('/api/points/import', { name: file.name, content: await file.text(), replace: true }));
      msg = `${data.points.length} punten uit ${file.name}`;
    });
    e.currentTarget.value = '';
  }

  async function push() {
    const ok = confirm(`${data.points.length} waypoints naar de ECHTE robot op ${robotUrl} sturen?\n` +
      'De waypoints op de robot worden vervangen. Er wordt niets gestart.');
    if (!ok) return;
    await run(async () => {
      const r = await post('/api/points/push', { robot_url: robotUrl });
      msg = `robot heeft nu ${r.robot_has} waypoints (${r.at})`;
    });
  }
</script>

<div class="sec">Punten <span class="muted small">{data.points.length} · route {data.length_m.toFixed(0)} m</span></div>
<div class="row wrap">
  <button class:active={placing} onclick={() => (placing = !placing)}>{placing ? 'Klaar met zetten' : 'Punten zetten'}</button>
  <button onclick={() => run(async () => show(await post('/api/points/undo')))} disabled={!data.points.length}>Laatste weg</button>
  <button onclick={() => run(async () => show(await post('/api/points/clear')))} disabled={!data.points.length}>Wis</button>
</div>
{#if placing}<div class="tiny muted">Dubbelklik op de grond zet een punt.</div>{/if}

<div class="sec">Onkruid</div>
<div class="row">
  <label class="file">Bestand (CSV/GeoJSON)<input type="file" accept=".csv,.json,.geojson,.txt" onchange={importFile} /></label>
</div>
<div class="row" style="margin-top:4px">
  <select bind:value={field} class="grow">
    <option value="">veld voor testplanten...</option>
    {#each scene?.fields ?? [] as f}<option value={f.id}>{f.name}</option>{/each}
  </select>
  <input type="number" min="5" max="500" step="5" bind:value={count} style="width:64px" />
  <button disabled={!field} onclick={() => run(async () => show(await post('/api/points/weeds', { field, count })))}>Maak</button>
</div>

<div class="sec">Route</div>
<div class="row wrap">
  <button onclick={() => run(async () => { const before = data.length_m; show(await post('/api/points/optimize'));
    msg = `kortste route: ${before.toFixed(0)} -> ${data.length_m.toFixed(0)} m`; })} disabled={data.points.length < 3}>Volgorde optimaliseren</button>
  <label class="inline">km/h <input type="number" min="1.4" max="5" step="0.1" bind:value={speed} style="width:56px" /></label>
  <button class="primary" disabled={!data.points.length}
    onclick={() => run(async () => { const r = await post('/api/points/drive', { speed_kmh: speed }); msg = `rijdt ${r.waypoints} punten in de sim`; })}>Rijden in de sim</button>
</div>

<div class="sec">Naar de robot</div>
<div class="row wrap">
  <a href="/api/points/waypoints.json" download><button disabled={!data.points.length}>Download waypoints.json</button></a>
</div>
<div class="row" style="margin-top:4px">
  <input type="text" bind:value={robotUrl} class="grow" title="Adres van de robot in het veld (zijn webinterface)" />
  <button disabled={!data.points.length} onclick={push}>Stuur</button>
</div>
{#if msg}<div class="small" style="margin-top:6px">{msg}</div>{/if}
<div class="tiny muted" style="margin-top:6px">Zelfde formaat als data/waypoints.json van OpenAgbot. Sturen vervangt alleen de
  waypoints op de robot; starten doe je daar zelf, onder toezicht.</div>

<style>
  .sec { font-weight: 700; margin: 12px 0 4px; }
  .sec:first-child { margin-top: 0; }
  .wrap { flex-wrap: wrap; }
  .file { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  .inline { font-size: 12px; color: var(--muted); display: flex; gap: 4px; align-items: center; }
  input[type='text'] { border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; font-size: 12px; }
</style>
