<script>
  // OpenAgbot FieldSim: 3D-wereld in FreeCAD-stijl met de panelen van de veldanimaties eroverheen.
  import { onDestroy, onMount } from 'svelte';
  import Bar from './components/Bar.svelte';
  import ConsolePanel from './components/ConsolePanel.svelte';
  import FaultPanel from './components/FaultPanel.svelte';
  import ImplementPanel from './components/ImplementPanel.svelte';
  import MiniMap from './components/MiniMap.svelte';
  import MissionPanel from './components/MissionPanel.svelte';
  import PointsPanel from './components/PointsPanel.svelte';
  import ReplayPanel from './components/ReplayPanel.svelte';
  import WorldPanel from './components/WorldPanel.svelte';
  import { agbot, connectState, get, getTerrain, post } from './lib/api.js';
  import { CAMERA_MODES, World3D } from './lib/scene/world3d.js';

  let viewport;
  let w3d = null;
  let scene = $state(null);
  let worlds = $state([]);
  let currentWorld = $state('');
  let st = $state(null);                 // live toestand van de sim
  let ab = $state(null);                 // status van OpenAgbot
  let connected = $state(false);
  let tab = $state('missie');
  let showRobotUi = $state(false);
  let placing = $state(false);           // punten zetten met dubbelklik
  let pointsVersion = $state(0);
  let camMode = $state('volg');
  let mission = $state(null);
  let selectedField = $state(null);
  let order = $state([]);
  let trailXY = $state([]);
  let lastTrailTotal = 0;
  let planVersion = null;
  let implements_ = $state([]);         // catalogus van aanbouwdelen
  let implementId = $state(null);
  let keys = new Set();
  let stopState;

  const fixLabel = { 4: 'RTK fixed', 5: 'RTK float', 2: 'DGPS', 1: 'GPS', 0: 'geen fix' };

  async function loadWorld() {
    const [s, t] = await Promise.all([get('/api/world'), getTerrain(2)]);
    scene = s;
    w3d.load(s, t.header, t.heights);
    mission = null; order = []; trailXY = []; lastTrailTotal = 0;
    const cat = await get('/api/implements');
    implements_ = cat.implements; implementId = cat.current;
    w3d.setImplement(implements_.find((c) => c.id === implementId) ?? null);
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
    await post('/api/world/load', { name });
    currentWorld = name;
    await loadWorld();
  }

  function onState(s) {
    st = s;
    if (!w3d) return;
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
    if (!st?.agbot?.running || !keys.size) return;
    const up = keys.has('ArrowUp') || keys.has('w'), down = keys.has('ArrowDown') || keys.has('s');
    const left = keys.has('ArrowLeft') || keys.has('a'), right = keys.has('ArrowRight') || keys.has('d');
    const speed = up ? 3.5 : down ? -2.0 : 0;
    const steer = left ? -100 : right ? 100 : 0;
    agbot(st.agbot.url, '/api/nav/manual_drive', { speed_kmh: speed, steering_percentage: steer }).catch(() => {});
  }

  function onKey(e, down) {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'w', 'a', 's', 'd', ' '].includes(k)) return;
    e.preventDefault();
    if (k === ' ' && down) { post('/api/agbot/stop').catch(() => {}); keys.clear(); return; }
    if (down) keys.add(k); else keys.delete(k);
  }

  function setCam(m) { camMode = m; w3d.setMode(m); }

  onMount(async () => {
    w3d = new World3D(viewport);
    window.__fieldsim = { w3d };       // voor debuggen in de devtools
    w3d.onPick = ({ x, y, field }) => {
      if (placing) {
        post('/api/points/add', { x, y }).then((d) => { w3d.setPoints(d.points); pointsVersion++; }).catch(() => {});
        return;
      }
      if (field) { selectedField = field.id; tab = 'missie'; return; }
      // Dubbelklik op de grond: robot daar neerzetten, in dezelfde richting
      post('/api/sim/reset', { x, y, heading: st?.robot?.heading ?? 0 }).catch(() => {});
    };
    const ws = await get('/api/worlds');
    worlds = ws.worlds; currentWorld = ws.current;
    await loadWorld();
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
  const simTime = $derived(st ? `${Math.floor(st.t / 60)}:${String(Math.floor(st.t % 60)).padStart(2, '0')}` : '0:00');
  const status = $derived(
    r?.stuck ? `vast: ${r.stuck}`
      : ab?.ab_active ? (ab.ab_message || ab.ab_state)
        : ab?.navigator_active ? (ab.nav_message || ab.nav_state)
          : ab ? (ab.drive_fault ? `aandrijving: ${ab.drive_fault}` : (r?.speed_kmh > 0.2 ? 'rijdt handmatig' : 'staat stil'))
            : st?.agbot?.running ? 'OpenAgbot start op...' : 'OpenAgbot draait niet'
  );
  const currentIdx = $derived(ab?.ab_active ? Math.max(0, (ab.ab_baan_nr ?? 1) - 1) : -1);
  const done = $derived(currentIdx > 0 ? order.slice(0, currentIdx) : []);
  const orderWindow = $derived.by(() => {
    if (!order.length) return [];
    const start = Math.max(0, Math.min(currentIdx - 5, order.length - 16));
    return order.slice(start, start + 16).map((b, i) => ({ b, i: start + i }));
  });
</script>

<svelte:head><title>OpenAgbot FieldSim{scene ? ` - ${scene.label}` : ''}</title></svelte:head>

<div class="viewport" bind:this={viewport}></div>

<!-- Titel en status: linksboven, zoals in de veldanimaties -->
<div class="panel tl">
  <div class="title">{mission ? `${scene?.fields.find((f) => f.id === mission.field_id)?.name}: AB-missie` : scene?.label ?? 'OpenAgbot FieldSim'}</div>
  <div class="line"><span class="muted">Robot:</span> {status}</div>
  <div class="line"><span class="muted">GNSS:</span>
    {st?.gnss?.connected ? `${fixLabel[st.gnss.quality] ?? '-'} · ${ab?.heading_bron ?? '-'} · fout ${((st.gnss.error_m ?? 0) * 100).toFixed(1)} cm` : 'niet verbonden'}</div>
  <div class="line"><span class="muted">Ondergrond:</span> {r?.surface ?? '-'}{#if r?.pitch !== undefined} · helling {r.pitch.toFixed(1)}° · dwars {r.roll.toFixed(1)}°{/if}</div>
</div>

<!-- Tijd: rechtsboven -->
<div class="panel tr">
  <div class="title">{st?.paused ? 'pauze' : '1.0× real time'}</div>
  <div class="muted right">simtijd {simTime}{connected ? '' : ' · geen verbinding'}</div>
</div>

<!-- Werkbalk -->
<div class="toolbar">
  <select value={currentWorld} onchange={(e) => switchWorld(e.currentTarget.value)} title="Wereld">
    {#each worlds as w}<option value={w.name}>{w.label}</option>{/each}
  </select>
  <span class="sep"></span>
  {#each CAMERA_MODES as m}<button class:active={camMode === m} onclick={() => setCam(m)}>{m}</button>{/each}
  <span class="sep"></span>
  {#each [['wereld', 'Wereld'], ['missie', 'Missie'], ['punten', 'Punten'], ['werktuig', 'Werktuig'], ['replay', 'Replay'], ['storingen', 'Storingen'], ['console', 'Console']] as [k, label]}
    <button class:active={tab === k} onclick={() => (tab = tab === k ? '' : k)}>{label}</button>
  {/each}
  <button class:active={showRobotUi} onclick={() => (showRobotUi = !showRobotUi)}>Robot-UI</button>
</div>

{#if tab}
  <div class="panel drawer">
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
{/if}

<!-- Minikaart en meters: rechtsonder -->
<div class="panel br">
  <MiniMap {scene} robot={r} {mission} trail={trailXY} doneSwaths={done.map((b) => b)} current={ab?.ab_active ? ab.current_swath : null} />
  <Bar label="accu" value={st ? `${st.battery.v.toFixed(1)} V · ${Math.round(st.battery.soc * 100)} %` : '-'}
    fraction={st?.battery.soc ?? 0} color="var(--blue)" />
  <Bar label="snelheid" value={r ? `${r.speed_kmh.toFixed(2)} km/h` : '-'} fraction={(r?.speed_kmh ?? 0) / 5.7}
    color="var(--orange)" />
  <div class="small" style="margin-top:8px">
    {#if r}koers {r.heading.toFixed(1)}° · stuur L {r.wheel_left_deg.toFixed(1)}° R {r.wheel_right_deg.toFixed(1)}° · {r.odometer_m.toFixed(0)} m gereden{/if}
  </div>
  <div class="tiny muted">
    {#if st}eRPM {Object.values(st.vesc).map((v) => Math.round(v.erpm)).join(' / ')} · CAN {st.can.connected ? 'ok' : 'weg'} · stuur {st.steering.connected ? 'ok' : 'weg'}{#if ab} · baan {ab.ab_baan_nr}/{ab.ab_totaal_banen}{/if}{/if}
  </div>
</div>

<!-- Baanvolgorde: linksonder -->
{#if order.length}
  <div class="panel bl">
    <span class="muted">Baanvolgorde:</span>
    {#each orderWindow as o}
      <span class="pass" class:done={o.i < currentIdx} class:now={o.i === currentIdx}>{o.b + 1}</span>
    {/each}
    {#if order.length > 16}<span class="muted">… ({order.length})</span>{/if}
  </div>
{:else}
  <div class="panel bl hint small muted">Rijden: pijltjes of WASD · spatie = stop · dubbelklik op een veld = missie</div>
{/if}

{#if showRobotUi && st?.agbot?.url}
  <div class="robot-ui">
    <div class="row head"><b>Webinterface van de robot</b><span class="muted small">{st.agbot.url} — de echte OpenAgbot-frontend</span>
      <span class="grow"></span><button onclick={() => (showRobotUi = false)}>Sluiten</button></div>
    <iframe title="OpenAgbot" src={st.agbot.url}></iframe>
  </div>
{/if}

<style>
  .viewport { position: absolute; inset: 0; }
  .tl { top: var(--margin); left: var(--margin); min-width: 380px; max-width: 560px; }
  .tl .line { font-size: 15px; margin-top: 3px; }
  .tr { top: var(--margin); right: var(--margin); text-align: right; }
  .right { text-align: right; font-size: 15px; }
  .toolbar { position: absolute; top: 96px; right: var(--margin); display: flex; flex-direction: column; gap: 5px;
    align-items: stretch; background: var(--panel); border-radius: var(--radius); padding: 8px; width: 150px; }
  .toolbar select { width: 100%; }
  .sep { height: 1px; background: var(--line); margin: 3px 0; }
  .drawer { top: 128px; left: var(--margin); width: 360px; max-height: calc(100% - 200px); overflow: auto; }
  .br { right: var(--margin); bottom: var(--margin); width: 398px; }
  .bl { left: var(--margin); bottom: var(--margin); font-size: 16px; display: flex; gap: 9px; align-items: baseline; }
  .bl.hint { font-size: 12px; }
  .pass { color: var(--muted); }
  .pass.done { color: var(--green); }
  .pass.now { color: var(--orange); font-weight: 700; font-size: 20px; }
  .robot-ui { position: absolute; inset: 70px 60px 60px 60px; background: #fff; border-radius: var(--radius);
    box-shadow: 0 0 0 1px var(--line); display: flex; flex-direction: column; overflow: hidden; }
  .robot-ui .head { padding: 8px 12px; gap: 12px; border-bottom: 1px solid var(--line); }
  .robot-ui iframe { flex: 1; border: 0; width: 100%; }
</style>
