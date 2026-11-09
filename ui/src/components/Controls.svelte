<script>
  // Besturing: toetsen en muis in één overzicht (hoofdmenu en pauzemenu).
  const drive = [[['W', '↑'], 'vooruit (3,5 km/u)'], [['S', '↓'], 'achteruit'], [['A', '←'], 'stuur links'],
    [['D', '→'], 'stuur rechts'], [['Spatie'], 'stop: missie en rijden']];
  const view = [[['C'], 'volgende camerastand'], [['P'], 'pauze'], [['H'], 'HUD verbergen (schermafbeelding)'],
    [['Esc'], 'menu']];
  const mouse = [['Slepen', 'camera draaien (vrije camera)'], ['Rechts slepen', 'camera verschuiven'],
    ['Scrollen', 'in- en uitzoomen'], ['Dubbelklik op een veld', 'missie voor dat veld'],
    ['Dubbelklik op de grond', 'robot daar neerzetten']];
</script>

<div class="cols">
  <div>
    <div class="sec">Rijden</div>
    {#each drive as [keys, what]}
      <div class="line"><span class="keys">{#each keys as k}<kbd>{k}</kbd>{/each}</span><span>{what}</span></div>
    {/each}
    <div class="tiny muted note">Rijden gaat via de API van de robot, dus met de echte dodemansknop: laat je de toets los,
      dan stopt hij.</div>
  </div>
  <div>
    <div class="sec">Beeld</div>
    {#each view as [keys, what]}
      <div class="line"><span class="keys">{#each keys as k}<kbd>{k}</kbd>{/each}</span><span>{what}</span></div>
    {/each}
    <div class="sec">Muis</div>
    {#each mouse as [k, what]}
      <div class="line"><span class="keys wide">{k}</span><span class="muted">{what}</span></div>
    {/each}
  </div>
</div>

<style>
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }
  .sec { margin: 14px 0 6px; }
  .cols > div > .sec:first-child { margin-top: 0; }
  .line { display: flex; gap: 12px; align-items: center; padding: 4px 0; }
  .keys { display: flex; gap: 4px; min-width: 84px; }
  .keys.wide { min-width: 150px; font-weight: 500; }
  kbd { font-family: var(--display); font-size: 14px; font-weight: 600; min-width: 28px; height: 26px; padding: 0 7px;
    display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--line-strong);
    border-bottom-width: 3px; border-radius: 5px; background: rgba(255, 255, 255, 0.07); }
  .note { margin-top: 8px; max-width: 300px; }
</style>
