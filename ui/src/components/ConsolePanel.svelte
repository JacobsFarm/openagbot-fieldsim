<script>
  // OpenAgbot-backend: draait hij, console-uitvoer, herstarten. Plus de gebeurtenissen van de sim.
  import { onDestroy } from 'svelte';
  import { get, post } from '../lib/api.js';

  let { sim = null } = $props();
  let info = $state(null);
  const timer = setInterval(load, 1500);
  load();
  onDestroy(() => clearInterval(timer));

  async function load() {
    try { info = await get('/api/agbot'); } catch { info = null; }
  }
</script>

<div class="row">
  <b>OpenAgbot</b>
  <span class:ok={info?.running} class:bad={!info?.running}>{info?.running ? 'draait' : 'gestopt'}</span>
  <span class="grow"></span>
  <button onclick={() => post('/api/agbot/restart').then(load)}>Herstart</button>
</div>
<div class="tiny muted">{info?.agbot_dir}</div>
<pre>{(info?.log ?? []).slice(-40).join('\n')}</pre>
<b>Sim</b>
<pre class="events">{(sim?.events ?? []).slice().reverse().map((e) => `${e.t.toFixed(1)} s  ${e.kind}: ${e.text}`).join('\n')}</pre>

<style>
  pre { font-family: Consolas, monospace; font-size: 11px; background: #f4f4f1; border-radius: 6px; padding: 6px;
    max-height: 260px; overflow: auto; white-space: pre-wrap; margin: 6px 0 10px; }
  .events { max-height: 140px; }
  .ok { color: var(--green); }
  .bad { color: var(--red); }
</style>
