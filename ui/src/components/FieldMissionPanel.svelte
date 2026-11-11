<script>
  // Veldmissie: kopakkerrondjes langs de rand, dan het hart in rechte banen (of andersom), met bochten die de robot
  // echt kan rijden. Afstand tot sloot, weg en hek per soort. Rijden in de sim doet de echte robotsoftware met zijn
  // missievolger en zijn eigen geofence (kaart.json); missie en kaart kunnen als bestand mee of direct naar de robot.
  import { post } from '../lib/api.js';

  let { scene = null, selected = null, onPlan, onStarted } = $props();

  let width = $state(3.0);
  let overlap = $state(0.15);
  let maxOverlap = $state(100);       // laatste baan: meer overlap dan dit (%) = vervalt, strook aan de slootkant
  let rounds = $state(3);
  let order = $state('eerst');
  let direction = $state('rechts');
  let autoHeading = $state(true);
  let heading = $state(0);
  let pattern = $state('auto');
  let speed = $state(3.0);
  let turnSpeed = $state(2.0);
  let water = $state(1.0);
  let road = $state(1.0);
  let fence = $state(0.6);
  let building = $state(1.5);
  let date = $state(new Date().toISOString().slice(0, 10));     // voor nesten: alleen verboden in hun periode
  let dikeMode = $state('');
  let straight = $state(true);           // rondje 2 en verder recht (alleen het buitenste volgt de rand)
  let straightM = $state(1.0);
  let robotUrl = $state(localStorage.getItem('fieldsim.robotUrl') ?? 'http://192.168.4.1:8000');

  let busy = $state(false);
  let msg = $state('');
  let plan = $state.raw(null);
  let mode = $state('missie');           // missie = missievolger van de robot | waypoints = punt naar punt (oud)

  const kindLabel = { water: 'sloot', weg: 'weg', verhard: 'erf/pad', hek: 'hek', gebouw: 'gebouw', boom: 'boom',
    perceel: 'buurperceel', open: 'open', dijk: 'dijk', verboden: 'verboden gebied', nest: 'nest', gat: 'gat',
    marker: 'marker' };

  function settings() {
    return {
      work_width_m: width, overlap_m: overlap, max_swath_overlap_pct: Math.min(100, Math.max(0, Number(maxOverlap) || 0)), headland_rounds: rounds, headland_order: order,
      round_direction: direction, swath_heading_deg: autoHeading ? null : heading, order: pattern,
      speed_kmh: speed, turn_speed_kmh: turnSpeed,
      clearance: { water, weg: road, hek: fence, gebouw: building }, date, dike_mode: dikeMode || null,
      headland_straight: straight, headland_straighten_m: straightM,
    };
  }

  async function doPlan() {
    if (!selected) return;
    busy = true; msg = 'plannen...';
    try {
      plan = await post('/api/fieldmission/plan', { field: selected, settings: settings() });
      onPlan?.(plan);
      if (autoHeading && plan.swath_heading_deg != null) heading = Math.round(plan.swath_heading_deg);
      if (plan.settings?.clearance?.water != null) water = plan.settings.clearance.water;   // minstens slootzone + marge
      msg = '';
    } catch (err) { msg = String(err.message || err); plan = null; }
    busy = false;
  }

  async function drive(force = false) {
    if (!plan) await doPlan();
    if (!plan) return;
    busy = true; msg = 'starten...';
    try {
      const r = await post('/api/fieldmission/drive', { field: selected, place_robot: true, force, mode });
      onStarted?.(r);
      msg = r.status !== 'started' ? JSON.stringify(r.agbot)
        : r.mode === 'missie' ? `rijdt: missie '${r.mission}' met de missievolger en geofence van de robot`
          : `rijdt: ${r.waypoints} waypoints (punt naar punt)`;
    } catch (err) { msg = String(err.message || err); }
    busy = false;
  }

  async function push() {
    if (!plan) return;
    try { localStorage.setItem('fieldsim.robotUrl', robotUrl); } catch {}
    busy = true; msg = `naar ${robotUrl}...`;
    try {
      const r = await post('/api/fieldmission/push', { field: selected, robot_url: robotUrl });
      msg = r.old_software
        ? `${r.sent} waypoints naar de robot (${r.at}). De software daar kent nog geen missies en kaart: werk OpenAgbot bij.`
        : `kaart (${r.kaart?.stop_zones ?? 0} stop-gebieden) en missie '${r.mission}' staan op de robot (${r.at}); starten doe je daar, tabblad Veldmissie`;
    } catch (err) { msg = String(err.message || err); }
    busy = false;
  }

  async function stop() {
    try { await post('/api/agbot/stop'); msg = 'gestopt'; } catch (err) { msg = String(err.message || err); }
  }

  $effect(() => { selected; plan = null; msg = ''; });
  // Standaard van het veld (ingesteld in "Wereld bewerken") en de vrije ruimtes van de boerderij overnemen
  $effect(() => {
    const f = scene?.fields.find((x) => x.id === selected);
    if (f?.headland_rounds != null) rounds = f.headland_rounds;
    if (f?.work_width_m != null) width = f.work_width_m;
  });
  $effect(() => {
    const c = scene?.farm_settings?.clearance ?? {};
    const sf = scene?.farm_settings?.safety ?? {};
    water = Math.max(c.water ?? 1.0, (sf.ditch_zone_m ?? 2.0) + (sf.plan_margin_m ?? 0.3));
    if (c.weg != null) road = c.weg;
    if (c.hek != null) fence = c.hek;
    if (c.gebouw != null) building = c.gebouw;
  });
  const st = $derived(plan?.stats);
  const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)} u ${Math.round(m % 60)} min` : `${Math.round(m)} min`);
</script>

<div class="grid">
  <label>werkbreedte (m)<input type="number" min="0.5" max="12" step="0.25" bind:value={width} /></label>
  <label>overlap (m)<input type="number" min="0" max="1" step="0.05" bind:value={overlap} /></label>
  <label title="Overlapt de laatste baan meer dan dit met zijn buur, dan vervalt hij en blijft de smalle strook aan de slootkant liggen. 100 = altijd rijden">max overlap laatste baan (%)<input type="number" min="0" max="100" step="5" bind:value={maxOverlap} /></label>
  <label>rondjes kopakker<input type="number" min="0" max="8" step="1" bind:value={rounds} /></label>
  <label>kopakker
    <select bind:value={order} disabled={rounds === 0}>
      <option value="eerst">eerst (draaien op bewerkt)</option>
      <option value="laatst">laatst (sporen wegwerken)</option>
    </select>
  </label>
  <label>rondjes
    <select bind:value={direction}>
      <option value="rechts">met de klok mee</option>
      <option value="links">tegen de klok in</option>
    </select>
  </label>
  <label>volgorde banen
    <select bind:value={pattern}>
      <option value="auto">kortste (auto)</option>
      <option value="heen_en_weer">heen en weer</option>
      <option value="overslaan_1">1 overslaan</option>
      <option value="overslaan_2">2 overslaan</option>
      <option value="overslaan_3">3 overslaan</option>
      <option value="overslaan_4">4 overslaan</option>
      <option value="overslaan_5">5 overslaan</option>
      <option value="overslaan_6">6 overslaan</option>
    </select>
  </label>
  <label>baanrichting (°)
    <span class="row"><input type="checkbox" bind:checked={autoHeading} title="Beste richting uitrekenen" />
      <input type="number" min="0" max="179" step="1" bind:value={heading} disabled={autoHeading} /></span>
  </label>
  <label>snelheid / bocht (km/h)
    <span class="row"><input type="number" min="1.4" max="5.6" step="0.1" bind:value={speed} />
      <input type="number" min="1.4" max="5.6" step="0.1" bind:value={turnSpeed} /></span>
  </label>
</div>
<label class="chkrow"><input type="checkbox" bind:checked={straight} disabled={rounds < 2} />
  rondje 2 en verder recht (alleen het buitenste volgt de rand), bochtjes tot
  <input class="mini" type="number" min="0.2" max="3" step="0.1" bind:value={straightM} disabled={!straight || rounds < 2} /> m</label>
<div class="sub">Uit de buurt blijven (m vanaf het obstakel)</div>
<div class="grid four">
  <label>sloot<input type="number" min="0" max="5" step="0.1" bind:value={water} /></label>
  <label>weg<input type="number" min="0" max="5" step="0.1" bind:value={road} /></label>
  <label>hek<input type="number" min="0" max="5" step="0.1" bind:value={fence} /></label>
  <label>gebouw<input type="number" min="0" max="5" step="0.1" bind:value={building} /></label>
</div>

<div class="grid" style="margin-top:6px">
  <label>datum (nesten in hun periode)<input type="text" bind:value={date} placeholder="jjjj-mm-dd" /></label>
  <label>dijken
    <select bind:value={dikeMode}>
      <option value="">zoals ingesteld</option><option value="ontwijken">ontwijken</option>
      <option value="negeren">niet meenemen</option><option value="doorgang">alleen bij doorgang</option>
    </select>
  </label>
</div>

<div class="row" style="margin-top:8px">
  <button onclick={doPlan} disabled={!selected || busy}>Plan</button>
  <button class="primary" onclick={() => drive(false)} disabled={!selected || busy}>Rijden in de sim</button>
  <button class="danger" onclick={stop}>Stop</button>
  <select class="mode" bind:value={mode} title="Hoe de sim-robot de missie rijdt">
    <option value="missie">missievolger</option>
    <option value="waypoints">waypoints (oud)</option>
  </select>
</div>
{#if msg}<div class="msg small">{msg}</div>{/if}

{#if plan}
  <div class="stats num">
    <span><b>{st.coverage_allowed_pct}%</b> bewerkt van wat mag</span>
    <span><b>{(st.worked_m2 / 1e4).toFixed(2)}</b> van {(st.field_m2 / 1e4).toFixed(2)} ha perceel</span>
    <span><b>{fmtMin(st.time_min)}</b> · {(st.length_m / 1000).toFixed(1)} km</span>
    <span><b>{st.efficiency_pct}%</b> van de route werkend</span>
    <span>{plan.rounds} rondjes · banen {plan.swath_heading_deg ?? '-'}° · {plan.cells.length} {plan.cells.length === 1 ? 'vak' : 'vakken'}
      ({[...new Set(plan.cells.map((c) => c.pattern.replace('_', ' ')))].join(', ')})</span>
    <span>draaicirkel {plan.turn_radius_m} m · overlap {st.overlap_pct}%</span>
  </div>
  <div class="sub">Randen</div>
  <div class="edges">
    {#each plan.edge_summary as e}
      <span class="edge"><i class="k-{e.kind}"></i>{kindLabel[e.kind] ?? e.kind} {Math.round(e.length_m)} m
        <span class="muted">· {e.setback_m.toFixed(1)} m binnen de lijn</span></span>
    {/each}
  </div>
  {#if plan.warnings.length}
    <div class="warn small">{#each plan.warnings.slice(0, 4) as w}<div>{w}</div>{/each}
      {#if plan.warnings.length > 4}<div>… en nog {plan.warnings.length - 4}</div>{/if}</div>
  {/if}
  <div class="sub">Veiligheidscontrole</div>
  {#if plan.safety?.ok !== false}
    <div class="small okc">Geen stuk raakt een sloot, verboden gebied, nest, dijk, gebouw of weg.</div>
  {:else}
    {#each plan.safety.violations.slice(0, 5) as v}
      <div class="small badc">{v.segment_label}: {v.label}{v.name && v.name !== v.label ? ` ${v.name}` : ''}</div>
    {/each}
    <div class="tiny muted">Rode stukken op de kaart. De sim rijdt dit niet en het gaat niet naar de robot.
      <button class="link" onclick={() => drive(true)}>Toch rijden (om de bewaking te testen)</button></div>
  {/if}
  <div class="sub">Naar de robot</div>
  <div class="row">
    <a class="btn" href="/api/fieldmission/{selected}/veldmissie.json" download>veldmissie.json</a>
    <a class="btn" href="/api/safety/kaart.json" download>kaart.json</a>
    <a class="btn" href="/api/fieldmission/{selected}/waypoints.json" download>waypoints.json</a>
  </div>
  <div class="row" style="margin-top:6px">
    <input class="grow" type="text" bind:value={robotUrl} />
    <button onclick={push} disabled={busy} title="Zet kaart, missie en waypoints op de robot; start niets">Stuur</button>
  </div>
  <div class="tiny muted" style="margin-top:6px">
    veldmissie.json = de route per stuk met snelheid en werktuig omhoog/omlaag; de robot rijdt hem in het tabblad
    Veldmissie. kaart.json = alle sloten (met slootzone) en verboden gebieden van deze wereld: de robot stopt daar
    altijd voor, ook bij handmatig rijden. Stuur zet beide op de robot; in het tabblad Veldmissie kun je de
    bestanden ook zelf laden.
  </div>
{/if}

<style>
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  .grid.four { grid-template-columns: repeat(4, 1fr); }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  input[type='number'], select { width: 100%; }
  .row { display: flex; gap: 6px; align-items: center; }
  .grow { flex: 1; min-width: 0; }
  .sub { font-weight: 600; font-size: 12px; margin: 10px 0 4px; }
  .msg { margin-top: 6px; }
  .stats { display: flex; flex-direction: column; gap: 2px; margin-top: 8px; font-size: 12.5px; }
  .edges { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12px; }
  .edge i { display: inline-block; width: 10px; height: 4px; margin-right: 5px; vertical-align: middle; border-radius: 2px; }
  .k-water { background: #3389ff; } .k-weg { background: #222; outline: 1px solid #666; } .k-verhard { background: #999; }
  .k-hek { background: #9e6130; } .k-gebouw { background: #d93333; } .k-boom { background: #339940; }
  .k-perceel { background: #ffd11a; } .k-open { background: #8cd959; }
  .warn { margin-top: 6px; color: #ffb23e; }
  .chkrow { flex-direction: row; align-items: center; flex-wrap: wrap; gap: 5px; margin-top: 7px; color: var(--ink); }
  .mini { width: 58px !important; }
  .okc { color: var(--green); } .badc { color: #ff9a90; }
  .k-dijk { background: #c9a36a; } .k-verboden { background: #ff4a3d; } .k-nest { background: #ffb23e; }
  .k-gat { background: #ff6a5d; } .k-marker { background: #fff; }
  input[type='text'] { width: 100%; }
  .link { background: none; border: 0; padding: 0; min-height: 0; color: var(--amber); text-decoration: underline; font-size: 11.5px; }
  .mode { width: auto !important; font-size: 12px; }
  .btn { display: inline-block; padding: 4px 10px; border: 1px solid var(--line-strong); border-radius: 4px;
    color: inherit; text-decoration: none; font-size: 12.5px; }
</style>
