<script>
  // Vloot: de echte robots die de sim live volgt. Per robot zijn stand, een stopknop en wat je ernaar kunt sturen.
  // De sim leest alleen mee; rijden doet een robot pas na een commando dat je hier of op de robot zelf geeft.
  import { del, errorText, get, post, put } from '../lib/api.js';
  import Icon from './Icon.svelte';

  let { fleet = null, simRobot = null, followId = null, onFollow, onOpenUi, onFocus, toast } = $props();

  const robots = $derived(fleet?.robots ?? []);
  const online = $derived(robots.filter((r) => r.online).length);
  const kinds = $derived(fleet?.kinds ?? []);
  const modeLabel = { idle: 'staat stil', manual: 'rijdt handmatig', waypoints: 'waypoints', ab: 'AB-missie', mission: 'veldmissie' };

  let open = $state(null);                 // id van de robot waarvan de details open staan
  let missions = $state.raw({});           // {id: [missies op die robot]}
  let editing = $state(null);              // {id, name, url, color}
  let adding = $state(false);
  let form = $state({ name: '', url: 'http://192.168.4.1:8000', kind: 'openagbot' });
  let probe = $state(null);                // uitkomst van 'Test'
  let busy = $state(false);

  async function run(what, fn) {
    busy = true;
    try { return await fn(); } catch (e) { toast?.('fout', `${what}: ${errorText(e)}`); return null; } finally { busy = false; }
  }

  function tone(r) {
    const t = r.telemetry;
    if (!r.enabled) return 'off';
    if (!r.online) return 'bad';
    if (t?.fault) return 'bad';
    if (t?.geofence || t?.fix !== 4 || !r.pose?.in_world) return 'warn';
    return t?.mode === 'idle' ? 'idle' : 'go';
  }

  function statusText(r) {
    const t = r.telemetry;
    if (!r.enabled) return 'uitgeschakeld';
    if (!r.online) return r.seen ? `offline · ${fmtAge(r.age_s)} geleden` : 'verbinden...';
    if (t.fault) return `fout: ${t.fault}`;
    if (t.geofence) return `geofence: ${t.geofence}`;
    return t.message || t.state || modeLabel[t.mode] || t.mode;
  }

  function fmtAge(s) {
    if (s == null) return '-';
    return s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} u`;
  }

  async function test() {
    probe = null;
    const r = await run('Test', () => post('/api/fleet/probe', { url: form.url, kind: form.kind }));
    if (r) probe = r;
  }

  async function add() {
    const r = await run('Toevoegen', () => post('/api/fleet', $state.snapshot(form)));
    if (!r) return;
    adding = false; probe = null;
    form = { name: '', url: 'http://192.168.4.1:8000', kind: 'openagbot' };
    onFollow?.(r.robot.id);
  }

  async function toggleOpen(r) {
    open = open === r.id ? null : r.id;
    editing = null;
    if (open && r.online && r.capabilities.includes('missions')) loadMissions(r.id);
  }

  async function loadMissions(id) {
    try { missions = { ...missions, [id]: (await get(`/api/fleet/${id}/missions`)).missions }; } catch { /* robot weg */ }
  }

  async function startMission(r, m, resume) {
    const ok = confirm(`${r.name} is een ECHTE robot en rijdt direct weg.\n\nMissie '${m.name}' ${resume ? 'hervatten' : 'starten'}?\n` +
      'Controleer dat er niemand bij de robot staat.');
    if (!ok) return;
    const d = await run('Missie starten', () => post(`/api/fleet/${r.id}/mission/start`, { name: m.name, resume, confirm: true }));
    if (d) { toast?.('vloot', `${r.name}: missie '${m.name}' gestart`); onFollow?.(r.id); }
  }

  async function sendMap(r) {
    const d = await run('Kaart sturen', () => post(`/api/fleet/${r.id}/map`));
    if (d) toast?.('vloot', d.sent.old_software ? `${r.name}: oude software, kent geen veiligheidskaart`
      : `${r.name}: veiligheidskaart van deze wereld staat op de robot`);
  }

  function useAsTarget(r) {
    try { localStorage.setItem('fieldsim.robotUrl', r.url); } catch { /* geen opslag */ }
    toast?.('vloot', `${r.name} is nu het doel van 'naar de robot sturen' in Missie en Punten`);
  }

  async function saveEdit() {
    const e = $state.snapshot(editing);
    if (await run('Bewaren', () => put(`/api/fleet/${e.id}`, { name: e.name, url: e.url, color: e.color }))) editing = null;
  }

  async function remove(r) {
    if (!confirm(`${r.name} uit de vloot halen? De robot zelf merkt daar niets van.`)) return;
    if (followId === r.id) onFollow?.(null);
    await run('Verwijderen', () => del(`/api/fleet/${r.id}`));
  }

  async function stopAll() {
    const d = await run('Stop alle', () => post('/api/fleet/stop'));
    if (!d) return;
    const failed = Object.entries(d.robots).filter(([, v]) => v !== 'stopped').map(([k]) => k);
    toast?.(failed.length ? 'fout' : 'vloot', failed.length ? `Stop NIET aangekomen bij: ${failed.join(', ')}` : 'Alle robots gestopt');
  }
</script>

<div class="row top">
  <span class="small muted">{robots.length} {robots.length === 1 ? 'robot' : 'robots'} · <b class:ok={online > 0}>{online} online</b></span>
  <span class="grow"></span>
  <button class="danger" disabled={!online} onclick={stopAll} title="Noodstop naar alle robots van de vloot">
    <Icon name="stop" size={15} /> Stop alle</button>
</div>

<!-- De sim-robot hoort er ook bij: zo kies je waar de camera en de rijmeters op staan -->
<div class="card" class:follow={followId === null}>
  <div class="row head">
    <i class="dot" style="background:var(--accent)"></i>
    <div class="grow"><b>Sim-robot</b><span class="st idle">gesimuleerd · {Math.abs(simRobot?.speed_kmh ?? 0).toFixed(1)} km/h</span></div>
    <button class:active={followId === null} onclick={() => onFollow?.(null)} title="Camera en meters op de sim-robot">
      <Icon name="follow" size={15} /></button>
  </div>
</div>

{#each robots as r (r.id)}
  {@const t = r.telemetry}
  <div class="card" class:follow={followId === r.id} class:offline={!r.online}>
    <div class="row head">
      <i class="dot" style="background:{r.online ? r.color : 'var(--faint)'}"></i>
      <button class="name grow" onclick={() => toggleOpen(r)} title="Details">
        <b>{r.name}</b><span class="st {tone(r)}">{statusText(r)}</span>
      </button>
      <button class:active={followId === r.id} disabled={!r.pose?.in_world} onclick={() => onFollow?.(followId === r.id ? null : r.id)}
        title="Camera en meters op deze robot"><Icon name="follow" size={15} /></button>
      {#if r.capabilities.includes('ui')}
        <button onclick={() => onOpenUi?.(r)} title="De eigen webinterface van deze robot"><Icon name="robot" size={15} /></button>
      {/if}
      <button class="danger" disabled={!r.online} onclick={() => run('Stop', () => post(`/api/fleet/${r.id}/stop`))}
        title="Noodstop naar deze robot"><Icon name="stop" size={15} /></button>
    </div>

    {#if r.online && t}
      <div class="grid num">
        <span><em>snelheid</em>{t.speed_kmh.toFixed(1)} km/h</span>
        <span class:warn={t.fix !== 4}><em>gnss</em>{t.fix_label || 'geen fix'}{t.sats != null ? ` · ${t.sats} sat` : ''}</span>
        <span class:warn={t.battery_soc != null && t.battery_soc < 0.25}><em>accu</em>{t.battery_v != null ? `${t.battery_v.toFixed(1)} V` : '-'}{t.battery_soc != null
          ? ` · ${Math.round(t.battery_soc * 100)} %` : ''}</span>
        <span class:warn={r.latency_ms > 400}><em>verbinding</em>{r.latency_ms?.toFixed(0) ?? '-'} ms</span>
      </div>
      {#if t.mission?.percent != null}
        <div class="prog"><div style="width:{Math.min(100, t.mission.percent)}%;background:{r.color}"></div></div>
        <div class="tiny muted num">{t.mission.name} · {t.mission.percent.toFixed(0)} %{t.mission.segment_label ? ` · ${t.mission.segment_label}` : ''}{t.mission.remaining_min != null
          ? ` · nog ${t.mission.remaining_min.toFixed(0)} min` : ''}</div>
      {/if}
      {#if !t.lat || !t.fix}
        <div class="note">Geen GPS-positie: de robot staat nog niet op de kaart.</div>
      {:else if r.pose && !r.pose.in_world}
        <div class="note">Staat buiten deze wereld ({(Math.hypot(r.pose.x, r.pose.y) / 1000).toFixed(1)} km van het midden).
          Open de wereld van het veld waar hij rijdt.</div>
      {/if}
    {:else if r.enabled && r.error}
      <div class="tiny muted err">{r.error}</div>
    {/if}

    {#if open === r.id}
      <div class="more">
        {#if editing?.id === r.id}
          <label>naam<input type="text" bind:value={editing.name} /></label>
          <label>adres<input type="text" bind:value={editing.url} /></label>
          <div class="row"><label class="row">kleur <input type="color" bind:value={editing.color} /></label><span class="grow"></span>
            <button onclick={() => (editing = null)}>Annuleer</button>
            <button class="primary" disabled={busy} onclick={saveEdit}>Bewaar</button></div>
        {:else}
          {#if r.capabilities.includes('missions') && r.online}
            <div class="sec">Missies op de robot</div>
            {#each missions[r.id] ?? [] as m}
              <div class="row mis">
                <span class="grow">{m.name}{#if m.error}<span class="muted tiny"> · {m.error}</span>{/if}</span>
                {#if t?.mode === 'mission' && t.mission?.name === m.name}
                  <button onclick={() => run('Missie stoppen', () => post(`/api/fleet/${r.id}/mission/stop`))}>Stop missie</button>
                {:else if !m.error}
                  {#if m.progress}<button disabled={busy} onclick={() => startMission(r, m, true)}>Hervat</button>{/if}
                  <button disabled={busy} onclick={() => startMission(r, m, false)}>Start</button>
                {/if}
              </div>
            {:else}
              <div class="tiny muted">Geen missies. Plan er een in Missie en stuur hem naar de robot.</div>
            {/each}
          {/if}
          <div class="row wrap">
            {#if r.pose?.in_world}<button onclick={() => onFocus?.(r)}>Toon op kaart</button>{/if}
            {#if r.capabilities.includes('map')}
              <button disabled={!r.online || busy} onclick={() => sendMap(r)} title="Sloten en verboden gebieden van deze wereld naar de robot">Stuur veiligheidskaart</button>
              <button onclick={() => useAsTarget(r)} title="Missies en waypoints gaan voortaan naar deze robot">Doel voor missies</button>
            {/if}
            <button onclick={() => post(`/api/fleet/${r.id}/trail/clear`).catch(() => {})}>Wis spoor</button>
            <button onclick={() => (editing = { id: r.id, name: r.name, url: r.url, color: r.color })}>Bewerk</button>
            <button onclick={() => run('Aan/uit', () => put(`/api/fleet/${r.id}`, { enabled: !r.enabled }))}>{r.enabled ? 'Verbreek' : 'Verbind'}</button>
            <button class="danger" onclick={() => remove(r)}>Verwijder</button>
          </div>
          <div class="tiny muted">{r.url} · {r.label}{r.info?.api ? ` · API ${r.info.api}` : ''}{r.info?.simulated ? ' · simulatie' : ''}</div>
        {/if}
      </div>
    {/if}
  </div>
{/each}

{#if adding}
  <div class="card add">
    <div class="sec">Robot toevoegen</div>
    <label>naam<input type="text" bind:value={form.name} placeholder="Bruut 1" /></label>
    <label>adres van zijn webinterface<input type="text" bind:value={form.url} oninput={() => (probe = null)} /></label>
    {#if kinds.length > 1}
      <label>soort<select bind:value={form.kind}>{#each kinds as k}<option value={k.kind}>{k.label}</option>{/each}</select></label>
    {/if}
    {#if probe}
      <div class="note ok">Bereikbaar: {probe.info?.name ?? 'robot'} · {probe.fix_label || 'geen fix'}{#if probe.pose && !probe.pose.in_world}
        · staat buiten deze wereld{:else if !probe.has_position} · nog geen positie{/if}</div>
    {/if}
    <div class="row">
      <button disabled={busy} onclick={test}>Test verbinding</button><span class="grow"></span>
      <button onclick={() => { adding = false; probe = null; }}>Annuleer</button>
      <button class="primary" disabled={busy || !form.url} onclick={add}>Voeg toe</button>
    </div>
  </div>
{:else}
  <button class="addbtn" onclick={() => (adding = true)}>+ Robot toevoegen</button>
  {#if !robots.length}
    <div class="tiny muted hint">Voeg het adres van een robot toe (bv. http://192.168.4.1:8000) om hem live in deze wereld te
      volgen. De sim leest alleen mee; de webinterface van de robot blijft gewoon werken.</div>
  {/if}
{/if}

<style>
  .top { margin-bottom: 8px; }
  .top b.ok { color: var(--green); }
  .top button { display: inline-flex; align-items: center; gap: 6px; }
  .card { border: 1px solid var(--line); border-left: 3px solid transparent; border-radius: 6px; padding: 7px 8px 8px;
    margin-bottom: 7px; background: rgba(255, 255, 255, 0.03); }
  .card.follow { border-left-color: var(--accent); background: var(--accent-soft); }
  .card.offline { opacity: 0.8; }
  .head { gap: 6px; }
  .head button { padding: 4px 7px; display: inline-flex; align-items: center; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .head .name { display: flex; flex-direction: column; align-items: flex-start; text-align: left; background: transparent;
    border-color: transparent; padding: 0 4px; min-width: 0; }
  .head > div.grow { display: flex; flex-direction: column; padding: 0 4px; }
  .name b, .head > div b { font-family: var(--display); font-size: 16px; letter-spacing: 0.05em; text-transform: uppercase; }
  .st { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 190px; }
  .st.go { color: var(--green); }
  .st.warn { color: var(--amber); }
  .st.bad { color: var(--red); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 12px; margin-top: 7px; font-size: 12.5px; }
  .grid em { font-style: normal; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
    margin-right: 6px; }
  .grid .warn { color: var(--amber); }
  .prog { height: 5px; background: var(--track); border-radius: 3px; margin: 7px 0 3px; overflow: hidden; }
  .prog div { height: 100%; transition: width 0.4s; }
  .note { margin-top: 6px; padding: 5px 8px; border-radius: 4px; font-size: 12px; background: rgba(255, 178, 62, 0.14);
    color: #ffd596; }
  .note.ok { background: rgba(125, 220, 60, 0.13); color: #c9f0a6; }
  .err { margin-top: 5px; user-select: text; }
  .more { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; }
  .wrap { flex-wrap: wrap; gap: 5px; }
  .mis { font-size: 13px; }
  label { display: flex; flex-direction: column; font-size: 12px; gap: 2px; }
  label.row { flex-direction: row; }
  .add { display: flex; flex-direction: column; gap: 7px; border-left-color: var(--blue); }
  input[type="color"] { width: 34px; height: 26px; padding: 0; border: 1px solid var(--line); background: transparent; }
  .addbtn { width: 100%; border-style: dashed; color: var(--muted); }
  .hint { margin-top: 8px; line-height: 1.4; }
</style>
