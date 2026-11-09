<script>
  // OpenAgbot FieldSim: de 3D-wereld met een hoofdmenu, een pauzemenu en de HUD eroverheen.
  import { onDestroy, onMount } from 'svelte';
  import ConsolePanel from './components/ConsolePanel.svelte';
  import DriveHud from './components/DriveHud.svelte';
  import FaultPanel from './components/FaultPanel.svelte';
  import Icon from './components/Icon.svelte';
  import ImplementPanel from './components/ImplementPanel.svelte';
  import MainMenu from './components/MainMenu.svelte';
  import MiniMap from './components/MiniMap.svelte';
  import MissionPanel from './components/MissionPanel.svelte';
  import PauseMenu from './components/PauseMenu.svelte';
  import PointsPanel from './components/PointsPanel.svelte';
  import ReplayPanel from './components/ReplayPanel.svelte';
  import WorldPanel from './components/WorldPanel.svelte';
  import { agbot, connectState, get, getTerrain, post } from './lib/api.js';
  import { CAMERA_MODES, QUALITY, World3D } from './lib/scene/world3d.js';
  import { applySettings, loadSettings, saveSettings } from './lib/settings.js';

  let viewport;
  let w3d = $state.raw(null);
  let scene = $state.raw(null);
  let worlds = $state([]);
  let currentWorld = $state('');
  let st = $state.raw(null);             // live toestand van de sim
  let ab = $state.raw(null);             // status van OpenAgbot
  let connected = $state(false);
  let screen = $state('menu');           // menu | sim
  let loading = $state(true);
  let paused = $state(false);            // pauzemenu open
  let settings = $state(loadSettings());
  let tab = $state('');
  let showRobotUi = $state(false);
  let showView = $state(false);          // uitklapper 'weergave' (lagen)
  let placing = $state(false);           // punten zetten met dubbelklik
  let pointsVersion = $state(0);
  let camMode = $state('rond');
  let mission = $state.raw(null);
  let selectedField = $state(null);
  let order = $state.raw([]);
  let trailXY = $state.raw([]);
  let toasts = $state([]);
  let lastTrailTotal = 0;
  let lastEventT = null;
  let planVersion = null;
  let wasPaused = false;
  let implements_ = $state.raw([]);      // catalogus van aanbouwdelen
  let implementId = $state(null);
  let keys = new Set();
  let stopState;

  const fixLabel = { 4: 'RTK fixed', 5: 'RTK float', 2: 'DGPS', 1: 'GPS', 0: 'geen fix' };
  const surfaceLabel = { yard: 'erf', grass: 'gras', field: 'veld', track: 'kavelpad', paved: 'verharding', road: 'weg',
    verge: 'berm', water: 'water' };
  const camLabel = { volg: ['Volg', 'follow'], vrij: ['Vrij', 'orbit'], boven: ['Boven', 'top'] };
  const tabs = [['missie', 'Missie', 'mission'], ['punten', 'Punten', 'points'], ['werktuig', 'Werktuig', 'implement'],
    ['replay', 'Replay', 'replay'], ['storingen', 'Storingen', 'fault'], ['console', 'Console', 'console'],
    ['wereld', 'Wereld', 'world']];
  const viewToggles = [['boundaries', 'Perceelgrenzen'], ['labels', 'Veldnamen'], ['nogo', 'Sloten markeren']];

  // Terrein in beeld op 1 m (zelfde raster als de fysica) als de wereld dat toelaat, anders grover
  function terrainCell(s) {
    const t = s.terrain, want = Math.max(t.cell, QUALITY[settings.quality]?.terrainCell ?? 1);
    const points = (t.nx * t.ny * t.cell * t.cell) / (want * want);
    return points > 1.8e6 ? Math.max(2, want * 2) : want;
  }

  async function loadWorld() {
    loading = true;
    try {
      const s = await get('/api/world');
      const t = await getTerrain(terrainCell(s));
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));   // eerst 'laden' tonen
      scene = s;
      w3d.load(s, t.header, t.heights);
      applySettings(w3d, settings);
      mission = null; order = []; trailXY = []; lastTrailTotal = 0;
      const cat = await get('/api/implements');
      implements_ = cat.implements; implementId = cat.current;
      w3d.setImplement(implements_.find((c) => c.id === implementId) ?? null);
    } catch (e) {
      toast('fout', `Wereld laden mislukt: ${e.message || e}`);
    }
    loading = false;
  }

  async function onImplement(spec) {
    w3d.setImplement(spec);
    if (spec?.kind === 'spot') {
      const p = await get('/api/points');
      w3d.setPoints(p.points);
    }
  }

  async function pollTargets() {
    if (!st?.implement?.targets || !w3d) return;
    try { w3d.setTargetStatus((await get('/api/implement/targets')).targets); } catch { /* geen werktuig */ }
  }

  async function switchWorld(name) {
    if (name === currentWorld || loading) return;
    loading = true;
    try {
      await post('/api/world/load', { name });
      currentWorld = name;
      lastEventT = null;
      await loadWorld();
    } catch (e) {
      loading = false;
      toast('fout', `Wereld openen mislukt: ${e.message || e}`);
    }
  }

  function toast(kind, text) {
    const id = Math.random();
    toasts = [...toasts.slice(-3), { id, kind, text }];
    setTimeout(() => (toasts = toasts.filter((t) => t.id !== id)), 5200);
  }

  function onState(s) {
    st = s;
    if (!w3d || !w3d.hf) return;
    w3d.setRobot(s.robot);
    const iid = s.implement?.id ?? null;
    if (iid !== (w3d.implement?.spec.id ?? null) && implements_.length) {   // ook als een ander (API) het koos
      implementId = iid;
      onImplement(implements_.find((c) => c.id === iid) ?? null);
    }
    w3d.updateImplement(s.implement);
    if (s.trail.total < lastTrailTotal) { w3d.clearTrail(); trailXY = []; }
    lastTrailTotal = s.trail.total;
    if (s.trail.points.length) {
      w3d.addTrail(s.trail.points);
      trailXY = [...trailXY, ...s.trail.points].slice(-4000);
    }
    const bad = s.robot.stuck || ab?.drive_fault;
    const waiting = s.gnss?.quality !== 4 || (ab && ab.heading_geldig === false);
    w3d.setBeacon(bad ? 'red' : waiting ? 'amber' : 'green');
    // Gebeurtenissen van de sim (sloot, botsing) als melding; wat er al stond bij het openen slaan we over
    const ev = s.events ?? [];
    if (lastEventT === null) lastEventT = ev.length ? ev[ev.length - 1].t : -1;
    for (const e of ev) if (e.t > lastEventT) { toast(e.kind, e.text); lastEventT = e.t; }
  }

  async function pollAgbot() {
    if (!st?.agbot?.url || !st.agbot.running) { ab = null; return; }
    try {
      ab = await agbot(st.agbot.url, '/api/status');
      if (ab.ab_active || mission) {
        const p = await get('/api/agbot/plan');
        if (p.status === 'ok' && p.versie !== planVersion) {
          planVersion = p.versie;
          order = p.order;
          w3d.setPlannedPath(p.path);
        }
      }
    } catch (err) { console.warn('OpenAgbot-status', err); ab = null; }
  }

  // Handmatig rijden met de pijltjes (via de echte API, dus met dodemansknop): zolang een toets vast zit
  // stuurt de UI 10x per seconde een commando, zoals de webinterface van de robot.
  function driveTick() {
    if (screen !== 'sim' || paused || !st?.agbot?.running || !keys.size) return;
    const up = keys.has('ArrowUp') || keys.has('w'), down = keys.has('ArrowDown') || keys.has('s');
    const left = keys.has('ArrowLeft') || keys.has('a'), right = keys.has('ArrowRight') || keys.has('d');
    const speed = up ? 3.5 : down ? -2.0 : 0;
    const steer = left ? -100 : right ? 100 : 0;
    agbot(st.agbot.url, '/api/nav/manual_drive', { speed_kmh: speed, steering_percentage: steer }).catch(() => {});
  }

  function onKey(e, down) {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (screen === 'menu') {
      if (down && k === 'Enter' && !loading && scene) start();
      return;
    }
    if (down && k === 'Escape') {
      if (showRobotUi) showRobotUi = false; else togglePause();
      return;
    }
    if (paused) return;
    if (down && !e.repeat) {
      if (k === 'c') { setCam(CAMERA_MODES[(CAMERA_MODES.indexOf(camMode) + 1) % CAMERA_MODES.length]); return; }
      if (k === 'p') { post('/api/sim/pause', { paused: !st?.paused }).catch(() => {}); return; }
      if (k === 'h') { set('hud', !settings.hud); return; }
    }
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'w', 'a', 's', 'd', ' '].includes(k)) return;
    e.preventDefault();
    if (k === ' ' && down) { post('/api/agbot/stop').catch(() => {}); keys.clear(); return; }
    if (down) keys.add(k); else keys.delete(k);
  }

  function setCam(m) { camMode = m; w3d.setMode(m); }

  function set(key, value) {
    settings[key] = value;
    saveSettings($state.snapshot(settings));
    applySettings(w3d, settings, key);
  }

  function start() {
    screen = 'sim';
    paused = false;
    setCam('volg');
  }

  function togglePause() {
    if (!paused) {
      wasPaused = !!st?.paused;
      paused = true;
      keys.clear();
      if (!wasPaused) post('/api/sim/pause', { paused: true }).catch(() => {});
    } else {
      paused = false;
      if (!wasPaused) post('/api/sim/pause', { paused: false }).catch(() => {});
    }
  }

  function toMainMenu() {
    if (!wasPaused) post('/api/sim/pause', { paused: false }).catch(() => {});
    paused = false; tab = ''; showRobotUi = false;
    screen = 'menu';
    setCam('rond');
  }

  onMount(async () => {
    w3d = new World3D(viewport);
    w3d.mode = 'rond';
    window.__fieldsim = { w3d };       // voor debuggen in de devtools
    w3d.onPick = ({ x, y, field }) => {
      if (screen !== 'sim' || paused) return;
      if (placing) {
        post('/api/points/add', { x, y }).then((d) => { w3d.setPoints(d.points); pointsVersion++; }).catch(() => {});
        return;
      }
      if (field) { selectedField = field.id; tab = 'missie'; return; }
      // Dubbelklik op de grond: robot daar neerzetten, in dezelfde richting
      post('/api/sim/reset', { x, y, heading: st?.robot?.heading ?? 0 }).catch(() => {});
    };
    try {
      const ws = await get('/api/worlds');
      worlds = ws.worlds; currentWorld = ws.current;
      await loadWorld();
    } catch (e) {
      loading = false;
      toast('fout', `Geen verbinding met de sim: ${e.message || e}`);
    }
    stopState = connectState(onState, (c) => (connected = c));
    const t1 = setInterval(pollAgbot, 400);
    const t2 = setInterval(driveTick, 100);
    const t3 = setInterval(() => { if (w3d && camMode !== w3d.mode) camMode = w3d.mode; }, 300);
    const t4 = setInterval(pollTargets, 1000);
    const kd = (e) => onKey(e, true), ku = (e) => onKey(e, false);
    window.addEventListener('keydown', kd);
    window.addEventListener('keyup', ku);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4);
      window.removeEventListener('keydown', kd); window.removeEventListener('keyup', ku); };
  });
  onDestroy(() => { stopState?.(); w3d?.dispose(); });

  const r = $derived(st?.robot);
  const simTime = $derived.by(() => {
    if (!st) return '0:00';
    const h = Math.floor(st.t / 3600), m = Math.floor((st.t % 3600) / 60), s = Math.floor(st.t % 60);
    return `${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${String(s).padStart(2, '0')}`;
  });
  const status = $derived(
    r?.stuck ? `vast: ${r.stuck}`
      : ab?.ab_active ? (ab.ab_message || ab.ab_state)
        : ab?.navigator_active ? (ab.nav_message || ab.nav_state)
          : ab ? (ab.drive_fault ? `aandrijving: ${ab.drive_fault}` : (Math.abs(r?.speed_kmh ?? 0) > 0.2 ? 'rijdt handmatig' : 'staat stil'))
            : st?.agbot?.running ? 'OpenAgbot start op...' : 'OpenAgbot draait niet'
  );
  const statusTone = $derived(r?.stuck || ab?.drive_fault ? 'bad' : ab?.ab_active || ab?.navigator_active ? 'go'
    : ab ? 'idle' : 'warn');
  const gnssTone = $derived(!st?.gnss?.connected ? 'bad' : st.gnss.quality === 4 ? 'go' : st.gnss.quality ? 'warn' : 'bad');
  const currentIdx = $derived(ab?.ab_active ? Math.max(0, (ab.ab_baan_nr ?? 1) - 1) : -1);
  const done = $derived(currentIdx > 0 ? order.slice(0, currentIdx) : []);
  const orderWindow = $derived.by(() => {
    if (!order.length) return [];
    const start = Math.max(0, Math.min(currentIdx - 5, order.length - 14));
    return order.slice(start, start + 14).map((b, i) => ({ b, i: start + i }));
  });
  const title = $derived(mission ? scene?.fields.find((f) => f.id === mission.field_id)?.name : scene?.label);
</script>

<svelte:head><title>OpenAgbot FieldSim{scene ? ` - ${scene.label}` : ''}</title></svelte:head>

<div class="viewport" bind:this={viewport}></div>
<div class="vignette"></div>

{#if screen === 'menu'}
  <MainMenu {worlds} current={currentWorld} {scene} sim={st} {loading} {settings} onSet={set} onStart={start}
    onWorld={switchWorld} />
{:else}
  {#if settings.hud}
    <!-- Linksboven: waar ben ik en hoe gaat het met de robot -->
    <div class="panel tl">
      <div class="eyebrow">{mission ? 'AB-missie' : 'Wereld'}</div>
      <div class="title">{title ?? 'OpenAgbot FieldSim'}</div>
      <div class="chips">
        <span class="chip {statusTone}"><i></i>{status}</span>
        <span class="chip {gnssTone}"><i></i>{st?.gnss?.connected
          ? `${fixLabel[st.gnss.quality] ?? '-'} · ${((st.gnss.error_m ?? 0) * 100).toFixed(1)} cm` : 'GNSS niet verbonden'}</span>
        <span class="chip plain">{surfaceLabel[r?.surface] ?? r?.surface ?? '-'}{#if r?.pitch !== undefined}
          · helling {r.pitch.toFixed(1)}° · dwars {r.roll.toFixed(1)}°{/if}</span>
      </div>
    </div>

    <!-- Dok met de panelen -->
    <div class="dock panel">
      {#each tabs as [k, label, icon]}
        <button class:active={tab === k} onclick={() => (tab = tab === k ? '' : k)} title={label}>
          <Icon name={icon} /><span>{label}</span>
        </button>
      {/each}
      <span class="sep"></span>
      <button class:active={showRobotUi} onclick={() => (showRobotUi = !showRobotUi)} title="De echte webinterface van de robot">
        <Icon name="robot" /><span>Robot-UI</span>
      </button>
    </div>

    {#if tab}
      <div class="panel drawer">
        <div class="drawer-head">
          <span>{tabs.find((t) => t[0] === tab)?.[1]}</span>
          <button class="ghost" onclick={() => (tab = '')} title="Sluiten"><Icon name="close" size={16} /></button>
        </div>
        <div class="drawer-body">
          {#if tab === 'missie'}
            <MissionPanel {scene} bind:selected={selectedField}
              onPlan={(p) => { mission = p; w3d.setMission(p); planVersion = null; }}
              onStarted={() => { w3d.clearTrail(); trailXY = []; setCam('volg'); }}
              onFocus={(f) => w3d.focus(f.center[0], f.center[1], Math.sqrt(f.area_ha * 1e4) * 1.4)} />
          {:else if tab === 'wereld'}
            <WorldPanel {scene} {w3d} current={currentWorld} onSwitch={switchWorld} onRebuilt={loadWorld} />
          {:else if tab === 'punten'}
            <PointsPanel {scene} {w3d} bind:placing reload={pointsVersion} />
          {:else if tab === 'werktuig'}
            <ImplementPanel catalog={implements_} bind:current={implementId} imp={st?.implement} onChange={onImplement} />
          {:else if tab === 'replay'}
            <ReplayPanel {w3d} onFocus={(x, y, d) => w3d.focus(x, y, d)} />
          {:else if tab === 'storingen'}
            <FaultPanel sim={st} />
          {:else if tab === 'console'}
            <ConsolePanel sim={st} />
          {/if}
        </div>
      </div>
    {/if}

    <!-- Midden boven: simtijd en pauze -->
    <div class="panel clock" class:paused={st?.paused}>
      <button class="ghost" onclick={() => post('/api/sim/pause', { paused: !st?.paused })} title="Pauze (P)">
        <Icon name={st?.paused ? 'play' : 'pause'} size={18} />
      </button>
      <span class="time num">{simTime}</span>
      <span class="rate">{st?.paused ? 'pauze' : '1.0× real time'}</span>
      {#if !connected}<span class="chip bad"><i></i>geen verbinding</span>{/if}
    </div>

    <!-- Rechtsboven: camera, weergave, menu -->
    <div class="tr">
      <div class="panel seg">
        {#each CAMERA_MODES as m}
          <button class:active={camMode === m} onclick={() => setCam(m)} title="Camera: {camLabel[m][0]} (C)">
            <Icon name={camLabel[m][1]} size={18} /><span>{camLabel[m][0]}</span>
          </button>
        {/each}
      </div>
      <div class="panel seg">
        <button class:active={showView} onclick={() => (showView = !showView)} title="Weergave"><Icon name="eye" size={18} /></button>
        <button onclick={togglePause} title="Menu (Esc)"><Icon name="menu" size={18} /></button>
      </div>
      {#if showView}
        <div class="panel view">
          <div class="sec">Weergave</div>
          {#each viewToggles as [k, label]}
            <button class:active={settings[k]} onclick={() => set(k, !settings[k])}>
              <span class="box">{#if settings[k]}<Icon name="check" size={13} />{/if}</span>{label}
            </button>
          {/each}
          <div class="row sun"><Icon name="sun" size={16} />
            <input type="range" min="6" max="21" step="0.25" value={settings.hour} oninput={(e) => set('hour', +e.currentTarget.value)} />
          </div>
        </div>
      {/if}
    </div>

    <!-- Melding als de robot vastzit -->
    {#if r?.stuck}
      <div class="panel alarm">
        <Icon name="fault" size={22} />
        <div><b>Robot zit vast</b><span>{r.stuck}</span></div>
        <button onclick={() => post('/api/sim/faults', { clear_stuck: true })}>Eruit zetten</button>
        <button onclick={() => post('/api/sim/reset', {})}>Naar startplek</button>
      </div>
    {/if}

    <DriveHud robot={r} battery={st?.battery} target={ab?.ab_active || ab?.navigator_active ? ab?.target_speed_kmh : null} />

    <!-- Rechtsonder: minikaart en missie -->
    <div class="panel br">
      <MiniMap {scene} robot={r} {mission} trail={trailXY} doneSwaths={done} current={ab?.ab_active ? ab.current_swath : null} />
      <div class="meta num">
        {#if ab?.ab_active}<span>baan <b>{ab.ab_baan_nr}</b> / {ab.ab_totaal_banen}</span>{/if}
        {#if st?.implement}<span>{st.implement.lowered ? 'werktuig omlaag' : 'werktuig omhoog'}</span>{/if}
        {#if st}<span class="muted">CAN {st.can.connected ? 'ok' : 'weg'} · stuur {st.steering.connected ? 'ok' : 'weg'}</span>{/if}
      </div>
    </div>

    <!-- Linksonder: baanvolgorde of de toetsen -->
    {#if order.length}
      <div class="panel bl">
        <span class="lab">Baanvolgorde</span>
        {#each orderWindow as o}
          <span class="pass num" class:done={o.i < currentIdx} class:now={o.i === currentIdx}>{o.b + 1}</span>
        {/each}
        {#if order.length > 14}<span class="muted">… {order.length}</span>{/if}
      </div>
    {:else if !tab}
      <div class="panel bl hint">
        <span><kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> rijden</span>
        <span><kbd>Spatie</kbd> stop</span>
        <span><kbd>C</kbd> camera</span>
        <span><kbd>Esc</kbd> menu</span>
      </div>
    {/if}
  {:else}
    <div class="panel hudhint">HUD verborgen · <kbd>H</kbd> toont hem weer</div>
  {/if}

  {#if showRobotUi && st?.agbot?.url}
    <div class="robot-ui panel">
      <div class="row head"><Icon name="robot" /><b>Webinterface van de robot</b>
        <span class="muted small">{st.agbot.url} · de echte OpenAgbot-frontend</span>
        <span class="grow"></span><button class="ghost" onclick={() => (showRobotUi = false)} title="Sluiten (Esc)"><Icon name="close" size={16} /></button></div>
      <iframe title="OpenAgbot" src={st.agbot.url}></iframe>
    </div>
  {/if}

  {#if loading}<div class="panel loading"><span class="spin"></span>Wereld laden...</div>{/if}
  {#if paused}
    <PauseMenu {settings} onSet={set} onResume={togglePause} onHome={() => post('/api/sim/reset', {}).catch(() => {})}
      onMainMenu={toMainMenu} />
  {/if}
{/if}

<div class="toasts">
  {#each toasts as t (t.id)}
    <div class="toast panel {t.kind}"><b>{t.kind}</b>{t.text}</div>
  {/each}
</div>

<style>
  .viewport { position: absolute; inset: 0; }
  .vignette { position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(ellipse at 50% 45%, transparent 55%, rgba(4, 8, 10, 0.34) 100%); }

  .tl { top: var(--margin); left: var(--margin); min-width: 330px; max-width: 620px; padding: 10px 14px 11px;
    border-left: 3px solid var(--accent); }
  .eyebrow { font-family: var(--display); font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent); }
  .title { line-height: 1.1; margin: 1px 0 8px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; align-items: center; gap: 7px; padding: 2px 9px 3px; border-radius: 11px; font-size: 12.5px;
    background: rgba(255, 255, 255, 0.07); white-space: nowrap; }
  .chip i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
  .chip.go i { background: var(--green); box-shadow: 0 0 7px var(--green); }
  .chip.warn i { background: var(--amber); box-shadow: 0 0 7px var(--amber); }
  .chip.bad { background: rgba(255, 90, 77, 0.2); color: #ffc9c4; }
  .chip.bad i { background: var(--red); box-shadow: 0 0 7px var(--red); }
  .chip.plain { color: var(--muted); }

  .dock { top: 118px; left: var(--margin); padding: 6px; display: flex; flex-direction: column; gap: 3px; width: 74px; }
  .dock button { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 7px 2px 5px; min-height: 0;
    background: transparent; border-color: transparent; color: var(--muted); }
  .dock button span { font-family: var(--display); font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; }
  .dock button:hover { color: var(--ink); background: rgba(255, 255, 255, 0.08); }
  .dock button.active { color: var(--accent); background: var(--accent-soft); border-color: transparent;
    box-shadow: inset 3px 0 0 var(--accent); }
  .dock .sep { height: 1px; background: var(--line); margin: 3px 6px; }

  .drawer { top: 118px; left: calc(var(--margin) + 82px); width: 372px; max-height: calc(100% - 118px - 150px); padding: 0;
    display: flex; flex-direction: column; animation: rise 0.18s; }
  .drawer-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 8px 14px;
    border-bottom: 1px solid var(--line); font-family: var(--display); font-size: 18px; font-weight: 600;
    letter-spacing: 0.08em; text-transform: uppercase; }
  .drawer-body { padding: 12px 14px 14px; overflow: auto; }
  .ghost { background: transparent; border-color: transparent; padding: 4px 6px; min-height: 28px; display: inline-flex;
    align-items: center; }

  .clock { top: var(--margin); left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 10px;
    padding: 5px 16px 5px 6px; border-radius: 22px; }
  .clock .time { font-family: var(--display); font-size: 24px; font-weight: 600; line-height: 1; min-width: 52px; }
  .clock .rate { font-size: 12px; color: var(--muted); letter-spacing: 0.04em; }
  .clock.paused .rate { color: var(--amber); }
  .clock .ghost { border-radius: 50%; width: 30px; height: 30px; justify-content: center; padding: 0;
    background: rgba(255, 255, 255, 0.08); }

  .tr { position: absolute; top: var(--margin); right: var(--margin); display: flex; gap: 8px; align-items: flex-start; }
  .seg { position: static; display: flex; gap: 3px; padding: 4px; }
  .seg button { display: flex; align-items: center; gap: 6px; background: transparent; border-color: transparent;
    color: var(--muted); font-family: var(--display); font-size: 14px; letter-spacing: 0.06em; text-transform: uppercase; }
  .seg button:hover { color: var(--ink); background: rgba(255, 255, 255, 0.08); }
  .seg button.active { color: var(--accent); background: var(--accent-soft); }
  .view { top: 46px; right: 0; width: 214px; display: flex; flex-direction: column; gap: 5px; animation: rise 0.15s; }
  .view button { display: flex; align-items: center; gap: 9px; text-align: left; }
  .view .box { width: 16px; height: 16px; border: 1px solid var(--line-strong); border-radius: 3px; display: grid;
    place-items: center; }
  .view button.active .box { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .view .sun { margin-top: 6px; color: var(--amber); }
  .view .sun input { flex: 1; min-width: 0; }

  .alarm { top: 66px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 12px;
    background: rgba(88, 18, 14, 0.9); border-color: rgba(255, 90, 77, 0.7); color: #ffd9d5; animation: rise 0.2s; }
  .alarm div { display: flex; flex-direction: column; line-height: 1.2; margin-right: 8px; }
  .alarm b { font-family: var(--display); font-size: 17px; letter-spacing: 0.08em; text-transform: uppercase; }
  .alarm span { font-size: 12.5px; }

  .br { right: var(--margin); bottom: var(--margin); padding: 8px; }
  .meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; margin: 7px 2px 0; max-width: 300px; }
  .meta b { color: var(--amber); font-weight: 600; }

  .bl { left: var(--margin); bottom: var(--margin); display: flex; gap: 10px; align-items: baseline; padding: 8px 14px; }
  .bl .lab { font-family: var(--display); font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
  .pass { color: var(--faint); font-family: var(--display); font-size: 17px; }
  .pass.done { color: var(--green); }
  .pass.now { color: var(--amber); font-weight: 700; font-size: 24px; }
  .hint { gap: 16px; font-size: 12.5px; color: var(--muted); align-items: center; padding: 7px 12px; }
  .hint span { display: inline-flex; align-items: center; gap: 4px; }
  kbd { font-family: var(--display); font-size: 12.5px; font-weight: 600; min-width: 20px; height: 20px; padding: 0 5px;
    display: inline-flex; align-items: center; justify-content: center; color: var(--ink);
    border: 1px solid var(--line-strong); border-bottom-width: 2px; border-radius: 4px; background: rgba(255, 255, 255, 0.07); }
  .hudhint { left: 50%; bottom: var(--margin); transform: translateX(-50%); font-size: 12.5px; color: var(--muted);
    padding: 6px 12px; opacity: 0.75; }

  .robot-ui { inset: 64px 70px 60px 110px; padding: 0; display: flex; flex-direction: column; overflow: hidden; z-index: 20;
    animation: rise 0.2s; }
  .robot-ui .head { padding: 8px 8px 8px 14px; gap: 10px; border-bottom: 1px solid var(--line); }
  .robot-ui iframe { flex: 1; border: 0; width: 100%; background: #fff; }

  .loading { left: 50%; top: 50%; transform: translate(-50%, -50%); display: flex; gap: 12px; align-items: center;
    padding: 12px 20px; border-radius: 26px; font-family: var(--display); font-size: 17px; letter-spacing: 0.08em;
    text-transform: uppercase; z-index: 40; }
  .spin { width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--track); border-top-color: var(--accent);
    animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .toasts { position: absolute; top: 66px; right: var(--margin); display: flex; flex-direction: column; gap: 8px;
    align-items: flex-end; pointer-events: none; z-index: 50; }
  .toast { position: static; max-width: 380px; padding: 8px 14px; border-left: 3px solid var(--amber); animation: rise 0.22s;
    font-size: 13px; }
  .toast b { display: block; font-family: var(--display); font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--amber); }
  .toast.fout { border-left-color: var(--red); }
  .toast.fout b { color: var(--red); }
</style>
