<script>
  // AB-missie per veld: plannen (bekijken), opslaan in data/ab_line.json van de sim-robot en starten.
  // De robotsoftware rekent zelf de baanvolgorde en de kopakkerbochten; die tekenen we daarna uit /api/agbot/plan.
  import { get, post } from '../lib/api.js';

  let { scene, selected = $bindable(null), onPlan, onStarted, onFocus } = $props();
  let width = $state(3.0);
  let headland = $state(8.0);
  let speed = $state(3.0);
  let skip = $state(1);
  let busy = $state(false);
  let msg = $state('');
  let plan = $state(null);

  async function preview() {
    if (!selected) return;
    busy = true; msg = '';
    try {
      plan = await get(`/api/missions/ab/${selected}?width=${width}&headland=${headland}`);
      onPlan?.(plan);
      const e = plan.entry;
      msg = `${e.aantal_banen} banen van ${e.baanlengte_m.toFixed(0)} m, koers ${plan.heading_deg.toFixed(0)}°`;
    } catch (err) { msg = String(err.message || err); }
    busy = false;
  }

  async function start() {
    if (!selected) return;
    busy = true; msg = 'opslaan en starten...';
    try {
      const r = await post('/api/missions/ab', { field: selected, width, headland, speed_kmh: speed, skip, start: true });
      plan = r.plan;
      onPlan?.(plan);
      onStarted?.(r);
      msg = r.agbot?.status === 'started' ? `gestart: volgorde ${r.agbot.baan_volgorde.slice(0, 8).map((b) => b + 1).join(' ')} ...`
        : JSON.stringify(r.agbot);
    } catch (err) { msg = String(err.message || err); }
    busy = false;
  }

  async function stop() {
    try { await post('/api/agbot/stop'); msg = 'gestopt'; } catch (err) { msg = String(err.message || err); }
  }

  async function home() {
    await post('/api/sim/reset', {});
    msg = 'robot op de startplek';
  }
</script>

<div class="sec">Velden</div>
<div class="fields">
  {#each scene?.fields ?? [] as f}
    <button class:active={selected === f.id} onclick={() => { selected = f.id; onFocus?.(f); preview(); }}>
      <b>{f.name}</b> <span class="muted">{f.crop} · {f.area_ha} ha</span>
    </button>
  {/each}
</div>

<div class="sec">AB-missie</div>
<div class="grid">
  <label>werkbreedte (m)<input type="number" min="0.5" max="12" step="0.25" bind:value={width} onchange={preview} /></label>
  <label>kopakker (m)<input type="number" min="3" max="30" step="1" bind:value={headland} onchange={preview} /></label>
  <label>snelheid (km/h)<input type="number" min="1.4" max="5.6" step="0.1" bind:value={speed} /></label>
  <label>overslaan<input type="number" min="0" max="4" step="1" bind:value={skip} /></label>
</div>
<div class="row" style="margin-top:8px">
  <button onclick={preview} disabled={!selected || busy}>Plan</button>
  <button class="primary" onclick={start} disabled={!selected || busy}>Opslaan + start</button>
  <button class="danger" onclick={stop}>Stop</button>
  <span class="grow"></span>
  <button onclick={home} title="Robot terug naar de startplek op het erf">Naar erf</button>
</div>
{#if msg}<div class="msg small">{msg}</div>{/if}
<div class="tiny muted" style="margin-top:6px">
  De missie komt in data/ab_line.json van de sim-robot, precies zoals AB_mission_maker.py hem maakt. Starten kan
  ook in de echte webinterface (tab Robot-UI, Landbouw).
</div>

<style>
  .sec { font-weight: 700; margin: 10px 0 4px; }
  .sec:first-child { margin-top: 0; }
  .fields { display: flex; flex-direction: column; gap: 4px; }
  .fields button { text-align: left; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  input { width: 100%; }
  .msg { margin-top: 6px; }
</style>
