<script>
  // Balk met label links en waarde rechts (tank, voortgang).
  let { label, value, fraction = 0, color = 'var(--blue)', marker = null } = $props();
</script>

<div class="bar">
  <div class="row"><span class="muted">{label}</span><span class="grow"></span><span class="num">{value}</span></div>
  <div class="track">
    <div class="fill" style="width:{Math.max(1.5, Math.min(100, fraction * 100))}%;background:{color}"></div>
    {#if marker !== null}<div class="marker" style="left:{Math.min(100, marker * 100)}%"></div>{/if}
  </div>
</div>

<style>
  .bar { margin-top: 6px; font-size: 13px; }
  .track { position: relative; height: 8px; background: var(--track); border-radius: 3px; margin-top: 3px; }
  .fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
  .marker { position: absolute; top: -3px; width: 2px; height: 14px; background: var(--ink); }
</style>
