<script>
  // Pauzemenu (Esc): de sim staat stil zolang het open is.
  import Controls from './Controls.svelte';
  import Icon from './Icon.svelte';
  import SettingsPanel from './SettingsPanel.svelte';

  let { settings, onSet, onResume, onHome, onMainMenu } = $props();
  let view = $state('');

  const items = [
    ['', 'Verder', 'play', () => onResume()],
    ['home', 'Robot naar startplek', 'home', () => { onHome(); onResume(); }],
    ['instellingen', 'Instellingen', 'settings', null],
    ['besturing', 'Besturing', 'keys', null],
    ['menu', 'Hoofdmenu', 'back', () => onMainMenu()],
  ];
</script>

<div class="scrim">
  <div class="box panel" class:wide={view}>
    <div class="list">
      <div class="head">Pauze</div>
      {#each items as [k, label, icon, run]}
        <button class:primary={k === ''} class:active={view === k && k} onclick={() => (run ? run() : (view = view === k ? '' : k))}>
          <Icon name={icon} size={18} /><span>{label}</span>
        </button>
      {/each}
      <div class="tiny muted">Esc = verder</div>
    </div>
    {#if view}
      <div class="detail">
        {#if view === 'instellingen'}<SettingsPanel {settings} {onSet} />{:else}<Controls />{/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .scrim { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(6, 10, 12, 0.55);
    backdrop-filter: blur(3px); animation: fade 0.18s; z-index: 30; }
  .box { position: relative; display: flex; gap: 22px; padding: 20px 22px; animation: rise 0.22s; max-height: 86vh; }
  .list { display: flex; flex-direction: column; gap: 7px; width: 250px; }
  .head { font-family: var(--display); font-size: 30px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
    margin-bottom: 6px; }
  .list button { display: flex; align-items: center; gap: 12px; min-height: 42px; text-align: left; font-family: var(--display);
    font-size: 18px; letter-spacing: 0.05em; text-transform: uppercase; }
  .list .tiny { margin-top: 6px; }
  .detail { width: 500px; overflow: auto; padding-left: 22px; border-left: 1px solid var(--line); }
</style>
