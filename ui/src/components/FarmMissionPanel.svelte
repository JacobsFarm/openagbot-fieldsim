<script>
  // Boerderijmissie: meer velden achter elkaar. De planner kiest de volgorde, plant elk veld en rijdt tussen de
  // velden alleen via de doorgangen (dam, hek, oversteek) die in "Wereld bewerken" getekend zijn.
  import { post } from '../lib/api.js';

  let { scene, onPlan, onStarted } = $props();

  let picked = $state({});               // veld-id -> meedoen (leeg = alle velden)
  let width = $state(3.0);
  let rounds = $state(2);
  let speed = $state(3.0);
  let transport = $state(3.0);
  let date = $state(new Date().toISOString().slice(0, 10));
  let dikeMode = $state('');
  let returnHome = $state(true);
  let pattern = $state('auto');
  let straight = $state(true);
  let busy = $state(false);
  let msg = $state('');
  let plan = $state.raw(null);

  const chosen = $derived(Object.entries(picked).filter(([, v]) => v).map(([k]) => k));
  const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)} u ${Math.round(m % 60)} min` : `${Math.round(m)} min`);

  function settings() {
    return { work_width_m: width, headland_rounds: rounds, speed_kmh: speed, transport_speed_kmh: transport, date,
      dike_mode: dikeMode || null, order: pattern, headland_straight: straight };
  }

  async function doPlan() {
    busy = true; msg = 'plannen (elk veld en de routes ertussen)...';
    try {
      plan = await post('/api/farm/plan', { fields: chosen.length ? chosen : null, settings: settings(), return_home: returnHome });
      onPlan?.(plan);
      msg = '';
    } catch (err) { msg = clean(err); plan = null; }
    busy = false;
  }

  async function drive(force = false) {
    if (!plan) await doPlan();
    if (!plan) return;
    busy = true; msg = 'starten...';
    try {
      const r = await post('/api/farm/drive', { force });
      onStarted?.(r);
      msg = `rijdt: ${r.waypoints} waypoints`;
    } catch (err) { msg = clean(err); }
    busy = false;
  }

  function clean(err) {
    const t = String(err.message || err);
    const m = t.match(/"detail":"(.*)"\}$/);
    return m ? m[1] : t;
  }

  async function stop() {
    try { await post('/api/agbot/stop'); msg = 'gestopt'; } catch (err) { msg = clean(err); }
  }
</script>

<div class="sec">Velden in de missie <span class="muted">(niets aangevinkt = alle)</span></div>
<div class="fields">
  {#each scene?.fields ?? [] as f}
    <label class="chk"><input type="checkbox" bind:checked={picked[f.id]} />{f.name} <span class="muted">{f.area_ha} ha</span></label>
  {/each}
</div>
<div class="grid">
  <label>werkbreedte (m)<input type="number" min="0.5" max="12" step="0.25" bind:value={width} /></label>
  <label>rondjes kopakker<input type="number" min="0" max="8" step="1" bind:value={rounds} /></label>
  <label>werksnelheid (km/h)<input type="number" min="1.4" max="5.6" step="0.1" bind:value={speed} /></label>
  <label>transport (km/h)<input type="number" min="1.4" max="5.6" step="0.1" bind:value={transport} /></label>
  <label>datum (nesten)<input type="text" bind:value={date} placeholder="jjjj-mm-dd" /></label>
  <label>dijken
    <select bind:value={dikeMode}>
      <option value="">zoals ingesteld</option><option value="ontwijken">ontwijken</option>
      <option value="negeren">niet meenemen</option><option value="doorgang">alleen bij doorgang</option>
    </select>
  </label>
</div>
<div class="grid" style="margin-top:6px">
  <label>volgorde banen
    <select bind:value={pattern}>
      <option value="auto">kortste (auto)</option><option value="heen_en_weer">heen en weer</option>
      {#each [1, 2, 3, 4, 5, 6] as k}<option value="overslaan_{k}">{k} overslaan</option>{/each}
    </select>
  </label>
  <label class="chk" style="align-self:end"><input type="checkbox" bind:checked={straight} />rondje 2+ recht</label>
</div>
<label class="chk" style="margin-top:6px"><input type="checkbox" bind:checked={returnHome} />na afloop terug naar de startplek</label>

<div class="row" style="margin-top:8px">
  <button onclick={doPlan} disabled={busy}>Plan</button>
  <button class="primary" onclick={() => drive(false)} disabled={busy}>Rijden in de sim</button>
  <button class="danger" onclick={stop}>Stop</button>
</div>
{#if msg}<div class="msg small">{msg}</div>{/if}

{#if plan}
  {@const st = plan.stats}
  <div class="stats num">
    <span><b>{st.fields}</b> velden · <b>{(st.worked_m2 / 1e4).toFixed(2)}</b> ha bewerkt</span>
    <span><b>{fmtMin(st.time_min)}</b> · {(st.length_m / 1000).toFixed(1)} km, waarvan {(st.transport_m / 1000).toFixed(2)} km transport</span>
    <span>{st.passages} doorgangen · {st.efficiency_pct}% van de route werkend</span>
  </div>
  <div class="sub">Volgorde</div>
  <ol class="order small">
    {#each plan.fields as f}<li>{f.name} <span class="muted">{f.rounds} rondjes · {fmtMin(f.time_min)} · {f.coverage_allowed_pct}%</span></li>{/each}
  </ol>
  {#if plan.unreachable.length}
    <div class="sub bad">Niet in de missie</div>
    {#each plan.unreachable as u}<div class="small bad">{u.name}: {u.reason}</div>{/each}
  {/if}
  <div class="sub">Veiligheidscontrole</div>
  {#if plan.safety.ok}
    <div class="small ok">Geen stuk van de route raakt een sloot, verboden gebied, nest, dijk, gebouw of weg.</div>
  {:else}
    {#each plan.safety.violations.slice(0, 5) as v}<div class="small bad">{v.segment_label}: {v.label}</div>{/each}
    <div class="tiny muted">De rode stukken op de kaart. De sim rijdt deze missie niet.
      <button class="link" onclick={() => drive(true)}>Toch rijden (om de bewaking te testen)</button></div>
  {/if}
  {#if plan.warnings.length}
    <div class="warn small">{#each plan.warnings.slice(0, 5) as w}<div>{w}</div>{/each}</div>
  {/if}
  <div class="sub">Naar de robot</div>
  <div class="row">
    <a class="btn" href="/api/farm/veldmissie.json" download>veldmissie.json</a>
    <a class="btn" href="/api/farm/waypoints.json" download>waypoints.json</a>
  </div>
  <div class="tiny muted" style="margin-top:6px">
    veldmissie.json (v2) bevat per stuk de snelheid, werktuig omhoog/omlaag, de doorgangen en de geofence. De huidige
    robotsoftware rijdt de waypoints; stoppen bij een doorgang of geofence vraagt de aanpassing uit docs/veldmissie.md.
  </div>
{/if}

<style>
  .sec { font-weight: 700; margin: 0 0 4px; }
  .fields { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 8px; max-height: 150px; overflow: auto; margin-bottom: 8px; }
  .chk { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--ink); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  label.chk { flex-direction: row; }
  input[type='number'], input[type='text'], select { width: 100%; }
  .row { display: flex; gap: 6px; align-items: center; }
  .sub { font-weight: 600; font-size: 12px; margin: 10px 0 4px; }
  .msg { margin-top: 6px; }
  .stats { display: flex; flex-direction: column; gap: 2px; margin-top: 8px; font-size: 12.5px; }
  .order { margin: 0; padding-left: 20px; }
  .bad { color: #ff9a90; } .ok { color: var(--green); } .warn { margin-top: 6px; color: #ffb23e; }
  .btn { display: inline-block; padding: 4px 10px; border: 1px solid var(--line-strong); border-radius: 4px; color: inherit;
    text-decoration: none; font-size: 12.5px; }
  .link { background: none; border: 0; padding: 0; min-height: 0; color: var(--amber); text-decoration: underline; font-size: 11.5px; }
</style>
