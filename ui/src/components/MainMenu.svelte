<script>
  // Hoofdmenu over het draaiende 3D-beeld (de camera maakt een rustig rondje om het erf).
  import Controls from './Controls.svelte';
  import Icon from './Icon.svelte';
  import SettingsPanel from './SettingsPanel.svelte';

  let { worlds = [], current = '', scene = null, sim = null, loading = false, settings, onSet, onStart, onWorld } = $props();
  let view = $state('wereld');           // rechterkolom: wereld | instellingen | besturing

  const world = $derived(worlds.find((w) => w.name === current));
  const hectares = $derived(scene ? scene.fields.reduce((s, f) => s + f.area_ha, 0).toFixed(1) : '-');
  const backend = $derived(sim?.agbot?.running ? 'OpenAgbot draait' : sim ? 'OpenAgbot start op...' : 'geen verbinding met de sim');

  const items = [['wereld', 'Wereld kiezen', 'world'], ['instellingen', 'Instellingen', 'settings'],
    ['besturing', 'Besturing', 'keys']];
</script>

<div class="menu">
  <div class="left">
    <div class="brand">
      <div class="mark"><span></span><span></span><span></span></div>
      <div>
        <div class="name">OPENAGBOT <b>FIELDSIM</b></div>
        <div class="tag">Software-in-the-loop veldsimulator voor de Bruut</div>
      </div>
    </div>

    <nav>
      <button class="go" onclick={onStart} disabled={loading || !scene}>
        <Icon name="play" size={22} />
        <span><b>Start simulatie</b><small>{world?.label ?? '...'}</small></span>
      </button>
      {#each items as [k, label, icon]}
        <button class="item" class:active={view === k} onclick={() => (view = k)}>
          <Icon name={icon} /><span>{label}</span><Icon name="arrow" size={16} />
        </button>
      {/each}
    </nav>

    <div class="foot">
      <span class="dot" class:ok={sim?.agbot?.running}></span>{backend}
      <span class="sep"></span>Enter = start
    </div>
  </div>

  <div class="right panel">
    {#if view === 'wereld'}
      <div class="head">Wereld</div>
      <div class="worlds">
        {#each worlds as w}
          <button class="card" class:active={w.name === current} onclick={() => onWorld(w.name)} disabled={loading}>
            <span class="pick">{#if w.name === current}<Icon name="check" size={16} />{/if}</span>
            <span class="info">
              <b>{w.label}</b>
              <small>{w.error ? `fout: ${w.error}` : `${w.fields} velden · ${w.buildings} gebouwen`}</small>
            </span>
          </button>
        {/each}
      </div>
      {#if scene}
        <div class="stats">
          <div><b class="num">{scene.fields.length}</b><span>velden</span></div>
          <div><b class="num">{hectares}</b><span>hectare</span></div>
          <div><b class="num">{scene.ditches.length + scene.surfaces.filter((s) => s.kind === 'water').length}</b><span>sloten en water</span></div>
          <div><b class="num">{scene.buildings.length}</b><span>gebouwen</span></div>
        </div>
      {/if}
      <div class="tiny muted">Een eigen plek toevoegen (kaarten van PDOK, AHN en 3D BAG) kan in de sim onder Wereld.</div>
    {:else if view === 'instellingen'}
      <div class="head">Instellingen</div>
      <SettingsPanel {settings} {onSet} />
    {:else}
      <div class="head">Besturing</div>
      <Controls />
    {/if}
  </div>

  {#if loading}<div class="loading"><span class="spin"></span>Wereld laden...</div>{/if}
</div>

<style>
  .menu { position: absolute; inset: 0; display: flex; align-items: stretch; animation: fade 0.35s;
    background: linear-gradient(90deg, rgba(7, 11, 14, 0.92) 0, rgba(7, 11, 14, 0.74) 26%, rgba(7, 11, 14, 0.12) 58%, transparent 80%),
      linear-gradient(0deg, rgba(7, 11, 14, 0.5) 0, transparent 26%); }
  .left { width: min(430px, 40vw); padding: 7vh 0 28px 5vw; display: flex; flex-direction: column; }
  .brand { display: flex; gap: 16px; align-items: center; animation: rise 0.5s both; }
  .mark { display: grid; gap: 5px; width: 44px; }
  .mark span { height: 7px; border-radius: 2px; background: var(--accent); transform: skewX(-18deg); }
  .mark span:nth-child(2) { width: 76%; opacity: 0.75; }
  .mark span:nth-child(3) { width: 48%; opacity: 0.5; }
  .name { font-family: var(--display); font-size: 40px; line-height: 1; letter-spacing: 0.04em; font-weight: 500; }
  .name b { font-weight: 700; color: var(--accent); }
  .tag { color: var(--muted); margin-top: 6px; }

  nav { display: flex; flex-direction: column; gap: 8px; margin-top: 9vh; }
  nav button { display: flex; align-items: center; gap: 14px; text-align: left; min-height: 50px; padding: 8px 16px;
    font-family: var(--display); font-size: 21px; letter-spacing: 0.05em; text-transform: uppercase;
    background: rgba(255, 255, 255, 0.04); border-color: var(--line); animation: rise 0.45s both; }
  nav button:nth-child(2) { animation-delay: 0.05s; }
  nav button:nth-child(3) { animation-delay: 0.1s; }
  nav button:nth-child(4) { animation-delay: 0.15s; }
  nav button span { flex: 1; }
  nav .item:hover, nav .item.active { transform: translateX(6px); }
  nav .go { min-height: 72px; background: var(--accent); color: var(--accent-ink); border-color: var(--accent); margin-bottom: 10px; }
  nav .go:hover { background: #cbf060; transform: translateX(6px); }
  nav .go span { display: flex; flex-direction: column; line-height: 1.1; }
  nav .go b { font-size: 26px; font-weight: 700; }
  nav .go small { font-family: var(--font); font-size: 13px; letter-spacing: 0; text-transform: none; opacity: 0.75; }

  .foot { margin-top: auto; display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12.5px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); }
  .dot.ok { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .sep { width: 1px; height: 12px; background: var(--line-strong); margin: 0 6px; }

  .right { position: absolute; left: calc(min(430px, 40vw) + 5vw + 34px); top: 7vh; width: min(470px, 42vw);
    max-height: 82vh; overflow: auto; padding: 18px 20px 20px; animation: rise 0.45s 0.12s both; }
  .head { font-family: var(--display); font-size: 24px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
    margin-bottom: 12px; }
  .worlds { display: flex; flex-direction: column; gap: 6px; }
  .card { display: flex; align-items: center; gap: 12px; text-align: left; min-height: 54px; padding: 8px 12px; }
  .card .pick { width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--line-strong); display: grid;
    place-items: center; flex: none; }
  .card.active .pick { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
  .card .info { display: flex; flex-direction: column; }
  .card b { font-size: 15.5px; font-weight: 600; }
  .card small { color: var(--muted); font-size: 12.5px; }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 16px 0 12px; }
  .stats div { display: flex; flex-direction: column; padding: 8px 10px; background: var(--well); border-radius: 6px;
    border: 1px solid var(--line); }
  .stats b { font-family: var(--display); font-size: 26px; font-weight: 600; line-height: 1.1; }
  .stats span { color: var(--muted); font-size: 11.5px; }

  .loading { position: absolute; left: 50%; bottom: 9vh; transform: translateX(-50%); display: flex; gap: 12px;
    align-items: center; padding: 10px 18px; background: var(--panel); border: 1px solid var(--line); border-radius: 24px;
    font-family: var(--display); font-size: 17px; letter-spacing: 0.08em; text-transform: uppercase; }
  .spin { width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--track); border-top-color: var(--accent);
    animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
