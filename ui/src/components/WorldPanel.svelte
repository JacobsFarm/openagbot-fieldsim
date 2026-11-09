<script>
  // Werelden: openen, nieuwe maken uit coördinaten, bouwen uit kaarten (AHN, BGT, BRP, 3D BAG) en lagen tonen.
  import { onDestroy } from 'svelte';
  import { get, post } from '../lib/api.js';

  let { scene, w3d, current, onSwitch, onRebuilt } = $props();
  let worlds = $state([]);
  let name = $state('');
  let label = $state('');
  let points = $state('');
  let msg = $state('');
  let build = $state(null);
  let timer = null;
  const layers = [['fields', 'Velden'], ['surfaces', 'Vlakken'], ['ditches', 'Sloten'], ['buildings', 'Gebouwen'],
    ['trees', 'Bomen'], ['fences', 'Hekken']];
  let visible = $state(Object.fromEntries(layers.map(([k]) => [k, true])));

  async function refresh() { worlds = (await get('/api/worlds')).worlds; }
  refresh();

  async function create() {
    msg = '';
    try {
      const r = await post('/api/world/new', { name, label, points });
      msg = `wereld ${r.world} gemaakt (${r.area.length} hoeken)`;
      await refresh();
    } catch (e) { msg = String(e.message || e); }
  }

  async function startBuild(world) {
    msg = '';
    try {
      build = await post('/api/world/build', { name: world });
      clearInterval(timer);
      timer = setInterval(poll, 1000);
    } catch (e) { msg = String(e.message || e); }
  }

  async function poll() {
    build = await get('/api/world/build');
    if (!build.running) {
      clearInterval(timer);
      await refresh();
      if (!build.error && build.world === current) onRebuilt?.();
    }
  }

  function toggle(k) { visible[k] = !visible[k]; w3d.setLayerVisible(k, visible[k]); }
  onDestroy(() => clearInterval(timer));
</script>

<div class="sec">Werelden</div>
{#each worlds as w}
  <div class="row item">
    <span class="grow"><b>{w.label}</b> <span class="muted small">{w.fields} velden · {w.buildings} gebouwen</span></span>
    {#if w.name === current}<span class="small muted">geopend</span>{:else}<button onclick={() => onSwitch(w.name)}>Open</button>{/if}
    {#if w.name !== 'example'}<button title="Percelen, vlakken, gebouwen en reliëf ophalen (PDOK, AHN, 3D BAG)"
      disabled={build?.running} onclick={() => startBuild(w.name)}>Bouw</button>{/if}
  </div>
{/each}

{#if build}
  <div class="build small">
    <b>{build.running ? `Bouwen: ${build.world}...` : build.error ? 'Mislukt' : `Klaar: ${build.world}`}</b>
    <pre>{build.log.join('\n')}{build.error ? `\n${build.error}` : ''}</pre>
  </div>
{/if}

<div class="sec">Nieuwe wereld van een echte plek</div>
<div class="grid">
  <label>naam (map)<input type="text" bind:value={name} placeholder="mijn_bedrijf" /></label>
  <label>titel<input type="text" bind:value={label} placeholder="Mijn bedrijf" /></label>
</div>
<label class="full">hoekpunten (lat, lon), in willekeurige volgorde
  <textarea rows="4" bind:value={points} placeholder="52.2147, 4.5889&#10;52.2150, 4.6057&#10;52.2093, 4.5878&#10;52.2095, 4.6064"></textarea>
</label>
<div class="row" style="margin-top:6px">
  <button class="primary" onclick={create} disabled={!name || !points}>Maak</button>
  <span class="tiny muted">daarna "Bouw" (internet nodig, één keer)</span>
</div>
{#if msg}<div class="small" style="margin-top:6px">{msg}</div>{/if}

<div class="sec">Lagen</div>
<div class="row wrap">
  {#each layers as [k, l]}<button class:active={visible[k]} onclick={() => toggle(k)}>{l}</button>{/each}
</div>
<div class="tiny muted" style="margin-top:6px">Bronnen: AHN (reliëf), BGT (vlakken, water, gebouwen), BRP (percelen),
  3D BAG (gebouwhoogtes), via PDOK. Een echte plek blijft buiten git (eigen locatie).</div>

<style>
  .sec { font-weight: 700; margin: 12px 0 4px; }
  .sec:first-child { margin-top: 0; }
  .item { margin: 3px 0; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
  label.full { margin-top: 6px; }
  input, textarea { width: 100%; font-family: var(--font); font-size: 12px; border: 1px solid var(--line);
    border-radius: 6px; padding: 4px 6px; }
  .wrap { flex-wrap: wrap; }
  .build pre { font-family: Consolas, monospace; font-size: 11px; background: #f4f4f1; border-radius: 6px;
    padding: 6px; max-height: 160px; overflow: auto; white-space: pre-wrap; margin: 4px 0; }
</style>
