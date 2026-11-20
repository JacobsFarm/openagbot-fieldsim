<script>
  // Missie: één paneel voor alles. Bovenaan de schuif simpel/geavanceerd, dan de wachtrij met velden (één veld =
  // veldmissie, meer velden = boerderijmissie via de doorgangen), de instellingen, één actiebalk en het resultaat.
  // Een wachtrij kan per wereld opgeslagen worden (worlds/<naam>/missions/wachtrijen.json) om later opnieuw te rijden.
  import { del, errorText, get, post } from '../lib/api.js';
  import Icon from './Icon.svelte';

  let { scene, selected = $bindable(null), onPlan, onStarted, onFocus, startPoints = [],
    placingStart = $bindable(false), onStartPoints = null, wide = false } = $props();

  const LS = 'fieldsim.mission.v2';
  const stored = (() => { try { return JSON.parse(localStorage.getItem(LS) ?? '{}'); } catch { return {}; } })();
  const today = () => new Date().toLocaleDateString('sv-SE');      // jjjj-mm-dd in de lokale tijd (niet UTC)

  const DEFAULTS = {
    width: 3.0, rounds: 3, speed: 3.0,                        // simpel
    overlap: 0.15, maxOverlap: 100, order: 'eerst', direction: 'rechts', autoHeading: true, heading: 0,
    pattern: 'auto', turnSpeed: 2.0, straight: true, straightM: 1.0,
    water: 1.0, road: 1.0, fence: 0.6, building: 1.5, dikeMode: '',
    transport: 3.0, transportWidth: 1.2,
    abHeadland: 8.0, abSkip: 1,
  };
  let advanced = $state(!!stored.advanced);
  let s = $state({ ...DEFAULTS, ...(stored.settings ?? {}) });
  let date = $state(today());                                  // nesten: altijd de dag van rijden, niet opslaan
  let kind = $state('veld');                                   // veld | ab (alleen geavanceerd)
  let driveMode = $state('missie');                            // missievolger | waypoints (oud)
  let queue = $state([...(stored.queue ?? [])]);               // veld-id's in rijvolgorde
  let optimize = $state(!!stored.optimize);                    // planner kiest de volgorde
  let startFrom = $state('startplek');                         // boerderij: startplek | robot | vloot:<id>
  let returnHome = $state(true);
  let startId = $state('');                                    // één veld: startpunt ('' = dichtst bij de robot)
  let robotUrl = $state(localStorage.getItem('fieldsim.robotUrl') ?? 'http://192.168.4.1:8000');

  let busy = $state(false);
  let msg = $state('');
  let msgBad = $state(false);
  let plan = $state.raw(null);
  let planKind = $state('');                                   // veld | boerderij | ab
  let planKey = $state('');
  let fleet = $state.raw([]);
  let saved = $state.raw([]);
  let loadedId = $state(null);
  let saveName = $state('');
  let showSave = $state(false);

  get('/api/fleet').then((d) => (fleet = d.robots.filter((r) => r.enabled))).catch(() => {});
  async function loadSaved() { try { saved = (await get('/api/queues')).queues; } catch { saved = []; } }
  $effect(() => { scene; loadSaved(); });

  const fields = $derived(scene?.fields ?? []);
  const byId = $derived(Object.fromEntries(fields.map((f) => [f.id, f])));
  // Velden die niet (meer) in deze wereld bestaan vallen uit de rij
  $effect(() => { if (scene && queue.some((id) => !byId[id])) queue = queue.filter((id) => byId[id]); });
  const effKind = $derived(advanced ? kind : 'veld');
  const single = $derived(queue.length === 1 || effKind === 'ab');
  const target = $derived(effKind === 'ab' ? 'ab' : queue.length > 1 ? 'boerderij' : 'veld');
  const starts = $derived(single ? startPoints.filter((p) => p.field === queue[0]) : []);
  const startLabel = (p) => (p.name ? `${p.nr} · ${p.name}` : `startpunt ${p.nr}`);
  const totalHa = $derived(queue.reduce((a, id) => a + (byId[id]?.area_ha ?? 0), 0));
  const fmtMin = (x) => { const m = Math.round(x); return m >= 60 ? `${Math.floor(m / 60)} u ${m % 60} min` : `${m} min`; };

  // Onthouden in de browser: stand, instellingen en de huidige rij
  $effect(() => {
    const data = { advanced, settings: $state.snapshot(s), queue: $state.snapshot(queue), optimize };
    try { localStorage.setItem(LS, JSON.stringify(data)); } catch { /* privé-venster */ }
  });

  // Veld gekozen op de kaart (dubbelklik): achteraan in de rij
  let lastSelected = selected;
  $effect(() => {
    const id = selected;
    if (id && id !== lastSelected && byId[id] && !queue.includes(id)) add(id);
    lastSelected = id;
  });
  $effect(() => { if (startId && !starts.some((p) => p.id === startId)) startId = ''; });
  $effect(() => { if (!single) placingStart = false; });
  $effect(() => () => { placingStart = false; });               // paneel dicht: geen startpunten meer zetten

  // Vrije ruimte van de boerderij overnemen (sloot minstens slootzone + marge)
  $effect(() => {
    const c = scene?.farm_settings?.clearance ?? {}, sf = scene?.farm_settings?.safety ?? {};
    s.water = Math.max(c.water ?? 1.0, (sf.ditch_zone_m ?? 2.0) + (sf.plan_margin_m ?? 0.3));
    if (c.weg != null) s.road = c.weg;
    if (c.hek != null) s.fence = c.hek;
    if (c.gebouw != null) s.building = c.gebouw;
  });

  // ------------------------------------------------------------------ wachtrij
  function add(id) {
    if (queue.includes(id)) return;
    if (!queue.length) {                                       // eerste veld: zijn eigen standaard overnemen
      const f = byId[id];
      if (f?.headland_rounds != null) s.rounds = f.headland_rounds;
      if (f?.work_width_m != null) s.width = f.work_width_m;
    }
    queue = [...queue, id];
    selected = id;
  }
  function toggle(f) {
    if (queue.includes(f.id)) queue = queue.filter((x) => x !== f.id);
    else add(f.id);
    onFocus?.(f);
  }
  function move(i, d) {
    const j = i + d;
    if (j < 0 || j >= queue.length) return;
    const q = [...queue];
    [q[i], q[j]] = [q[j], q[i]];
    queue = q;
  }
  function clearQueue() { queue = []; loadedId = null; resetPlan(); }

  // Slepen om de volgorde te veranderen
  let dragFrom = $state(-1);
  let dragOver = $state(-1);
  function drop(i) {
    if (dragFrom < 0 || dragFrom === i) { dragFrom = dragOver = -1; return; }
    const q = [...queue];
    const [x] = q.splice(dragFrom, 1);
    q.splice(i, 0, x);
    queue = q;
    dragFrom = dragOver = -1;
  }

  // ------------------------------------------------------------------ opgeslagen wachtrijen
  async function saveQueue() {
    const name = saveName.trim();
    if (!name || !queue.length) return;
    const settings = $state.snapshot(s);
    try {
      const r = await post('/api/queues', { id: loadedId, name, fields: $state.snapshot(queue), optimize, settings,
        start: startFrom, return_home: returnHome, start_point: single ? startId || null : null, advanced });
      saved = r.queues; loadedId = r.saved; showSave = false;
      say(`wachtrij '${name}' opgeslagen`);
    } catch (e) { say(errorText(e), true); }
  }

  function loadQueue(q) {
    const ok = q.fields.filter((id) => byId[id]);
    queue = ok;
    s = { ...DEFAULTS, ...s, ...(q.settings ?? {}) };
    optimize = !!q.optimize;
    startFrom = q.start ?? 'startplek';
    if (startFrom.startsWith('vloot:') && !fleet.some((r) => `vloot:${r.id}` === startFrom)) startFrom = 'startplek';
    returnHome = q.return_home ?? true;
    startId = q.start_point ?? '';
    if (q.advanced) advanced = true;
    loadedId = q.id; saveName = q.name;
    resetPlan();
    if (ok[0]) { selected = lastSelected = ok[0]; onFocus?.(byId[ok[0]]); }
    say(q.missing?.length ? `'${q.name}' geladen; niet meer in deze wereld: ${q.missing.join(', ')}` : `'${q.name}' geladen`,
      !!q.missing?.length);
  }

  async function removeQueue(q) {
    if (!confirm(`Wachtrij '${q.name}' weggooien?`)) return;
    try { saved = (await del(`/api/queues/${q.id}`)).queues; if (loadedId === q.id) loadedId = null; }
    catch (e) { say(errorText(e), true); }
  }

  // ------------------------------------------------------------------ plannen en rijden
  function say(t, bad = false) { msg = t; msgBad = bad; }
  function resetPlan() { plan = null; planKey = ''; onPlan?.(null); }

  function apiSettings() {
    return {
      work_width_m: s.width, overlap_m: s.overlap, max_swath_overlap_pct: Math.min(100, Math.max(0, Number(s.maxOverlap) || 0)),
      headland_rounds: s.rounds, headland_order: s.order, round_direction: s.direction,
      swath_heading_deg: s.autoHeading ? null : s.heading, order: s.pattern,
      speed_kmh: s.speed, turn_speed_kmh: s.turnSpeed, transport_speed_kmh: s.transport, transport_width_m: s.transportWidth,
      clearance: { water: s.water, weg: s.road, hek: s.fence, gebouw: s.building }, date, dike_mode: s.dikeMode || null,
      headland_straight: s.straight, headland_straighten_m: s.straightM,
    };
  }
  // Alles wat de route verandert; anders dan bij het plannen = opnieuw plannen voor het rijden
  const currentKey = $derived(JSON.stringify([target, queue, apiSettings(), optimize, startFrom, returnHome, startId]));
  const stale = $derived(!!plan && planKey !== currentKey);

  async function doPlan() {
    if (!queue.length) return false;
    busy = true; say(target === 'boerderij' ? 'plannen (elk veld en de routes ertussen)...' : 'plannen...');
    try {
      let p;
      if (target === 'ab') {
        p = await get(`/api/missions/ab/${queue[0]}?width=${s.width}&headland=${s.abHeadland}`);
      } else if (target === 'veld') {
        p = await post('/api/fieldmission/plan', { field: queue[0], settings: apiSettings(), start_point: startId || null });
        if (s.autoHeading && p.swath_heading_deg != null) s.heading = Math.round(p.swath_heading_deg);
        if (p.settings?.clearance?.water != null) s.water = p.settings.clearance.water;
      } else {
        p = await post('/api/farm/plan', { fields: $state.snapshot(queue), settings: apiSettings(), return_home: returnHome,
          start: startFrom, order: optimize ? 'auto' : 'gegeven' });
      }
      plan = p; planKind = target; onPlan?.(p);
      planKey = JSON.stringify([target, queue, apiSettings(), optimize, startFrom, returnHome, startId]);
      say(target === 'ab' ? `${p.entry.aantal_banen} banen van ${p.entry.baanlengte_m.toFixed(0)} m, koers ${p.heading_deg.toFixed(0)}°` : '');
      busy = false;
      return true;
    } catch (e) {
      say(errorText(e), true); resetPlan();
      busy = false;
      return false;
    }
  }

  async function drive(force = false) {
    if ((!plan || stale) && !(await doPlan())) return;
    busy = true; say('starten...');
    try {
      let r;
      if (planKind === 'ab') {
        r = await post('/api/missions/ab', { field: queue[0], width: s.width, headland: s.abHeadland, speed_kmh: s.speed,
          skip: s.abSkip, start: true });
        plan = r.plan; onPlan?.(plan);
        say(r.agbot?.status === 'started' ? `rijdt: baanvolgorde ${r.agbot.baan_volgorde.slice(0, 8).map((b) => b + 1).join(' ')} ...`
          : JSON.stringify(r.agbot));
      } else if (planKind === 'veld') {
        r = await post('/api/fieldmission/drive', { field: queue[0], place_robot: true, force, mode: driveMode });
        say(r.status !== 'started' ? JSON.stringify(r.agbot) : r.mode === 'missie' ? 'rijdt met de missievolger en geofence van de robot'
          : `rijdt: ${r.waypoints} waypoints (punt naar punt)`);
      } else {
        r = await post('/api/farm/drive', { force, mode: driveMode });
        say(`rijdt: ${plan.fields.length} velden, ${r.waypoints} waypoints`);
      }
      onStarted?.(r);
      if (loadedId) post(`/api/queues/${loadedId}/used`).then((d) => (saved = d.queues)).catch(() => {});
    } catch (e) { say(errorText(e), true); }
    busy = false;
  }

  async function stop() {
    try { await post('/api/agbot/stop'); say('gestopt'); } catch (e) { say(errorText(e), true); }
  }

  async function push() {
    if ((!plan || stale) && !(await doPlan())) return;
    try { localStorage.setItem('fieldsim.robotUrl', robotUrl); } catch {}
    busy = true; say(`naar ${robotUrl}...`);
    try {
      const r = planKind === 'boerderij' ? await post('/api/farm/push', { robot_url: robotUrl })
        : await post('/api/fieldmission/push', { field: queue[0], robot_url: robotUrl });
      say(r.old_software ? `${r.sent} waypoints naar de robot (${r.at}). De software daar kent nog geen missies en kaart: werk OpenAgbot bij.`
        : `kaart en missie '${r.mission}' staan op de robot (${r.at}); starten doe je daar, tabblad Veldmissie`);
    } catch (e) { say(errorText(e), true); }
    busy = false;
  }

  async function startOp(path, body) {
    try { onStartPoints?.((await post(path, body)).start_points); resetPlan(); }
    catch (e) { say(errorText(e), true); }
  }

  const files = $derived(planKind === 'boerderij' ? '/api/farm' : `/api/fieldmission/${queue[0]}`);
  const kindLabel = { water: 'sloot', weg: 'weg', verhard: 'erf/pad', hek: 'hek', gebouw: 'gebouw', boom: 'boom',
    perceel: 'buurperceel', open: 'open', dijk: 'dijk', verboden: 'verboden gebied', nest: 'nest', gat: 'gat', marker: 'marker' };
  const lanes = $derived((plan?.graph?.passages ?? []).filter((p) => p.kind === 'rijpad'));
  const stats = $derived(plan?.stats);
  const safetyOk = $derived(plan?.safety?.ok !== false);
</script>

<!-- Schuif: simpel / geavanceerd -->
<div class="top">
  <div class="slider" role="radiogroup" aria-label="Weergave van het missiepaneel">
    <span class="knob" class:right={advanced}></span>
    <button role="radio" aria-checked={!advanced} class:on={!advanced} onclick={() => (advanced = false)}>Simpel</button>
    <button role="radio" aria-checked={advanced} class:on={advanced} onclick={() => (advanced = true)}>Geavanceerd</button>
  </div>
  {#if advanced}
    <select class="kind" bind:value={kind} title="Soort missie" onchange={resetPlan}>
      <option value="veld">Veld- / boerderijmissie</option>
      <option value="ab">AB-lijn (robot plant zelf)</option>
    </select>
  {/if}
</div>

<div class="layout" class:wide>
  <div class="col">
    <!-- Wachtrij -->
    <section>
      <div class="head">
        <span class="h">Wachtrij</span>
        <span class="muted small">{queue.length ? `${queue.length} ${queue.length === 1 ? 'veld' : 'velden'} · ${totalHa.toFixed(2)} ha` : 'leeg'}</span>
        <span class="grow"></span>
        {#if queue.length}<button class="ghost small" onclick={clearQueue}>Leegmaken</button>{/if}
      </div>
      {#if queue.length}
        <ol class="queue">
          {#each queue as id, i (id)}
            {@const f = byId[id]}
            {@const pf = plan?.fields?.find((x) => x.id === id)}
            <li draggable="true" class:over={dragOver === i} class:dim={effKind === 'ab' && i > 0}
              ondragstart={() => (dragFrom = i)} ondragover={(e) => { e.preventDefault(); dragOver = i; }}
              ondragleave={() => (dragOver = -1)} ondrop={() => drop(i)} ondragend={() => (dragFrom = dragOver = -1)}>
              <span class="nr num">{optimize && queue.length > 1 ? '·' : i + 1}</span>
              <button class="name" onclick={() => { selected = lastSelected = id; onFocus?.(f); }} title="In beeld brengen">
                <b>{f?.name ?? id}</b>
                <span class="muted">{f?.area_ha ?? '?'} ha{#if pf} · {fmtMin(pf.time_min)}{/if}</span>
              </button>
              <span class="ops">
                <button class="ghost" disabled={i === 0 || optimize} onclick={() => move(i, -1)} title="Eerder"><Icon name="up" size={15} /></button>
                <button class="ghost" disabled={i === queue.length - 1 || optimize} onclick={() => move(i, 1)} title="Later"><Icon name="down" size={15} /></button>
                <button class="ghost del" onclick={() => (queue = queue.filter((x) => x !== id))} title="Uit de wachtrij"><Icon name="close" size={14} /></button>
              </span>
            </li>
          {/each}
        </ol>
        {#if queue.length > 1 && effKind !== 'ab'}
          <label class="chk"><input type="checkbox" bind:checked={optimize} />volgorde laten kiezen (kortste route)</label>
        {/if}
        {#if effKind === 'ab' && queue.length > 1}<div class="tiny warnc">AB-lijn rijdt alleen het eerste veld.</div>{/if}
      {:else}
        <div class="empty small">Klik hieronder op een veld (of dubbelklik op de kaart) om het in de rij te zetten. Eén
          veld = veldmissie, meer velden = boerderijmissie via de doorgangen.</div>
      {/if}

      <div class="fieldlist" class:cols={wide}>
        {#each fields as f}
          {@const n = queue.indexOf(f.id)}
          <button class="fieldbtn" class:in={n >= 0} onclick={() => toggle(f)} title={n >= 0 ? 'Uit de wachtrij' : 'In de wachtrij'}>
            <span class="badge num">{#if n >= 0}{n + 1}{:else}<Icon name="plus" size={13} />{/if}</span>
            <span class="fn">{f.name}</span><span class="muted">{f.area_ha} ha</span>
          </button>
        {/each}
      </div>
      {#if queue.length < fields.length && fields.length > 1}
        <button class="ghost small addall" onclick={() => fields.forEach((f) => add(f.id))}>Alle velden toevoegen</button>
      {/if}
    </section>

    <!-- Opgeslagen wachtrijen -->
    <section>
      <div class="head">
        <span class="h">Opgeslagen</span>
        <span class="grow"></span>
        <button class="ghost small" disabled={!queue.length} onclick={() => { showSave = !showSave; if (!saveName) saveName = queue.map((id) => byId[id]?.name).join(' + ').slice(0, 40); }}>
          <Icon name="save" size={14} />Wachtrij opslaan</button>
      </div>
      {#if showSave}
        <div class="row">
          <input class="grow" type="text" bind:value={saveName} placeholder="naam, bv. 'Bemesten voorjaar'"
            onkeydown={(e) => e.key === 'Enter' && saveQueue()} />
          <button class="primary" disabled={!saveName.trim()} onclick={saveQueue}>Opslaan</button>
        </div>
        <div class="tiny muted">Bewaart de velden, de volgorde en alle instellingen bij deze wereld. Zelfde naam = bijwerken.</div>
      {/if}
      {#if saved.length}
        <div class="saved">
          {#each saved as q (q.id)}
            <div class="sq" class:on={q.id === loadedId}>
              <button class="load" onclick={() => loadQueue(q)} title="Laden: {q.field_names.join(' → ')}">
                <b>{q.name}</b>
                <span class="muted tiny">{q.fields.length} {q.fields.length === 1 ? 'veld' : 'velden'}
                  · {q.last_used ? `gereden ${q.last_used.slice(0, 10)}` : `opgeslagen ${q.saved_at.slice(0, 10)}`}
                  {#if q.missing.length}<span class="warnc"> · {q.missing.length} ontbreekt</span>{/if}</span>
              </button>
              <button class="ghost del" onclick={() => removeQueue(q)} title="Weggooien"><Icon name="close" size={14} /></button>
            </div>
          {/each}
        </div>
      {:else}
        <div class="tiny muted">Nog niets opgeslagen voor deze wereld.</div>
      {/if}
    </section>

    <!-- Instellingen -->
    <section>
      <div class="head"><span class="h">Instellingen</span></div>
      <div class="grid">
        <label>werkbreedte (m)<input type="number" min="0.5" max="12" step="0.25" bind:value={s.width} /></label>
        {#if effKind === 'ab'}
          <label>kopakker (m)<input type="number" min="3" max="30" step="1" bind:value={s.abHeadland} /></label>
        {:else}
          <label>rondjes kopakker<input type="number" min="0" max="8" step="1" bind:value={s.rounds} /></label>
        {/if}
        <label>snelheid (km/h)<input type="number" min="1.4" max="5.6" step="0.1" bind:value={s.speed} /></label>
        {#if effKind === 'ab'}
          <label>overslaan<input type="number" min="0" max="6" step="1" bind:value={s.abSkip} /></label>
        {/if}
      </div>

      {#if !advanced && single && starts.length}
        <label class="block">startpunt
          <select bind:value={startId}>
            <option value="">dichtst bij de robot</option>
            {#each starts as p}<option value={p.id}>{startLabel(p)}</option>{/each}
          </select>
        </label>
      {/if}

      {#if advanced && effKind !== 'ab'}
        <div class="sub">Banen en kopakker</div>
        <div class="grid">
          <label>overlap (m)<input type="number" min="0" max="1" step="0.05" bind:value={s.overlap} /></label>
          <label title="Overlapt de laatste baan meer dan dit met zijn buur, dan vervalt hij. 100 = altijd rijden">max overlap laatste baan (%)
            <input type="number" min="0" max="100" step="5" bind:value={s.maxOverlap} /></label>
          <label>volgorde banen
            <select bind:value={s.pattern}>
              <option value="auto">kortste (auto)</option><option value="heen_en_weer">heen en weer</option>
              {#each [1, 2, 3, 4, 5, 6] as k}<option value="overslaan_{k}">{k} overslaan</option>{/each}
            </select>
          </label>
          <label>baanrichting (°)
            <span class="row"><input type="checkbox" bind:checked={s.autoHeading} title="Beste richting uitrekenen" />
              <input type="number" min="0" max="179" step="1" bind:value={s.heading} disabled={s.autoHeading} /></span>
          </label>
          <label>rondjes
            <select bind:value={s.direction}>
              <option value="rechts">met de klok mee</option><option value="links">tegen de klok in</option>
            </select>
          </label>
          <label>begin met
            <select bind:value={s.order} disabled={s.rounds === 0}>
              <option value="eerst">buitenbaan (rondjes eerst)</option>
              <option value="laatst">rechte banen (rondjes laatst)</option>
            </select>
          </label>
          <label>bochtsnelheid (km/h)<input type="number" min="1.4" max="5.6" step="0.1" bind:value={s.turnSpeed} /></label>
          <label title="Rondje 2 en verder recht: alleen het buitenste volgt de rand; bochtjes kleiner dan dit worden rechtgetrokken">rondje 2+ recht tot (m)
            <span class="row"><input type="checkbox" bind:checked={s.straight} disabled={s.rounds < 2} />
              <input type="number" min="0.2" max="3" step="0.1" bind:value={s.straightM} disabled={!s.straight || s.rounds < 2} /></span>
          </label>
        </div>

        <div class="sub">Uit de buurt blijven (m)</div>
        <div class="grid four">
          <label>sloot<input type="number" min="0" max="5" step="0.1" bind:value={s.water} /></label>
          <label>weg<input type="number" min="0" max="5" step="0.1" bind:value={s.road} /></label>
          <label>hek<input type="number" min="0" max="5" step="0.1" bind:value={s.fence} /></label>
          <label>gebouw<input type="number" min="0" max="5" step="0.1" bind:value={s.building} /></label>
        </div>
        <div class="grid" style="margin-top:6px">
          <label>datum (nesten in hun periode)<input type="text" bind:value={date} placeholder="jjjj-mm-dd" /></label>
          <label>dijken
            <select bind:value={s.dikeMode}>
              <option value="">zoals ingesteld</option><option value="ontwijken">ontwijken</option>
              <option value="negeren">niet meenemen</option><option value="doorgang">alleen bij doorgang</option>
            </select>
          </label>
        </div>

        {#if single}
          <div class="sub">Startpunt <span class="muted">· waar de robot op dit veld begint</span></div>
          <div class="row">
            <select class="grow" bind:value={startId} disabled={!starts.length}>
              <option value="">{starts.length ? 'dichtst bij de robot' : 'geen: begint bij de startplek van de wereld'}</option>
              {#each starts as p}<option value={p.id}>{startLabel(p)}</option>{/each}
            </select>
            <button class:active={placingStart} onclick={() => { selected = lastSelected = queue[0]; placingStart = !placingStart; }}
              title="Dubbelklik daarna op de kaart (3D of plattegrond)">{placingStart ? 'Klaar' : 'Zetten'}</button>
            <button onclick={() => startOp('/api/startpoints/add', { field: queue[0] })} title="Startpunt waar de robot nu staat">Bij robot</button>
          </div>
          {#if placingStart}<div class="tiny hintc">Dubbelklik op de kaart om een startpunt te zetten (mag net buiten de grens, bv. bij de dam).</div>{/if}
          {#if starts.length}
            <div class="starts">
              {#each starts as p}
                <span class="start" class:on={p.id === (startId || plan?.start_point?.id)}>
                  <button class="pick" onclick={() => (startId = p.id)}>{startLabel(p)}</button>
                  <button class="del" onclick={() => startOp('/api/startpoints/remove', { id: p.id })} title="Weghalen">×</button>
                </span>
              {/each}
            </div>
          {/if}
        {:else if queue.length > 1}
          <div class="sub">Tussen de velden</div>
          <div class="grid">
            <label>transport (km/h)<input type="number" min="1.4" max="5.6" step="0.1" bind:value={s.transport} /></label>
            <label title="Breedte van de machine onderweg: rijpaden en doorgangen moeten breder zijn">transportbreedte (m)
              <input type="number" min="0.5" max="12" step="0.1" bind:value={s.transportWidth} /></label>
          </div>
          <label class="block">route begint
            <select bind:value={startFrom}>
              <option value="startplek">op de startplek van de wereld</option>
              <option value="robot">waar de sim-robot nu staat</option>
              {#each fleet as r}<option value="vloot:{r.id}">waar {r.name} nu staat (echte robot)</option>{/each}
            </select>
          </label>
          <label class="chk"><input type="checkbox" bind:checked={returnHome} />na afloop terug naar het beginpunt</label>
        {/if}

        <label class="block">rijden in de sim met
          <select bind:value={driveMode}>
            <option value="missie">de missievolger van de robot</option><option value="waypoints">waypoints (oud, punt naar punt)</option>
          </select>
        </label>
      {/if}
    </section>
  </div>

  <div class="col">
    <!-- Eén actiebalk -->
    <section class="actions">
      <button onclick={doPlan} disabled={!queue.length || busy} title="Route uitrekenen en op de kaart tekenen">Plan</button>
      <button class="primary grow" onclick={() => drive(false)} disabled={!queue.length || busy}>
        <Icon name="play" size={15} />{target === 'boerderij' ? `Rijd ${queue.length} velden` : 'Rijden in de sim'}</button>
      <button class="danger" onclick={stop} title="Sim-robot stoppen"><Icon name="stop" size={15} />Stop</button>
    </section>
    {#if msg}<div class="msg small" class:badc={msgBad}>{msg}</div>{/if}
    {#if stale}<div class="tiny warnc">Instellingen of wachtrij veranderd: rijden plant eerst opnieuw.</div>{/if}

    {#if plan && planKind !== 'ab'}
      <section>
        <div class="head"><span class="h">Resultaat</span></div>
        <div class="kpis num">
          <div><b>{fmtMin(stats.time_min)}</b><span>{(stats.length_m / 1000).toFixed(1)} km</span></div>
          <div><b>{(stats.worked_m2 / 1e4).toFixed(2)} ha</b><span>bewerkt</span></div>
          <div><b>{stats.efficiency_pct}%</b><span>werkend</span></div>
          <div class:bad={!safetyOk} class:good={safetyOk}><b>{safetyOk ? 'Veilig' : 'Let op'}</b><span>controle</span></div>
        </div>
        {#if !safetyOk}
          {#each plan.safety.violations.slice(0, advanced ? 6 : 3) as v}
            <div class="small badc">{v.segment_label}: {v.label}{v.name && v.name !== v.label ? ` ${v.name}` : ''}</div>
          {/each}
          <div class="tiny muted">Rode stukken op de kaart; de sim rijdt dit niet.
            {#if advanced}<button class="link" onclick={() => drive(true)}>Toch rijden (bewaking testen)</button>{/if}</div>
        {/if}

        {#if planKind === 'boerderij'}
          <div class="sub">Volgorde {optimize ? '(gekozen door de planner)' : ''}</div>
          <ol class="order small">
            {#each plan.fields as f}<li>{f.name} <span class="muted">{f.rounds} rondjes · {fmtMin(f.time_min)} · {f.coverage_allowed_pct}%</span></li>{/each}
          </ol>
          {#if plan.unreachable.length}
            {#each plan.unreachable as u}<div class="small badc">Niet in de missie: {u.name}: {u.reason}</div>{/each}
          {/if}
        {/if}

        {#if advanced}
          <div class="stats small">
            {#if planKind === 'veld'}
              <span><b>{stats.coverage_allowed_pct}%</b> bewerkt van wat mag · {(stats.field_m2 / 1e4).toFixed(2)} ha perceel</span>
              <span>{plan.rounds} rondjes · banen {plan.swath_heading_deg ?? '-'}° · {plan.cells.length} {plan.cells.length === 1 ? 'vak' : 'vakken'}
                · draaicirkel {plan.turn_radius_m} m · overlap {stats.overlap_pct}%</span>
              {#if plan.start_point}<span>begint bij <b>{startLabel(plan.start_point)}</b>{plan.start_point.auto ? ' (dichtst bij de robot)' : ''}</span>{/if}
            {:else}
              <span>{stats.passages} doorgangen · {(stats.transport_m / 1000).toFixed(2)} km transport</span>
            {/if}
          </div>
          {#if planKind === 'veld' && plan.edge_summary?.length}
            <div class="sub">Randen</div>
            <div class="edges">
              {#each plan.edge_summary as e}
                <span class="edge"><i class="k-{e.kind}"></i>{kindLabel[e.kind] ?? e.kind} {Math.round(e.length_m)} m
                  <span class="muted">· {e.setback_m.toFixed(1)} m</span></span>
              {/each}
            </div>
          {/if}
          {#if lanes.length}
            <div class="sub">Rijpaden</div>
            {#each lanes as l}
              <div class="small" class:badc={!l.usable || l.reason}>{l.name || l.id}{#if l.connects.length}
                <span class="muted"> · {l.connects.join(', ')}</span>{/if}{#if l.reason}: {l.reason}{/if}</div>
            {/each}
          {/if}
        {/if}
        {#if plan.warnings?.length}
          <div class="warnc small">{#each plan.warnings.slice(0, advanced ? 6 : 2) as w}<div>{w}</div>{/each}
            {#if plan.warnings.length > (advanced ? 6 : 2)}<div class="muted">… en nog {plan.warnings.length - (advanced ? 6 : 2)}</div>{/if}</div>
        {/if}
      </section>

      <section>
        <div class="head"><span class="h">Naar de echte robot</span></div>
        <div class="row">
          <input class="grow" type="text" bind:value={robotUrl} title="Adres van de robot" />
          <button onclick={push} disabled={busy || !safetyOk} title="Zet kaart en missie op de robot; start niets">Stuur</button>
        </div>
        {#if advanced}
          <div class="row files">
            <a class="btn" href="{files}/veldmissie.json" download>veldmissie.json</a>
            <a class="btn" href="/api/safety/kaart.json" download>kaart.json</a>
            <a class="btn" href="{files}/waypoints.json" download>waypoints.json</a>
          </div>
          <div class="tiny muted">veldmissie.json = de route met snelheid en werktuig omhoog/omlaag; kaart.json = sloten en
            verboden gebieden (de robot stopt daar altijd). Laden of starten op de robot in het tabblad Veldmissie.</div>
        {:else}
          <div class="tiny muted">Zet missie en veiligheidskaart op de robot; starten doe je daar.</div>
        {/if}
      </section>
    {:else if !plan && wide}
      <div class="placeholder small muted">Zet velden in de wachtrij en druk op <b>Plan</b>: de route verschijnt op de kaart
        en het resultaat hier.</div>
    {/if}
  </div>
</div>

<style>
  .top { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
  .slider { position: relative; display: grid; grid-template-columns: 1fr 1fr; flex: 1; max-width: 300px; padding: 3px;
    border-radius: 18px; background: var(--well); border: 1px solid var(--line); }
  .slider .knob { position: absolute; top: 3px; bottom: 3px; left: 3px; width: calc(50% - 3px); border-radius: 15px;
    background: var(--accent); transition: transform 0.18s ease; }
  .slider .knob.right { transform: translateX(100%); }
  .slider button { position: relative; z-index: 1; background: transparent; border: 0; border-radius: 15px; min-height: 26px;
    font-family: var(--display); font-size: 13.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
  .slider button.on { color: var(--accent-ink); font-weight: 700; }
  .slider button:hover { background: transparent; color: var(--ink); }
  .slider button.on:hover { color: var(--accent-ink); }
  .kind { flex: 1; min-width: 0; font-size: 12.5px; }

  .layout { display: flex; flex-direction: column; gap: 0; }
  .layout.wide { display: grid; grid-template-columns: minmax(340px, 1.1fr) minmax(300px, 1fr); gap: 22px; align-items: start; }
  .layout.wide > .col:last-child { position: sticky; top: 0; }
  section { margin-bottom: 14px; }
  .head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
  .h { font-family: var(--display); font-size: 13px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); }
  .grow { flex: 1; min-width: 0; }
  .row { display: flex; gap: 6px; align-items: center; }
  .ghost { background: transparent; border-color: transparent; display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 6px; min-height: 26px; color: var(--muted); }
  .ghost:hover { color: var(--ink); }
  .ghost.del:hover { color: var(--red); }
  .small { font-size: 12.5px; }
  .tiny { font-size: 11.5px; }

  .queue { list-style: none; margin: 0 0 6px; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .queue li { display: flex; align-items: center; gap: 6px; padding: 3px 4px 3px 6px; border-radius: 6px;
    background: rgba(255, 255, 255, 0.05); border: 1px solid var(--line); cursor: grab; }
  .queue li.over { border-color: var(--accent); }
  .queue li.dim { opacity: 0.45; }
  .queue .nr { width: 22px; height: 22px; flex: none; border-radius: 50%; display: grid; place-items: center;
    background: var(--accent); color: var(--accent-ink); font-family: var(--display); font-weight: 700; font-size: 13px; }
  .queue .name { flex: 1; min-width: 0; display: flex; gap: 8px; align-items: baseline; text-align: left; background: transparent;
    border: 0; padding: 2px; min-height: 26px; }
  .queue .name b { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .queue .name .muted { font-size: 12px; white-space: nowrap; }
  .queue .ops { display: flex; }
  .queue .ops .ghost { padding: 2px 4px; }
  .empty { padding: 10px; border: 1px dashed var(--line-strong); border-radius: 6px; color: var(--muted); margin-bottom: 8px; }

  .fieldlist { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; max-height: 150px; overflow: auto; }
  .fieldlist.cols { max-height: 220px; }
  .fieldbtn { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px 2px 3px; border-radius: 14px; min-height: 26px;
    font-size: 12.5px; }
  .fieldbtn .badge { width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; background: var(--track);
    color: var(--muted); font-size: 11.5px; font-weight: 700; }
  .fieldbtn.in { border-color: var(--accent); background: var(--accent-soft); }
  .fieldbtn.in .badge { background: var(--accent); color: var(--accent-ink); }
  .fieldbtn .muted { font-size: 11.5px; }
  .addall { margin-top: 4px; }

  .saved { display: flex; flex-direction: column; gap: 3px; max-height: 170px; overflow: auto; }
  .sq { display: flex; align-items: center; border: 1px solid var(--line); border-radius: 6px; }
  .sq.on { border-color: var(--blue); }
  .sq .load { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; text-align: left; border: 0;
    background: transparent; padding: 4px 8px; gap: 1px; }
  .sq .load b { font-weight: 600; }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  .grid.four { grid-template-columns: repeat(4, 1fr); }
  .layout.wide .grid:not(.four) { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  label.block { margin-top: 8px; }
  label.chk { flex-direction: row; align-items: center; gap: 6px; color: var(--ink); font-size: 12.5px; margin-top: 4px; }
  input[type='number'], input[type='text'], select { width: 100%; }
  input[type='checkbox'] { width: auto; }
  .sub { font-weight: 600; font-size: 12px; margin: 12px 0 4px; }
  .sub .muted { font-weight: 400; }

  .actions { display: flex; gap: 6px; position: sticky; top: -12px; z-index: 2; padding: 8px 0; margin: 0 0 4px;
    background: var(--panel-solid); }
  .actions button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; }
  .msg { margin: 0 0 6px; }

  .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
  .kpis div { display: flex; flex-direction: column; padding: 6px 8px; border-radius: 6px; background: var(--well); }
  .kpis b { font-family: var(--display); font-size: 17px; font-weight: 600; line-height: 1.1; white-space: nowrap; }
  .kpis span { font-size: 11px; color: var(--muted); }
  .kpis .good b { color: var(--green); }
  .kpis .bad { background: rgba(255, 90, 77, 0.18); }
  .kpis .bad b { color: #ff9a90; }
  .stats { display: flex; flex-direction: column; gap: 2px; margin-top: 8px; }
  .order { margin: 0; padding-left: 20px; }
  .edges { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12px; }
  .edge i { display: inline-block; width: 10px; height: 4px; margin-right: 5px; vertical-align: middle; border-radius: 2px; }
  .k-water { background: #3389ff; } .k-weg { background: #222; outline: 1px solid #666; } .k-verhard { background: #999; }
  .k-hek { background: #9e6130; } .k-gebouw { background: #d93333; } .k-boom { background: #339940; }
  .k-perceel { background: #ffd11a; } .k-open { background: #8cd959; } .k-dijk { background: #c9a36a; }
  .k-verboden { background: #ff4a3d; } .k-nest { background: #ffb23e; } .k-gat { background: #ff6a5d; } .k-marker { background: #fff; }

  .starts { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
  .start { display: inline-flex; border: 1px solid var(--line-strong); border-radius: 4px; overflow: hidden; }
  .start.on { border-color: var(--blue); }
  .start.on .pick { color: var(--blue); }
  .start button { border: 0; border-radius: 0; min-height: 24px; padding: 2px 8px; font-size: 12px; background: transparent; }
  .start .del { border-left: 1px solid var(--line); color: var(--muted); padding: 2px 7px; }

  .files { margin: 6px 0 4px; flex-wrap: wrap; }
  .btn { display: inline-block; padding: 4px 10px; border: 1px solid var(--line-strong); border-radius: 4px; color: inherit;
    text-decoration: none; font-size: 12.5px; }
  .link { background: none; border: 0; padding: 0; min-height: 0; color: var(--amber); text-decoration: underline; font-size: 11.5px; }
  .placeholder { padding: 18px; border: 1px dashed var(--line-strong); border-radius: 8px; text-align: center; }
  .okc { color: var(--green); } .badc { color: #ff9a90; } .warnc { color: #ffb23e; } .hintc { margin-top: 5px; color: var(--blue); }
</style>
