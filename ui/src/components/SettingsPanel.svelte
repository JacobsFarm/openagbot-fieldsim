<script>
  // Weergave-instellingen (hoofdmenu en pauzemenu). Elke wijziging is direct in beeld te zien.
  let { settings, onSet } = $props();

  const quality = [['laag', 'Laag', 'geen schaduw, grof terrein'], ['normaal', 'Normaal', 'schaduw, terrein op 1 m'],
    ['hoog', 'Hoog', 'scherpe schaduw, volle resolutie']];
  const toggles = [
    ['shadows', 'Schaduwen', 'zon werpt schaduw van robot, gebouwen en bomen'],
    ['clouds', 'Wolken', 'bewolkte lucht in plaats van strakblauw'],
    ['boundaries', 'Perceelgrenzen', 'lichte lijn om elk veld'],
    ['labels', 'Veldnamen', 'naamkaartje boven een veld als je uitzoomt'],
    ['nogo', 'Sloten markeren', 'rode lijn waar de robot vastloopt (insteek van sloot en water)'],
    ['edges', 'CAD-randen', 'zwarte randen op robot en werktuig, zoals in FreeCAD'],
  ];
  const clock = (h) => `${Math.floor(h)}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
</script>

<div class="sec">Kwaliteit</div>
<div class="seg">
  {#each quality as [k, label, hint]}
    <button class:active={settings.quality === k} title={hint} onclick={() => onSet('quality', k)}>{label}</button>
  {/each}
</div>
<div class="tiny muted hint">{quality.find((q) => q[0] === settings.quality)?.[2]}. Terreindetail geldt vanaf de volgende keer laden.</div>

<div class="sec">Tijd van de dag</div>
<div class="row">
  <input type="range" min="6" max="21" step="0.25" value={settings.hour} class="grow"
    oninput={(e) => onSet('hour', +e.currentTarget.value)} />
  <span class="num clock">{clock(settings.hour)}</span>
</div>

<div class="sec">Beeld</div>
{#each toggles as [k, label, hint]}
  <button class="toggle" class:on={settings[k]} onclick={() => onSet(k, !settings[k])} role="switch" aria-checked={settings[k]}>
    <span class="knob"></span>
    <span class="text"><b>{label}</b><span class="tiny muted">{hint}</span></span>
  </button>
{/each}

<style>
  .sec { margin: 16px 0 6px; }
  .sec:first-child { margin-top: 0; }
  .seg { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .hint { margin-top: 5px; }
  .clock { font-family: var(--display); font-size: 20px; width: 52px; text-align: right; }
  .toggle { display: flex; gap: 12px; align-items: center; width: 100%; text-align: left; padding: 7px 10px;
    margin-top: 5px; background: transparent; }
  .toggle:hover { background: rgba(255, 255, 255, 0.06); }
  .knob { position: relative; flex: none; width: 34px; height: 18px; border-radius: 9px; background: var(--track);
    transition: background 0.15s; }
  .knob::after { content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%;
    background: var(--muted); transition: transform 0.15s, background 0.15s; }
  .toggle.on .knob { background: var(--accent-soft); box-shadow: inset 0 0 0 1px var(--accent); }
  .toggle.on .knob::after { transform: translateX(16px); background: var(--accent); }
  .text { display: flex; flex-direction: column; gap: 1px; }
  .text b { font-weight: 500; }
</style>
