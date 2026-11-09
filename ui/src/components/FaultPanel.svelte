<script>
  // Storingen inschieten: zien wat de echte robotsoftware doet als de GNSS, de sturing of de aandrijving wegvalt.
  import { post } from '../lib/api.js';

  let { sim = null } = $props();
  const fixes = [['rtk_fixed', 'RTK fixed'], ['rtk_float', 'RTK float'], ['dgps', 'DGPS'], ['gps', 'GPS'],
    ['none', 'geen fix']];
  const vescFaults = [[0, 'geen'], [4, 'ABS_OVER_CURRENT'], [5, 'OVER_TEMP_FET'], [2, 'UNDER_VOLTAGE']];
  let msg = $state('');

  async function set(body) {
    try { await post('/api/sim/faults', body); msg = ''; } catch (e) { msg = String(e.message || e); }
  }

  const g = $derived(sim?.gnss?.settings ?? {});
</script>

<div class="sec">GNSS</div>
<div class="row wrap">
  {#each fixes as [key, label]}
    <button class:active={g.fix === key} onclick={() => set({ fix: key })}>{label}</button>
  {/each}
</div>
<div class="row wrap" style="margin-top:6px">
  <button class:active={g.heading_valid === false} onclick={() => set({ heading_valid: !g.heading_valid })}>
    {g.heading_valid === false ? 'Heading terug' : 'Heading weg (THS V)'}</button>
  <button class:active={g.stream_enabled === false} onclick={() => set({ stream_enabled: !g.stream_enabled })}>
    {g.stream_enabled === false ? 'Stroom terug' : 'Ontvanger stil'}</button>
</div>
<div class="row" style="margin-top:6px">
  <label class="grow">correctieleeftijd (s)
    <input type="number" min="0" max="120" step="1" value={g.correction_age_s ?? 1}
      onchange={(e) => set({ correction_age_s: +e.currentTarget.value })} /></label>
  <label class="grow">ruis (x)
    <input type="number" min="0" max="20" step="0.5" value={g.noise_scale ?? 1}
      onchange={(e) => set({ noise_scale: +e.currentTarget.value })} /></label>
</div>

<div class="sec">Sturing (Arduino)</div>
<div class="row wrap">
  <button class:active={sim?.steering?.responsive === false}
    onclick={() => set({ steering_responsive: !(sim?.steering?.responsive ?? true) })}>
    {sim?.steering?.responsive === false ? 'Arduino antwoordt weer' : 'Arduino antwoordt niet'}</button>
  <button onclick={() => set({ steering_stalled: true })}>Stappenmotor vast</button>
  <button onclick={() => set({ steering_stalled: false })}>los</button>
</div>

<div class="sec">Aandrijving (VESC)</div>
<div class="row wrap">
  <button onclick={() => set({ vesc_online: false })}>CAN weg</button>
  <button onclick={() => set({ vesc_online: true })}>CAN terug</button>
  <select onchange={(e) => set({ vesc_fault_code: +e.currentTarget.value })}>
    {#each vescFaults as [code, label]}<option value={code}>fout: {label}</option>{/each}
  </select>
</div>

<div class="sec">Robot</div>
<div class="tiny muted" style="margin-bottom:4px">Dubbelklik op de grond zet de robot daar neer.</div>
<div class="row wrap">
  <button onclick={() => post('/api/sim/pause', { paused: !sim?.paused })}>{sim?.paused ? 'Verder' : 'Pauze'}</button>
  <button onclick={() => set({ clear_stuck: true })} disabled={!sim?.robot?.stuck}>Uit de sloot</button>
  <button onclick={() => post('/api/sim/reset', {})}>Naar startplek</button>
  <button onclick={() => post('/api/world/start_pose', {}).then(() => (msg = 'startplek bewaard'))}>Hier = startplek</button>
</div>
{#if msg}<div class="small" style="margin-top:6px">{msg}</div>{/if}

<style>
  .sec { font-weight: 700; margin: 12px 0 4px; }
  .sec:first-child { margin-top: 0; }
  .wrap { flex-wrap: wrap; }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  input { width: 100%; }
</style>
