<script>
  // Aanbouwdeel kiezen en bedienen; tellers uit de sim (openagbot_fieldsim/implements/state.py).
  import { post } from '../lib/api.js';

  let { catalog = [], current = $bindable(null), imp = null, onChange = () => {} } = $props();
  let msg = $state('');
  let rate = $state(500);

  const spec = $derived(catalog.find((c) => c.id === current) ?? null);
  const statusLabel = { open: 'open', done: 'geboord', missed_speed: 'te snel voorbij', out_of_reach: 'buiten bereik' };

  async function run(fn) {
    msg = '';
    try { await fn(); } catch (e) { msg = String(e.message || e); }
  }

  async function choose(id) {
    await run(async () => {
      await post('/api/implement', { id: id || null });
      current = id || null;
      const s = catalog.find((c) => c.id === id);
      if (s?.rate_l_ha) rate = s.rate_l_ha;
      onChange(s ?? null);
    });
  }

  const modeLabel = { auto: 'auto (omlaag in een veld)', down: 'omlaag', up: 'omhoog' };
</script>

<div class="sec">Werktuig</div>
<select value={current ?? ''} onchange={(e) => choose(e.currentTarget.value)} style="width:100%">
  <option value="">geen</option>
  {#each catalog as c}<option value={c.id} disabled={!c.poses.length}>{c.name}{c.poses.length ? '' : ' (geen model)'}</option>{/each}
</select>

{#if spec}
  <div class="tiny muted" style="margin-top:4px">{spec.note}</div>
  <div class="small" style="margin-top:6px">
    {#if spec.kind === 'strip'}werkbreedte {spec.width_m.toFixed(2)} m{:else}X-slede ±{(spec.reach_x_m * 100).toFixed(0)} cm · {imp?.cycle_s ?? spec.cycle_s} s per plant{/if}
    · {spec.work_speed_kmh} km/h
  </div>

  <div class="sec">Stand</div>
  <div class="row wrap">
    {#each ['auto', 'down', 'up'] as m}
      <button class:active={imp?.mode === m} onclick={() => run(() => post('/api/implement/set', { mode: m }))}>{modeLabel[m]}</button>
    {/each}
  </div>
  <div class="tiny muted">Nu: {imp?.lowered ? 'omlaag' : 'omhoog'}. OpenAgbot stuurt nog geen werktuig aan; de sim doet dat zelf.</div>

  {#if spec.rate_l_ha}
    <div class="sec">Dosis</div>
    <div class="row">
      <input type="number" min="0" max="2000" step="10" bind:value={rate} style="width:80px" /> <span class="small muted">l/ha per vloeistof</span>
      <button onclick={() => run(() => post('/api/implement/set', { rate_l_ha: rate }))}>Zet</button>
    </div>
  {/if}

  <div class="sec">Resultaat</div>
  {#if imp}
    {#if spec.kind === 'strip'}
      <div class="small">{(imp.area_ha).toFixed(3)} ha bewerkt · {imp.distance_m.toFixed(0)} m</div>
      {#if spec.rate_l_ha}<div class="small">{imp.liquid_l.toFixed(1)} l toegediend ({imp.rate_l_ha} l/ha)</div>{/if}
    {:else}
      <div class="small">
        {#each Object.entries(imp.targets ?? {}) as [k, n]}<span class="tag {k}">{statusLabel[k] ?? k}: {n}</span> {/each}
        {#if !Object.keys(imp.targets ?? {}).length}geen planten: maak of importeer punten (tab Punten) en klik hieronder{/if}
      </div>
      {#if imp.dx_mean_m !== null && imp.dx_mean_m !== undefined}<div class="small">gemiddelde zijafstand plant–spil {(imp.dx_mean_m * 100).toFixed(1)} cm</div>{/if}
      {#if imp.drilling_s > 0}<div class="small"><b>boort</b> {imp.drilling_s.toFixed(1)} / {imp.cycle_s} s</div>{/if}
      <div class="row" style="margin-top:4px">
        <button onclick={() => run(async () => { const r = await post('/api/implement/targets'); msg = `${r.targets} planten geladen`; onChange(spec); })}>Punten als planten</button>
      </div>
      <div class="tiny muted">Een plant is geboord als de robot met de spil erboven {imp.cycle_s} s stilstaat. Rijdt OpenAgbot er
        zonder te stoppen langs, dan telt hij als "te snel voorbij".</div>
    {/if}
    <div class="small">{imp.energy_wh.toFixed(0)} Wh werktuig</div>
    <div class="row" style="margin-top:4px"><button onclick={() => run(() => post('/api/implement/reset'))}>Wis resultaat</button></div>
  {/if}
{/if}
{#if msg}<div class="small" style="margin-top:6px">{msg}</div>{/if}

<style>
  .sec { font-weight: 700; margin: 12px 0 4px; }
  .sec:first-child { margin-top: 0; }
  .wrap { flex-wrap: wrap; }
  .tag { white-space: nowrap; }
  .tag.done { color: var(--green); }
  .tag.missed_speed { color: #d02a2a; }
  .tag.out_of_reach { color: #8e44ad; }
</style>
