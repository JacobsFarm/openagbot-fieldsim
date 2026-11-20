<script>
  // OpenAgbot FieldSim: de 3D-wereld met een hoofdmenu, een pauzemenu en de HUD eroverheen.
  import { onDestroy, onMount } from 'svelte';
  import ConsolePanel from './components/ConsolePanel.svelte';
  import DriveHud from './components/DriveHud.svelte';
  import FaultPanel from './components/FaultPanel.svelte';
  import FieldMap2D from './components/FieldMap2D.svelte';
  import FleetPanel from './components/FleetPanel.svelte';
  import Icon from './components/Icon.svelte';
  import ImplementPanel from './components/ImplementPanel.svelte';
  import MainMenu from './components/MainMenu.svelte';
  import MiniMap from './components/MiniMap.svelte';
  import MissionPanel from './components/MissionPanel.svelte';
  import PauseMenu from './components/PauseMenu.svelte';
  import PointsPanel from './components/PointsPanel.svelte';
  import WorldEditor from './components/WorldEditor.svelte';
  import ReplayPanel from './components/ReplayPanel.svelte';
  import WorldPanel from './components/WorldPanel.svelte';
  import { loadAerialTiles } from './lib/aerial.js';
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
  let screen = $state('menu');           // menu | sim | editor
  let loading = $state(true);
  let paused = $state(false);            // pauzemenu open
  let settings = $state(loadSettings());
  let tab = $state('');
  let drawerMax = $state((() => { try { return localStorage.getItem('fieldsim.drawerMax') === '1'; } catch { return false; } })());
  $effect(() => { try { localStorage.setItem('fieldsim.drawerMax', drawerMax ? '1' : '0'); } catch { /* privé */ } });
  let robotUi = $state.raw(null);        // {name, url}: webinterface van de sim-robot of van een robot uit de vloot
  let followId = $state(null);           // robot uit de vloot die de camera en de meters volgen (null = sim-robot)
  let showView = $state(false);          // uitklapper 'weergave' (lagen)
  let placing = $state(false);           // punten zetten met dubbelklik
  let pointsVersion = $state(0);
  let camMode = $state('rond');
  let map2d = $state(false);             // vlakke plattegrond in plaats van het 3D-beeld
  let mapRef = $state(null);
  let mapInfo = $state.raw(null);        // muispositie, veld onder de muis en schaalbalk van de plattegrond
  let mapFollow = $state(false);
  let plannedPath = $state.raw(null);    // route die de robot zelf plant (ook op de plattegrond)
  let points2d = $state.raw([]);
  let startPoints = $state.raw([]);      // startpunten per veld (waar de robot een veldmissie mag beginnen)
  let placingStart = $state(false);      // dubbelklik zet een startpunt bij het gekozen veld
  let mission = $state.raw(null);
  let selectedField = $state(null);
  let order = $state.raw([]);
  let trailXY = $state.raw([]);
  let toasts = $state([]);
  let lastTrailTotal = 0;
  let lastEventT = null;
  let lastFleetSeq = null;
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
  const tabs = [['vloot', 'Vloot', 'fleet'], ['missie', 'Missie', 'mission'], ['punten', 'Punten', 'points'], ['werktuig', 'Werktuig', 'implement'],
    ['replay', 'Replay', 'replay'], ['storingen', 'Storingen', 'fault'], ['console', 'Console', 'console'],
    ['wereld', 'Wereld', 'world']];
  const viewToggles = [['boundaries', 'Perceelgrenzen'], ['labels', 'Veldnamen'], ['nogo', 'Sloten markeren'],
    ['farm', 'Doorgangen, zones, slootzone'], ['aerial', 'Luchtfoto (PDOK)']];

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
      if (settings.aerial) loadAerial(s);
      mission = null; order = []; trailXY = []; lastTrailTotal = 0; plannedPath = null; points2d = [];
      placingStart = false;
      setStarts(s.start_points ?? []);
      const cat = await get('/api/implements');
      implements_ = cat.implements; implementId = cat.current;
      w3d.setImplement(implements_.find((c) => c.id === implementId) ?? null);
      stopState?.restart();              // sporen (sim en vloot) opnieuw vanaf het begin in de nieuwe wereld
    } catch (e) {
      toast('fout', `Wereld laden mislukt: ${e.message || e}`);
    }
    loading = false;
  }

  // Luchtfoto van PDOK over de 3D-wereld (zelfde bron en cache als in de editor)
  async function loadAerial(s = scene) {
    if (!s || !w3d) return;
    const xs = s.area.map((p) => p[0]), ys = s.area.map((p) => p[1]);
    try {
      const tiles = await loadAerialTiles(Math.min(...xs) - 30, Math.min(...ys) - 30, Math.max(...xs) + 30,
        Math.max(...ys) + 30);
      if (settings.aerial && scene === s) w3d.setAerial(tiles);
    } catch (e) {
      toast('fout', `Luchtfoto: ${e.message || e}`);
      settings.aerial = false; saveSettings($state.snapshot(settings));
    }
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
    w3d.setFleet(s.fleet?.robots);
    // Meldingen van de vloot (verbonden, verbinding weg, fout op een robot)
    const fe = s.fleet?.events ?? [];
    if (lastFleetSeq === null) lastFleetSeq = fe.length ? fe[fe.length - 1].seq : 0;
    for (const e of fe) if (e.seq > lastFleetSeq) { toast(e.kind, e.text); lastFleetSeq = e.seq; }
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
      if (ab.ab_active || (mission && mission.kind !== 'veldmissie')) {
        const p = await get('/api/agbot/plan');
        if (p.status === 'ok' && p.versie !== planVersion) {
          planVersion = p.versie;
          order = p.order;
          w3d.setPlannedPath(p.path);
          plannedPath = p.path;
        }
      }
    } catch (err) { console.warn('OpenAgbot-status', err); ab = null; }
  }

  // Handmatig rijden met de pijltjes (via de echte API, dus met dodemansknop): zolang een toets vast zit
  // stuurt de UI 10x per seconde een commando, zoals de webinterface van de robot.
  async function refreshWorlds() {
    try { const ws = await get('/api/worlds'); worlds = ws.worlds; currentWorld = ws.current; } catch { /* sim herstart */ }
  }

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
    if (screen === 'editor') return;          // de editor heeft zijn eigen toetsen
    if (screen === 'menu') {
      if (down && k === 'Enter' && !loading && scene) start();
      return;
    }
    if (down && k === 'Escape') {
      if (robotUi) robotUi = null; else togglePause();
      return;
    }
    if (paused) return;
    if (down && !e.repeat) {
      if (k === 'c') { pickCam(CAMERA_MODES[(CAMERA_MODES.indexOf(camMode) + 1) % CAMERA_MODES.length]); return; }
      if (k === 'm') { map2d = !map2d; return; }
      if (k === 'p') { post('/api/sim/pause', { paused: !st?.paused }).catch(() => {}); return; }
      if (k === 'h') { set('hud', !settings.hud); return; }
    }
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'w', 'a', 's', 'd', ' '].includes(k)) return;
    e.preventDefault();
    if (k === ' ' && down) {             // stop: de sim-robot, en de echte robot die je volgt
      post('/api/agbot/stop').catch(() => {});
      if (followId) post(`/api/fleet/${followId}/stop`).catch((err) => toast('fout', `Stop: ${err.message || err}`));
      keys.clear();
      return;
    }
    if (down) keys.add(k); else keys.delete(k);
  }

  function setCam(m) { camMode = m; w3d.setMode(m); }
  function pickCam(m) { map2d = false; setCam(m); }       // een camera kiezen = terug naar 3D

  // Dubbelklik in 3D of op de plattegrond: punt zetten, veld openen of de robot daar neerzetten
  function setStarts(list) { startPoints = list; }
  // 3D: palen met een vlagje; het punt waar de geplande missie begint krijgt een andere kleur
  $effect(() => {
    const list = startPoints, active = mission?.start_point?.id ?? null;      // eerst lezen: daar hangt het effect aan
    if (w3d?.hf) w3d.setStartPoints(list, active);
  });
  $effect(() => { if (tab !== 'missie') placingStart = false; });

  function pick({ x, y, field }) {
    if (screen !== 'sim' || paused) return;
    if (placingStart && selectedField) {
      post('/api/startpoints/add', { field: selectedField, x, y }).then((d) => setStarts(d.start_points))
        .catch((e) => toast('fout', `Startpunt: ${e.message || e}`));
      return;
    }
    if (placing) {
      post('/api/points/add', { x, y }).then((d) => { w3d.setPoints(d.points); pointsVersion++; }).catch(() => {});
      return;
    }
    if (field) { selectedField = field.id; tab = 'missie'; return; }
    post('/api/sim/reset', { x, y, heading: st?.robot?.heading ?? 0 }).catch(() => {});
  }

  // Het 3D-beeld hoeft niet te renderen zolang de plattegrond ervoor staat
  $effect(() => { if (w3d) w3d.suspended = map2d && screen === 'sim'; });

  function set(key, value) {
    settings[key] = value;
    saveSettings($state.snapshot(settings));
    applySettings(w3d, settings, key);
    if (key === 'aerial') { if (value) loadAerial(); else w3d.setAerial(null); }
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
    paused = false; tab = ''; robotUi = null;
    screen = 'menu';
    setCam('rond');
  }

  onMount(async () => {
    w3d = new World3D(viewport);
    w3d.mode = 'rond';
    window.__fieldsim = { w3d };       // voor debuggen in de devtools
    w3d.onPick = pick;
    w3d.onPoints = (p) => (points2d = p);
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
  // Vloot: de robot die je volgt levert de stand voor de camera, de rijmeters en de kaart
  const fleetRobots = $derived(st?.fleet?.robots ?? []);
  const live = $derived(followId ? fleetRobots.find((x) => x.id === followId) ?? null : null);
  const others = $derived(fleetRobots.filter((x) => x.enabled && x.pose?.in_world)
    .map((x) => ({ id: x.id, name: x.name, color: x.online ? x.color : '#5f717a', x: x.pose.x, y: x.pose.y, heading: x.pose.heading })));
  const hudRobot = $derived(live?.telemetry ? { speed_kmh: live.telemetry.speed_kmh, heading: live.telemetry.heading ?? 0,
    wheel_left_deg: live.telemetry.wheel_left_deg ?? 0, wheel_right_deg: live.telemetry.wheel_right_deg ?? 0, live: live.name } : r);
  const hudBattery = $derived(live ? (live.telemetry ? { soc: live.telemetry.battery_soc, v: live.telemetry.battery_v } : null)
    : st?.battery);
  const liveTone = $derived(!live ? '' : !live.online || live.telemetry?.fault ? 'bad'
    : live.telemetry?.geofence || live.telemetry?.fix !== 4 ? 'warn' : live.telemetry?.mode === 'idle' ? 'idle' : 'go');
  const liveStatus = $derived(!live ? '' : !live.online ? 'geen verbinding' : live.telemetry.fault ? `fout: ${live.telemetry.fault}`
    : live.telemetry.geofence ? `geofence: ${live.telemetry.geofence}`
    : live.telemetry.message || live.telemetry.state || { idle: 'staat stil', manual: 'rijdt handmatig' }[live.telemetry.mode] || live.telemetry.mode);
  $effect(() => { if (followId && st?.fleet && !fleetRobots.some((x) => x.id === followId)) followId = null; });
  $effect(() => { const id = followId; w3d?.setFollow(id); });

  function follow(id) {
    followId = id;
    if (map2d) mapFollow = true; else setCam('volg');
  }

  function focusRobot(x) {
    w3d.focus(x.pose.x, x.pose.y, 60);
    mapRef?.centerOn(x.pose.x, x.pose.y);
  }
  const simTime = $derived.by(() => {
    if (!st) return '0:00';
    const h = Math.floor(st.t / 3600), m = Math.floor((st.t % 3600) / 60), s = Math.floor(st.t % 60);
    return `${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${String(s).padStart(2, '0')}`;
  });
  const status = $derived(
    r?.stuck ? `vast: ${r.stuck}`
      : ab?.ab_active ? (ab.ab_message || ab.ab_state)
        : ab?.missie_actief ? (ab.missie_message || ab.missie_state)
        : ab?.navigator_active ? (ab.nav_message || ab.nav_state)
        : ab?.geofence && (!ab.geofence.vooruit || !ab.geofence.achteruit) ? `geofence van de robot: ${ab.geofence.reden}`
        : ab?.missie_state === 'FOUT' ? ab.missie_message
          : ab ? (ab.drive_fault ? `aandrijving: ${ab.drive_fault}` : (Math.abs(r?.speed_kmh ?? 0) > 0.2 ? 'rijdt handmatig' : 'staat stil'))
            : st?.agbot?.running ? 'OpenAgbot start op...' : 'OpenAgbot draait niet'
  );
  const statusTone = $derived(r?.stuck || ab?.drive_fault || (ab?.geofence && !ab.geofence.vooruit) ? 'bad'
    : ab?.ab_active || ab?.navigator_active || ab?.missie_actief ? 'go'
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
{#if !(map2d && screen === 'sim')}<div class="vignette"></div>{/if}

{#if screen === 'menu'}
  <MainMenu {worlds} current={currentWorld} {scene} sim={st} {loading} {settings} onSet={set} onStart={start}
    onWorld={switchWorld} onEdit={() => (screen = 'editor')} />
{:else if screen === 'editor'}
  <WorldEditor {worlds} onClose={() => { screen = 'menu'; refreshWorlds(); }} onSaved={() => { lastEventT = null; loadWorld(); }}
    onWorldCreated={refreshWorlds} />
{:else}
  {#if map2d}
    <FieldMap2D bind:this={mapRef} bind:follow={mapFollow} {scene} robot={r} {others}
      center={live?.pose?.in_world ? live.pose : null} {mission} trail={trailXY} doneSwaths={done}
      current={ab?.ab_active ? ab.current_swath : null} selected={selectedField} path={plannedPath} points={points2d}
      starts={startPoints} activeStart={mission?.start_point?.id ?? null}
      layers={settings} onPick={pick} onSelect={(id) => { if (!placingStart) selectedField = id; }}
      onInfo={(i) => (mapInfo = i)} />
  {/if}
  {#if settings.hud}
    <!-- Linksboven: waar ben ik en hoe gaat het met de robot -->
    <div class="panel tl">
      <div class="eyebrow">{live ? 'Live · echte robot' : mission ? (mission.kind === 'veldmissie' ? 'Veldmissie' : 'AB-missie') : 'Wereld'}</div>
      <div class="title">{title ?? 'OpenAgbot FieldSim'}</div>
      {#if live}
        <div class="chips livebar">
          <span class="chip live" style="--c:{live.color}"><i></i>Live · {live.name}</span>
          <span class="chip {liveTone}"><i></i>{liveStatus}</span>
          {#if live.online}<span class="chip plain">{live.telemetry.fix_label || 'geen fix'}{#if !live.pose?.in_world} · buiten deze wereld{/if}</span>{/if}
          <button class="danger" disabled={!live.online}
            onclick={() => post(`/api/fleet/${live.id}/stop`).catch((e) => toast('fout', `Stop: ${e.message || e}`))}>Stop</button>
        </div>
      {:else}
      <div class="chips">
        <span class="chip {statusTone}"><i></i>{status}</span>
        <span class="chip {gnssTone}"><i></i>{st?.gnss?.connected
          ? `${fixLabel[st.gnss.quality] ?? '-'} · ${((st.gnss.error_m ?? 0) * 100).toFixed(1)} cm` : 'GNSS niet verbonden'}</span>
        <span class="chip plain">{surfaceLabel[r?.surface] ?? r?.surface ?? '-'}{#if r?.pitch !== undefined}
          · helling {r.pitch.toFixed(1)}° · dwars {r.roll.toFixed(1)}°{/if}</span>
      </div>
      {/if}
    </div>

    <!-- Dok met de panelen -->
    <div class="dock panel">
      {#each tabs as [k, label, icon]}
        <button class:active={tab === k} onclick={() => (tab = tab === k ? '' : k)} title={label}>
          <Icon name={icon} /><span>{label}</span>
        </button>
      {/each}
      <span class="sep"></span>
      <button class:active={!!robotUi} disabled={!st?.agbot?.url && !live} title="De echte webinterface van de robot"
        onclick={() => (robotUi = robotUi ? null : live ? { name: live.name, url: live.url } : { name: 'de sim-robot', url: st.agbot.url })}>
        <Icon name="robot" /><span>Robot-UI</span>
      </button>
    </div>

    {#if tab}
      <div class="panel drawer" class:max={drawerMax}>
        <div class="drawer-head">
          <span>{tabs.find((t) => t[0] === tab)?.[1]}</span>
          <span class="grow"></span>
          <button class="ghost" onclick={() => (drawerMax = !drawerMax)} title={drawerMax ? 'Kleiner' : 'Groot scherm'}>
            <Icon name={drawerMax ? 'shrink' : 'expand'} size={16} /></button>
          <button class="ghost" onclick={() => (tab = '')} title="Sluiten"><Icon name="close" size={16} /></button>
        </div>
        <div class="drawer-body">
          {#if tab === 'vloot'}
            <FleetPanel fleet={st?.fleet} simRobot={r} {followId} onFollow={follow} onFocus={focusRobot} {toast}
              onOpenUi={(x) => (robotUi = { name: x.name, url: x.url })} />
          {:else if tab === 'missie'}
            <MissionPanel {scene} wide={drawerMax} bind:selected={selectedField} {startPoints} bind:placingStart onStartPoints={setStarts}
              onPlan={(p) => { mission = p; w3d.setMission(p); planVersion = null; if (p?.kind === 'veldmissie') { order = []; w3d.setPlannedPath(null); plannedPath = null; } }}
              onStarted={() => { w3d.clearTrail(); trailXY = []; setCam('volg'); }}
              onFocus={(f) => { w3d.focus(f.center[0], f.center[1], Math.sqrt(f.area_ha * 1e4) * 1.4); mapRef?.fitField(f.id); }} />
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
          <button class:active={!map2d && camMode === m} onclick={() => pickCam(m)} title="Camera: {camLabel[m][0]} (C)">
            <Icon name={camLabel[m][1]} size={18} /><span>{camLabel[m][0]}</span>
          </button>
        {/each}
        <button class:active={map2d} onclick={() => (map2d = !map2d)} title="Plattegrond: vlakke 2D-kaart met perceelgrenzen (M)">
          <Icon name="plan" size={18} /><span>Kaart</span>
        </button>
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

    <!-- Bewaking: robot of werktuig kwam in een verboden gebied of te dicht bij een sloot -->
    {#if st?.geofence?.alarm && !r?.stuck}
      <div class="panel alarm">
        <Icon name="shield" size={22} />
        <div><b>Gestopt door de bewaking</b><span>robot of werktuig komt in {st.geofence.alarm.text}</span></div>
        <button onclick={() => post('/api/agbot/stop').catch(() => {})}>Stop robot</button>
        <button onclick={() => post('/api/sim/reset', {})}>Naar startplek</button>
        <button onclick={() => post('/api/sim/pause', { paused: false })}>Toch verder</button>
      </div>
    {/if}

    <!-- Melding als de robot vastzit -->
    {#if r?.stuck}
      <div class="panel alarm">
        <Icon name="fault" size={22} />
        <div><b>Robot zit vast</b><span>{r.stuck}</span></div>
        <button onclick={() => post('/api/sim/faults', { clear_stuck: true })}>Eruit zetten</button>
        <button onclick={() => post('/api/sim/reset', {})}>Naar startplek</button>
      </div>
    {/if}

    <DriveHud robot={hudRobot} battery={hudBattery}
      target={!live && (ab?.ab_active || ab?.navigator_active) ? ab?.target_speed_kmh : null} />

    <!-- Rechtsonder: minikaart en missie -->
    <div class="panel br">
      {#if map2d}
        <!-- Plattegrond: de minikaart is dan dubbel; hier staan de bediening, de schaal en de muispositie -->
        <div class="mapbar">
          <button onclick={() => mapRef?.fitAll()} title="Hele wereld in beeld">Alles</button>
          <button disabled={!selectedField} onclick={() => mapRef?.fitField(selectedField)} title="Gekozen veld in beeld">Veld</button>
          <button class:active={mapFollow} onclick={() => (mapFollow = !mapFollow)} title="Robot in het midden houden">Volg robot</button>
        </div>
        <div class="mapinfo num">
          {#if mapInfo}<span class="scale"><i style="width:{mapInfo.barPx}px"></i>{mapInfo.barM} m</span>{/if}
          {#if mapInfo?.x != null}<span>x {mapInfo.x.toFixed(1)} · y {mapInfo.y.toFixed(1)} m</span>{/if}
          {#if mapInfo?.field}<span><b>{mapInfo.field.name}</b> · {mapInfo.field.area_ha.toFixed(2)} ha</span>{/if}
        </div>
      {:else}
        <MiniMap {scene} robot={r} {others} {mission} trail={trailXY} doneSwaths={done} current={ab?.ab_active ? ab.current_swath : null}
          starts={startPoints} />
      {/if}
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
        <span><kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> {live ? 'sim-robot rijden' : 'rijden'}</span>
        <span><kbd>Spatie</kbd> stop</span>
        <span><kbd>C</kbd> camera</span>
        <span><kbd>M</kbd> kaart</span>
        <span><kbd>Esc</kbd> menu</span>
      </div>
    {/if}
  {:else}
    <div class="panel hudhint">HUD verborgen · <kbd>H</kbd> toont hem weer</div>
  {/if}

  {#if robotUi}
    <div class="robot-ui panel">
      <div class="row head"><Icon name="robot" /><b>Webinterface van {robotUi.name}</b>
        <span class="muted small">{robotUi.url} · de eigen frontend van de robot</span>
        <span class="grow"></span><button class="ghost" onclick={() => (robotUi = null)} title="Sluiten (Esc)"><Icon name="close" size={16} /></button></div>
      <iframe title="OpenAgbot" src={robotUi.url}></iframe>
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
  .chip.live { background: color-mix(in srgb, var(--c) 22%, transparent); font-weight: 600; }
  .chip.live i { background: var(--c); box-shadow: 0 0 7px var(--c); animation: pulse 1.6s infinite; }
  .livebar { align-items: center; }
  .livebar button { min-height: 24px; padding: 1px 10px; }
  @keyframes pulse { 50% { opacity: 0.35; } }

  .dock { top: 118px; left: var(--margin); padding: 6px; display: flex; flex-direction: column; gap: 3px; width: 74px; }
  .dock button { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 2px 4px; min-height: 0;
    background: transparent; border-color: transparent; color: var(--muted); }
  .dock button span { font-family: var(--display); font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; }
  .dock button:hover { color: var(--ink); background: rgba(255, 255, 255, 0.08); }
  .dock button.active { color: var(--accent); background: var(--accent-soft); border-color: transparent;
    box-shadow: inset 3px 0 0 var(--accent); }
  .dock .sep { height: 1px; background: var(--line); margin: 3px 6px; }

  .drawer { top: 118px; left: calc(var(--margin) + 82px); width: 372px; max-height: calc(100% - 118px - 150px); padding: 0;
    display: flex; flex-direction: column; animation: rise 0.18s; }
  .drawer.max { width: min(1120px, calc(100% - var(--margin) * 2 - 82px)); max-height: calc(100% - 118px - var(--margin));
    z-index: 15; background: var(--panel-solid); }
  .drawer-head .grow { flex: 1; }
  .drawer-head { display: flex; align-items: center; gap: 2px; justify-content: space-between; padding: 8px 8px 8px 14px;
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
  .mapbar { display: flex; gap: 5px; width: 300px; }
  .mapbar button { flex: 1; }
  .mapinfo { display: flex; flex-direction: column; gap: 3px; min-height: 58px; margin: 9px 2px 0; font-size: 12px;
    color: var(--muted); }
  .mapinfo b { color: var(--ink); font-weight: 600; }
  .mapinfo .scale { display: flex; align-items: center; gap: 7px; color: var(--ink); }
  .mapinfo .scale i { height: 5px; max-width: 220px; border: 1px solid var(--ink); border-top: 0; }

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
