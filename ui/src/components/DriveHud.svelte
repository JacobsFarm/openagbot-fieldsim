<script>
  // Rijcluster onderin: kompas, stuurhoek, snelheid en accu. Alle waarden komen uit de sim-snapshot.
  let { robot = null, battery = null, target = null } = $props();

  const MAX = 6;                                     // km/u volle schaal
  const speed = $derived(robot?.speed_kmh ?? 0);
  const frac = $derived(Math.min(1, Math.abs(speed) / MAX));
  const steer = $derived(robot ? (robot.wheel_left_deg + robot.wheel_right_deg) / 2 : 0);
  const heading = $derived(((robot?.heading ?? 0) % 360 + 360) % 360);
  const soc = $derived(battery?.soc ?? 0);

  // Boog van de snelheidsmeter: 220 graden, straal 46
  const R = 46, A0 = 200, SPAN = 220;
  function arc(f) {
    const a0 = (A0 * Math.PI) / 180, a1 = ((A0 + SPAN * f) * Math.PI) / 180;
    const p = (a) => `${60 + R * Math.cos(a)} ${58 + R * Math.sin(a)}`;
    return `M ${p(a0)} A ${R} ${R} 0 ${SPAN * f > 180 ? 1 : 0} 1 ${p(a1)}`;
  }

  const winds = ['N', 'NO', 'O', 'ZO', 'Z', 'ZW', 'W', 'NW'];
  // Kompasband: streepjes om de 15 graden, 2,2 px per graad, +-60 graden in beeld
  const ticks = $derived.by(() => {
    const out = [];
    const start = Math.ceil((heading - 62) / 15) * 15;
    for (let d = start; d <= heading + 62; d += 15) {
      const n = ((d % 360) + 360) % 360;
      out.push({ x: 132 + (d - heading) * 2.2, label: n % 45 === 0 ? winds[n / 45] : null, major: n % 45 === 0 });
    }
    return out;
  });
</script>

<div class="cluster panel">
  <svg class="compass" width="264" height="26" viewBox="0 0 264 26">
    {#each ticks as t}
      <line x1={t.x} x2={t.x} y1={t.major ? 15 : 18} y2="24" stroke="currentColor" stroke-width={t.major ? 1.6 : 1} opacity={t.major ? 0.9 : 0.45} />
      {#if t.label}<text x={t.x} y="11" text-anchor="middle" class:north={t.label === 'N'}>{t.label}</text>{/if}
    {/each}
    <path d="M132 26 l-5 -7 h10 z" fill="var(--accent)" />
  </svg>

  <div class="main">
    <div class="side">
      <div class="lab">Stuur</div>
      <svg width="92" height="34" viewBox="0 0 92 34">
        <line x1="6" x2="86" y1="24" y2="24" stroke="var(--track)" stroke-width="4" stroke-linecap="round" />
        <line x1="46" x2="46" y1="16" y2="30" stroke="var(--muted)" stroke-width="1.2" />
        <line x1="46" x2={46 + Math.max(-40, Math.min(40, steer * 1.15))} y1="24" y2="24" stroke="var(--accent)" stroke-width="4" stroke-linecap="round" />
        <circle cx={46 + Math.max(-40, Math.min(40, steer * 1.15))} cy="24" r="5" fill="var(--ink)" />
      </svg>
      <div class="val num">{steer >= 0 ? 'R' : 'L'} {Math.abs(steer).toFixed(1)}°</div>
    </div>

    <div class="speed">
      <svg width="120" height="84" viewBox="0 0 120 84">
        <path d={arc(1)} stroke="var(--track)" stroke-width="6" fill="none" stroke-linecap="round" />
        {#if target}<path d={arc(Math.min(1, target / MAX))} stroke="rgba(255,255,255,0.22)" stroke-width="6" fill="none" stroke-linecap="round" />{/if}
        {#if frac > 0.005}<path d={arc(frac)} stroke={speed < 0 ? 'var(--amber)' : 'var(--accent)'} stroke-width="6" fill="none" stroke-linecap="round" />{/if}
      </svg>
      <div class="digits num">{Math.abs(speed).toFixed(1)}</div>
      <div class="unit">{speed < -0.05 ? 'km/u achteruit' : 'km/u'}</div>
    </div>

    <div class="side">
      <div class="lab">Accu</div>
      <div class="batt"><div class="fill" class:low={soc < 0.25} style="width:{Math.max(3, soc * 100)}%"></div></div>
      <div class="val num">{battery ? `${Math.round(soc * 100)} % · ${battery.v.toFixed(1)} V` : '-'}</div>
    </div>
  </div>

  <div class="foot num">
    <span>koers <b>{heading.toFixed(0)}°</b></span>
    <span>{robot ? `${robot.odometer_m.toFixed(0)} m` : '-'} gereden</span>
    {#if battery}<span>{Math.abs(battery.a).toFixed(1)} A</span>{/if}
  </div>
</div>

<style>
  .cluster { left: 50%; bottom: var(--margin); transform: translateX(-50%); padding: 6px 18px 9px; display: flex;
    flex-direction: column; align-items: center; }
  .compass { color: var(--ink); overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent, #000 22%, #000 78%, transparent); }
  .compass text { font-family: var(--display); font-size: 12px; font-weight: 600; fill: var(--muted); }
  .compass text.north { fill: var(--accent); }
  .main { display: flex; align-items: flex-end; gap: 14px; }
  .side { width: 108px; display: flex; flex-direction: column; align-items: center; gap: 2px; padding-bottom: 6px; }
  .lab { font-family: var(--display); font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
  .val { font-size: 12.5px; }
  .speed { position: relative; width: 120px; height: 84px; }
  .digits { position: absolute; left: 0; right: 0; top: 22px; text-align: center; font-family: var(--display);
    font-size: 42px; font-weight: 600; line-height: 1; }
  .unit { position: absolute; left: 0; right: 0; top: 63px; text-align: center; font-size: 11px; color: var(--muted);
    letter-spacing: 0.06em; text-transform: uppercase; }
  .batt { width: 92px; height: 12px; margin: 11px 0 9px; border-radius: 3px; background: var(--track); overflow: hidden; }
  .batt .fill { height: 100%; background: var(--blue); border-radius: 3px; transition: width 0.4s; }
  .batt .fill.low { background: var(--red); }
  .foot { display: flex; gap: 18px; font-size: 12px; color: var(--muted); margin-top: 2px; }
  .foot b { color: var(--ink); font-weight: 500; }
</style>
