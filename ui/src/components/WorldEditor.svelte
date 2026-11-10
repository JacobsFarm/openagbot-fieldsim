<script>
  // Wereld bewerken: bovenaanzicht van de gekozen wereld, noorden boven, alles in lokale meters.
  // Wat je tekent of wijzigt komt in farm.json (eigen aanpassingen); de kaartdata in world.json blijft de bron,
  // zodat "Bouw" opnieuw kan draaien zonder dat je werk verdwijnt.
  import { onMount } from 'svelte';
  import { loadAerialTiles } from '../lib/aerial.js';
  import { get, post, put } from '../lib/api.js';
  import { approxLatLon, bounds, dist, fmtArea, fmtLen, hitTest, lineLength, nearestOnSegment, newId, ORDER, polyArea,
    rings, round3, snap, STYLE } from '../lib/editor.js';
  import { color, css } from '../theme.js';
  import Icon from './Icon.svelte';

  let { worlds = [], onClose, onSaved, onWorldCreated } = $props();

  let wrap, canvas;
  let st = $state.raw(null);             // toestand van de server (schema, gebied, instellingen)
  let objs = $state.raw([]);             // alle objecten (bron + eigen); vervangen, nooit gemuteerd
  let hidden = $state.raw([]);           // verborgen bronobjecten [{layer, id, anchor?}]
  let settings = $state({});
  let layerUi = $state({});              // laag -> { visible, locked }
  let tool = $state('select');           // select | draw | hole | split | measure
  let drawLayer = $state('fields');
  let sel = $state.raw([]);              // sleutels van gekozen objecten
  let selVertex = $state.raw(null);      // { k, hole, i }
  let draft = $state.raw([]);            // punten van wat je nu tekent
  let cursor = $state.raw(null);         // { p, kind } onder de muis (gesnapt)
  let snapOpt = $state({ vertex: true, edge: true, grid: false, gridM: 1 });
  let dirty = $state(false);
  let busy = $state('');
  let messages = $state.raw([]);
  let side = $state('eigenschappen');    // eigenschappen | boerderij | import
  let showHidden = $state(false);
  let aerialOn = $state(false);
  let offsetM = $state(-1.0);
  let simplifyM = $state(0.1);
  let importSimplify = $state(0.03);
  let newWorld = $state('');
  let copyWorld = $state('');
  let check = $state.raw(null);
  let canUndo = $state(false), canRedo = $state(false);

  let view = { cx: 0, cy: 0, s: 1 };
  let size = { w: 800, h: 600 };
  let undo = [], redo = [];
  let drag = null;
  let aerial = null;
  let raf = 0;
  let spaceDown = false;

  const key = (o) => `${o.layer}:${o.id}`;
  const schema = $derived(st?.schema ?? {});
  const hiddenSet = $derived(new Set(hidden.map((h) => `${h.layer}:${h.id}`)));
  const selObjs = $derived(objs.filter((o) => sel.includes(key(o))));
  const one = $derived(selObjs.length === 1 ? selObjs[0] : null);
  const counts = $derived.by(() => {
    const c = {};
    for (const o of objs) if (!hiddenSet.has(key(o))) c[o.layer] = (c[o.layer] ?? 0) + 1;
    return c;
  });
  const clearanceKinds = [['water', 'sloot / water'], ['weg', 'weg'], ['verhard', 'erf / pad'], ['hek', 'hek'],
    ['gebouw', 'gebouw'], ['boom', 'boom'], ['dijk', 'dijk'], ['verboden', 'verboden gebied'], ['gat', 'gat in veld'],
    ['marker', 'marker']];
  const clearanceDefault = { water: 1.0, weg: 1.0, verhard: 0.3, hek: 0.6, gebouw: 1.5, boom: 1.5, dijk: 0.5,
    verboden: 0.5, gat: 0.3, marker: 0.5 };

  // ------------------------------------------------------------------ laden en bewaren
  function say(text, kind = 'info') {
    messages = [...messages.slice(-7), { text, kind, t: Date.now() }];
  }

  function take(state, keepView = false) {
    st = state;
    objs = ORDER.flatMap((l) => (state.layers[l] ?? []).map((o) => ({ ...o, dirty: o.source === 'overschreven' })));
    hidden = state.hidden.map((h) => ({ layer: h.layer, id: h.id, anchor: h.anchor, obj: h }));
    settings = structuredClone(state.settings);
    settings.clearance ??= {};
    settings.safety ??= {};
    for (const l of Object.keys(state.schema)) {
      layerUi[l] ??= { visible: true, locked: ['surfaces', 'buildings', 'trees'].includes(l) };
    }
    sel = []; selVertex = null; draft = []; undo = []; redo = []; canUndo = canRedo = false; dirty = false;
    for (const r of state.report ?? []) say(r, r.includes('bestaat niet meer') ? 'warn' : 'info');
    if (!keepView) fit();
    redraw();
  }

  async function load(keepView = false) {
    busy = 'laden...';
    try { take(await get('/api/editor/state'), keepView); } catch (e) { say(String(e.message || e), 'fout'); }
    busy = '';
  }

  async function save() {
    busy = 'bewaren en wereld opnieuw opbouwen...';
    const out = {};
    for (const o of objs) {
      if (!schema[o.layer]?.edit || (o.source === 'bron' && !o.dirty)) continue;
      (out[o.layer] ??= []).push({ id: o.id, source: o.source, replaces: o.replaces, props: o.props, pts: o.pts,
        holes: o.holes });
    }
    try {
      const state = await put('/api/editor/state', { objects: out,
        hidden: hidden.map((h) => ({ layer: h.layer, id: h.id, anchor: h.anchor })), settings: $state.snapshot(settings) });
      take(state, true);
      say('Bewaard. De wereld is opnieuw geladen.', 'ok');
      onSaved?.();
    } catch (e) { say(String(e.message || e), 'fout'); }
    busy = '';
  }

  async function restore(name) {
    if (!confirm(`Back-up ${name} terugzetten? De huidige versie gaat eerst zelf naar backups/.`)) return;
    busy = 'terugzetten...';
    try { take(await post('/api/editor/restore', { name }), true); onSaved?.(); } catch (e) { say(String(e.message || e), 'fout'); }
    busy = '';
  }

  // ------------------------------------------------------------------ wijzigen met ongedaan maken
  function snapshot() { return JSON.stringify({ objs, hidden: hidden.map(({ layer, id, anchor }) => ({ layer, id, anchor })) }); }

  function commit(nextObjs, nextHidden = null) {
    undo.push(snapshot());
    if (undo.length > 60) undo.shift();
    redo = [];
    objs = nextObjs;
    if (nextHidden) hidden = nextHidden;
    dirty = true; canUndo = true; canRedo = false;
    redraw();
  }

  function restoreSnap(s) {
    const d = JSON.parse(s);
    objs = d.objs; hidden = d.hidden;
    sel = sel.filter((k) => objs.some((o) => key(o) === k));
    selVertex = null; dirty = true;
    redraw();
  }

  function doUndo() {
    if (!undo.length) return;
    redo.push(snapshot()); restoreSnap(undo.pop());
    canUndo = undo.length > 0; canRedo = true;
  }

  function doRedo() {
    if (!redo.length) return;
    undo.push(snapshot()); restoreSnap(redo.pop());
    canUndo = true; canRedo = redo.length > 0;
  }

  const touch = (o, patch) => ({ ...o, ...patch, dirty: true });
  const replaceObj = (o, next) => objs.map((x) => (x === o ? next : x));

  function setProp(o, k, v, typ) {
    if (typ === 'number' || typ === 'int') v = v === '' || v === null ? null : Number(v);
    commit(replaceObj(o, touch(o, { props: { ...o.props, [k]: v } })));
  }

  function addObject(layer, pts, props = {}) {
    const def = {};
    for (const [k, , , d] of schema[layer].props) if (d !== null && d !== '') def[k] = d;
    const n = objs.filter((o) => o.layer === layer).length + 1;
    if (schema[layer].props.some((p) => p[0] === 'name')) def.name = `${schema[layer].label} ${n}`;
    const o = { id: newId(layer), layer, geom: schema[layer].geom, source: 'eigen', pts: pts.map(round3),
      props: { ...def, ...props }, dirty: true };
    if (layer === 'fields') o.holes = [];
    if (layer === 'passages' && o.props.kind === 'oversteek') o.props.confirm = true;
    commit([...objs, o]);
    sel = [key(o)];
    return o;
  }

  function removeSelected() {
    if (!selObjs.length) return;
    let nextHidden = hidden;
    const gone = new Set();
    for (const o of selObjs) {
      if (o.source === 'eigen') { gone.add(o); continue; }
      nextHidden = [...nextHidden, { layer: o.layer, id: o.id }];         // bronobject: verbergen, niet weggooien
      if (o.dirty) gone.add(o);
    }
    commit(objs.filter((o) => !gone.has(o)), nextHidden);
    say(selObjs.some((o) => o.source !== 'eigen') ? 'Bronobject verborgen (terug te halen onder "verborgen").' : 'Verwijderd.');
    sel = []; selVertex = null;
  }

  function unhide(h) { commit(objs, hidden.filter((x) => x !== h)); }

  function deleteVertex() {
    const v = selVertex, o = objs.find((x) => key(x) === v?.k);
    if (!o) return false;
    const min = o.geom === 'polygon' ? 3 : 2;
    if (v.hole >= 0) {
      const h = o.holes[v.hole];
      const holes = h.pts.length <= 3 ? o.holes.filter((_, i) => i !== v.hole)
        : o.holes.map((x, i) => (i === v.hole ? { ...x, pts: x.pts.filter((_, j) => j !== v.i) } : x));
      commit(replaceObj(o, touch(o, { holes })));
    } else {
      if (o.pts.length <= min) { say(`Een ${schema[o.layer].label.toLowerCase()} heeft minstens ${min} punten.`, 'warn'); return true; }
      commit(replaceObj(o, touch(o, { pts: o.pts.filter((_, j) => j !== v.i) })));
    }
    selVertex = null;
    return true;
  }

  // ------------------------------------------------------------------ bewerkingen op velden (server rekent)
  const shape = (o) => ({ pts: o.pts, holes: o.holes ?? [] });

  async function op(body) {
    busy = 'rekenen...';
    try { return (await post('/api/editor/op', body)).polys; } catch (e) { say(String(e.message || e).replace(/^.*?"detail":"|"}$/g, ''), 'fout'); return null; } finally { busy = ''; }
  }

  async function splitField(line) {
    const o = one;
    if (!o || o.layer !== 'fields') return;
    const parts = await op({ op: 'split', polys: [shape(o)], line });
    if (!parts) return;
    const next = objs.filter((x) => x !== o);
    parts.forEach((p, i) => next.push(i === 0 ? touch(o, { pts: p.pts, holes: p.holes })
      : { ...o, id: newId('fields'), source: 'eigen', replaces: undefined, dirty: true, pts: p.pts, holes: p.holes,
        props: { ...o.props, name: `${o.props.name ?? 'veld'} ${i + 1}` } }));
    commit(next);
    say(`Veld gesplitst in ${parts.length} delen (${parts.map((p) => fmtArea(p.area_m2)).join(', ')}).`, 'ok');
  }

  async function mergeFields() {
    const fs = selObjs.filter((o) => o.layer === 'fields');
    if (fs.length < 2) { say('Kies twee of meer velden met shift-klik.', 'warn'); return; }
    const parts = await op({ op: 'merge', polys: fs.map(shape), gap_m: 1.5 });
    if (!parts) return;
    let nextHidden = hidden;
    const rest = new Set(fs.slice(1));
    for (const o of rest) if (o.source !== 'eigen') nextHidden = [...nextHidden, { layer: o.layer, id: o.id }];
    commit(objs.filter((o) => !rest.has(o)).map((o) => (o === fs[0] ? touch(o, { pts: parts[0].pts, holes: parts[0].holes }) : o)),
      nextHidden);
    sel = [key(fs[0])];
    say(`Samengevoegd tot ${fmtArea(parts[0].area_m2)}.`, 'ok');
  }

  async function offsetField() {
    const o = one;
    if (!o || o.geom !== 'polygon') return;
    const parts = await op({ op: 'offset', polys: [shape(o)], distance: Number(offsetM) });
    if (parts) commit(replaceObj(o, touch(o, { pts: parts[0].pts, holes: o.layer === 'fields' ? parts[0].holes : undefined })));
  }

  async function simplifyField() {
    const o = one;
    if (!o || o.geom !== 'polygon') return;
    const parts = await op({ op: 'simplify', polys: [shape(o)], tolerance: Number(simplifyM) });
    if (parts?.length) {
      say(`${o.pts.length} -> ${parts[0].pts.length} punten.`, 'ok');
      commit(replaceObj(o, touch(o, { pts: parts[0].pts, holes: o.layer === 'fields' ? parts[0].holes : undefined })));
    }
  }

  // ------------------------------------------------------------------ import, export, voorstellen, controle
  function addImported(list, report) {
    const used = new Set(objs.map(key));
    const fresh = list.map((o) => {
      let id = o.id || newId(o.layer);
      while (used.has(`${o.layer}:${id}`)) id = `${id}_2`;
      used.add(`${o.layer}:${id}`);
      return { ...o, id, source: 'eigen', dirty: true };
    });
    if (fresh.length) {
      commit([...objs, ...fresh]);
      sel = fresh.map(key);
      const b = bounds(fresh);
      if (b) zoomTo(b);
    }
    for (const r of report ?? []) say(r, r.startsWith('LET OP') ? 'warn' : 'info');
  }

  async function readFiles(fileList) {
    const want = /(^|\/)(field\.txt|boundary\.txt|flags\.txt)$|\.kml$|\.geojson$|\.json$/i;
    const files = {};
    for (const f of fileList) {
      const name = f.webkitRelativePath || f.name;
      if (!want.test(name) || /field\.kml$/i.test(name) && fileList.length > 1) continue;
      if (f.size > 30e6) continue;
      files[name] = await f.text();
    }
    return files;
  }

  async function doImport(e, asWorld = false) {
    const files = await readFiles(e.currentTarget.files);
    e.currentTarget.value = '';
    if (!Object.keys(files).length) { say('Geen bruikbare bestanden (AgOpenGPS-map met Field.txt, of .kml / .geojson).', 'warn'); return; }
    busy = 'inlezen...';
    try {
      if (asWorld) {
        const r = await post('/api/editor/import_world', { world: newWorld, label: newWorld.replaceAll('_', ' '), files,
          simplify_m: Number(importSimplify) });
        say(`Nieuwe wereld "${r.world}" met ${r.fields} velden. Kies hem in het hoofdmenu; met Bouw (tab Wereld) komen sloten, gebouwen en hoogte uit de kaart erbij.`, 'ok');
        onWorldCreated?.(r.world);
      } else {
        const r = await post('/api/editor/import', { files, simplify_m: Number(importSimplify) });
        addImported(r.objects, r.report);
      }
    } catch (err) { say(String(err.message || err), 'fout'); }
    busy = '';
  }

  async function copyFrom() {
    if (!copyWorld) return;
    busy = 'overnemen...';
    try { const r = await post('/api/editor/copy_from', { world: copyWorld }); addImported(r.objects, r.report); }
    catch (err) { say(String(err.message || err), 'fout'); }
    busy = '';
  }

  async function suggest() {
    if (dirty) { say('Bewaar eerst: de voorstellen gaan uit van de bewaarde wereld.', 'warn'); return; }
    busy = 'doorgangen zoeken...';
    try {
      const r = await post('/api/farm/suggest_passages', {});
      if (!r.passages.length) say('Geen nieuwe plekken gevonden waar twee gebieden zonder sloot of hek aan elkaar grenzen.');
      else {
        addImported(r.passages.map((p) => ({ id: newId('passages'), layer: 'passages', geom: 'line', pts: p.line,
          props: { name: p.name, kind: p.kind, width_m: p.width_m, direction: 'beide', max_speed_kmh: 2, confirm: false } })),
        [`${r.passages.length} doorgangen voorgesteld (geselecteerd). Kijk ze na, gooi weg wat niet klopt en bewaar.`]);
      }
    } catch (err) { say(String(err.message || err), 'fout'); }
    busy = '';
  }

  async function runCheck() {
    if (dirty) { say('Bewaar eerst: de controle gaat uit van de bewaarde wereld.', 'warn'); return; }
    busy = 'controleren...';
    try {
      const g = await get('/api/farm/graph');
      const linked = new Set(g.passages.filter((p) => p.usable).flatMap((p) => p.connects));
      const fieldRegions = g.regions.filter((r) => r.kind === 'veld');
      check = {
        unusable: g.passages.filter((p) => !p.usable),
        alone: fieldRegions.filter((r) => !linked.has(r.name)).map((r) => r.name),
        skipped: Object.entries(g.skipped_fields).map(([id, why]) => `${objs.find((o) => o.layer === 'fields' && o.id === id)?.props.name ?? id}: ${why}`),
        ok: g.passages.filter((p) => p.usable).length, regions: g.regions,
      };
      side = 'boerderij';
      redraw();
    } catch (err) { say(String(err.message || err), 'fout'); }
    busy = '';
  }

  async function toggleAerial() {
    aerialOn = !aerialOn;
    if (!aerialOn) { redraw(); return; }
    const a = st.area, xs = a.map((p) => p[0]), ys = a.map((p) => p[1]);
    const m = st.margin_m;
    busy = 'luchtfoto ophalen (PDOK)...';
    try {
      aerial = await loadAerialTiles(Math.min(...xs) - m, Math.min(...ys) - m, Math.max(...xs) + m, Math.max(...ys) + m,
        { onProgress: (k, n) => { busy = `luchtfoto ophalen (PDOK) ${k}/${n}...`; } });
    } catch (err) { say(`Luchtfoto: ${String(err.message || err)}`, 'fout'); aerialOn = false; }
    busy = '';
    redraw();
  }

  // ------------------------------------------------------------------ beeld
  const tx = (x) => size.w / 2 + (x - view.cx) * view.s;
  const ty = (y) => size.h / 2 - (y - view.cy) * view.s;
  const toWorld = (px, py) => [view.cx + (px - size.w / 2) / view.s, view.cy - (py - size.h / 2) / view.s];

  function zoomTo(b, pad = 1.25) {
    view.cx = (b.x0 + b.x1) / 2; view.cy = (b.y0 + b.y1) / 2;
    view.s = Math.min(size.w / ((b.x1 - b.x0 || 20) * pad), size.h / ((b.y1 - b.y0 || 20) * pad));
    view.s = Math.max(0.02, Math.min(200, view.s));
    updateScale();
    redraw();
  }

  function fit() {
    if (!st) return;
    const xs = st.area.map((p) => p[0]), ys = st.area.map((p) => p[1]);
    zoomTo({ x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) }, 1.08);
  }

  function redraw() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); }

  const visible = (o) => layerUi[o.layer]?.visible !== false && !hiddenSet.has(key(o));
  const usable = (o) => visible(o) && !layerUi[o.layer]?.locked;

  function path(ctx, pts, closed) {
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(tx(x), ty(y)) : ctx.moveTo(tx(x), ty(y))));
    if (closed) ctx.closePath();
  }

  function drawObj(ctx, o, selected) {
    const s = STYLE[o.layer] ?? {};
    ctx.setLineDash(s.dash ?? []);
    ctx.lineJoin = 'round';
    if (o.geom === 'polygon') {
      ctx.beginPath();
      path(ctx, o.pts, true);
      for (const h of o.holes ?? []) path(ctx, h.pts, true);
      let fill = s.fill;
      if (o.layer === 'surfaces') fill = css(color[o.props.kind] ?? color.grass, o.props.kind === 'water' ? 0.95 : 0.8);
      if (o.layer === 'zones') fill = o.props.klass === 'verboden' ? 'rgba(255,70,60,0.25)' : o.props.klass === 'werken' ? 'rgba(125,220,60,0.18)'
        : o.props.klass === 'niet_bewerken' ? 'rgba(181,140,255,0.38)' : s.fill;
      if (o.layer === 'no_go' && o.props.action === 'mijden') fill = 'rgba(255,178,62,0.25)';
      if (fill) { ctx.fillStyle = fill; ctx.fill('evenodd'); }
      ctx.strokeStyle = selected ? '#ffffff' : (o.layer === 'no_go' && o.props.action === 'mijden' ? '#ffb23e'
        : o.layer === 'zones' && o.props.klass === 'niet_bewerken' ? '#b58cff' : s.stroke);
      ctx.lineWidth = selected ? 2.4 : s.lw;
      ctx.stroke();
      for (const h of o.holes ?? []) {            // gat: doorrijdbaar = gestippeld
        ctx.beginPath(); path(ctx, h.pts, true);
        ctx.setLineDash(h.passable ? [4, 4] : []);
        ctx.strokeStyle = h.passable ? '#ffd76a' : '#ff6a5d'; ctx.lineWidth = 1.5; ctx.stroke();
      }
    } else if (o.geom === 'line') {
      const w = o.layer === 'passages' ? o.props.width_m ?? 4 : o.layer === 'ditches' ? o.props.width_m ?? 2
        : o.layer === 'dikes' ? (o.props.crest_width_m ?? 2) + 2 * (o.props.height_m ?? 1.5) * (o.props.slope ?? 2.5) : 0;
      if (w * view.s > 3) {                        // echte breedte als band
        ctx.beginPath(); path(ctx, o.pts, false);
        ctx.lineCap = 'butt';
        ctx.strokeStyle = o.layer === 'passages' ? 'rgba(185,227,74,0.35)' : o.layer === 'dikes' ? 'rgba(201,163,106,0.45)' : css(color.water, 0.75);
        ctx.lineWidth = w * view.s; ctx.setLineDash([]); ctx.stroke();
        ctx.setLineDash(s.dash ?? []);
      }
      ctx.beginPath(); path(ctx, o.pts, false);
      ctx.strokeStyle = selected ? '#ffffff' : s.stroke; ctx.lineWidth = selected ? 2.6 : s.lw; ctx.stroke();
      if (o.layer === 'passages' && o.pts.length > 1) {        // pijl: richting van de rijlijn
        const a = o.pts[o.pts.length - 2], b = o.pts[o.pts.length - 1], ang = Math.atan2(-(b[1] - a[1]), b[0] - a[0]);
        ctx.save(); ctx.translate(tx(b[0]), ty(b[1])); ctx.rotate(ang);
        ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(-9, -5); ctx.lineTo(-9, 5); ctx.closePath();
        ctx.fillStyle = o.props.direction === 'beide' ? 'rgba(185,227,74,0.6)' : '#b9e34a'; ctx.fill(); ctx.restore();
      }
    } else {
      const [x, y] = o.pts[0];
      const rM = o.layer === 'nests' ? o.props.radius_m ?? 5 : o.layer === 'markers' ? o.props.radius_m ?? 0.5 : s.r ?? 1;
      const r = Math.max(o.layer === 'trees' ? 2.2 : 4.5, rM * view.s);
      ctx.beginPath(); ctx.arc(tx(x), ty(y), r, 0, Math.PI * 2);
      ctx.fillStyle = s.fill; ctx.fill();
      ctx.strokeStyle = selected ? '#ffffff' : s.stroke; ctx.lineWidth = selected ? 2.4 : s.lw ?? 1; ctx.stroke();
      if (o.layer !== 'trees') { ctx.beginPath(); ctx.arc(tx(x), ty(y), 2.2, 0, Math.PI * 2); ctx.fillStyle = s.stroke; ctx.fill(); }
    }
    ctx.setLineDash([]);
  }

  // Slootzone: de verboden strook langs elke sloot en elk water (boerderij-instelling), als oranje band
  function drawDitchZone(ctx) {
    const z = Number(settings.safety?.ditch_zone_m ?? 2);
    if (!(z > 0)) return;
    ctx.save();
    ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.setLineDash([]);
    ctx.strokeStyle = 'rgba(255,122,40,0.42)';
    for (const o of objs) {
      if (hiddenSet.has(key(o))) continue;
      if (o.layer === 'ditches' && layerUi.ditches?.visible !== false) {
        ctx.beginPath(); path(ctx, o.pts, false);
        ctx.lineWidth = ((o.props.width_m ?? 2) + 2 * z) * view.s; ctx.stroke();
      } else if (o.layer === 'surfaces' && o.props.kind === 'water' && layerUi.surfaces?.visible !== false) {
        ctx.beginPath(); path(ctx, o.pts, true);
        ctx.lineWidth = 2 * z * view.s; ctx.stroke();
      }
    }
    ctx.restore();
  }

  function label(ctx, text, x, y, col = '#fff') {
    ctx.font = '600 12px "Barlow", "Segoe UI", Arial';
    ctx.textAlign = 'center';
    ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(8,12,14,0.85)'; ctx.strokeText(text, x, y);
    ctx.fillStyle = col; ctx.fillText(text, x, y);
  }

  function draw() {
    if (!canvas || !st) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(size.w * dpr) || canvas.height !== Math.round(size.h * dpr)) {
      canvas.width = Math.round(size.w * dpr); canvas.height = Math.round(size.h * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = css(color.grass.map((v) => v * 0.5));
    ctx.fillRect(0, 0, size.w, size.h);
    if (aerialOn && aerial) {
      ctx.globalAlpha = 0.95;
      for (const { img, b } of aerial) {
        ctx.drawImage(img, tx(b.x0), ty(b.y1), (b.x1 - b.x0) * view.s + 0.5, (b.y1 - b.y0) * view.s + 0.5);
      }
      ctx.globalAlpha = 1;
    }
    // wereldgebied
    ctx.beginPath(); path(ctx, st.area, true);
    ctx.setLineDash([8, 6]); ctx.strokeStyle = 'rgba(255,255,255,0.45)'; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
    if (snapOpt.grid && view.s * snapOpt.gridM >= 6) {
      const g = snapOpt.gridM, [x0, y1] = toWorld(0, 0), [x1, y0] = toWorld(size.w, size.h);
      ctx.beginPath();
      for (let x = Math.ceil(x0 / g) * g; x <= x1; x += g) { ctx.moveTo(tx(x), 0); ctx.lineTo(tx(x), size.h); }
      for (let y = Math.ceil(y0 / g) * g; y <= y1; y += g) { ctx.moveTo(0, ty(y)); ctx.lineTo(size.w, ty(y)); }
      ctx.strokeStyle = 'rgba(255,255,255,0.07)'; ctx.lineWidth = 1; ctx.stroke();
    }
    const selSet = new Set(sel);
    const faded = aerialOn && aerial;
    for (const layer of ORDER) {
      if (layer === 'zones') drawDitchZone(ctx);
      if (layerUi[layer]?.visible === false) continue;
      ctx.globalAlpha = faded && (layer === 'surfaces' || layer === 'fields') ? 0.35 : 1;
      for (const o of objs) if (o.layer === layer && !hiddenSet.has(key(o))) drawObj(ctx, o, selSet.has(key(o)));
    }
    ctx.globalAlpha = 1;
    if (showHidden) for (const h of hidden) if (h.obj) { ctx.globalAlpha = 0.35; drawObj(ctx, h.obj, false); ctx.globalAlpha = 1; }
    if (check?.regions && side === 'boerderij') {          // rijgebieden uit de controle
      for (const r of check.regions) for (const poly of r.poly) {
        ctx.beginPath(); for (const ring of poly) path(ctx, ring, true);
        ctx.fillStyle = r.kind === 'veld' ? 'rgba(185,227,74,0.10)' : 'rgba(79,179,255,0.22)'; ctx.fill('evenodd');
        ctx.strokeStyle = r.kind === 'veld' ? 'rgba(185,227,74,0.6)' : 'rgba(79,179,255,0.8)'; ctx.lineWidth = 1; ctx.stroke();
      }
    }
    // namen
    if (view.s > 0.25) for (const o of objs) {
      if (!visible(o) || !o.props.name) continue;
      if (o.layer === 'fields' && Math.sqrt(polyArea(o.pts)) * view.s > 70) {
        const c = o.pts.reduce((a, p) => [a[0] + p[0] / o.pts.length, a[1] + p[1] / o.pts.length], [0, 0]);
        label(ctx, o.props.name, tx(c[0]), ty(c[1]));
        label(ctx, fmtArea(polyArea(o.pts) - (o.holes ?? []).reduce((a, h) => a + polyArea(h.pts), 0)), tx(c[0]), ty(c[1]) + 15, '#c6d2d7');
      } else if (['passages', 'nests', 'markers', 'no_go', 'zones', 'dikes'].includes(o.layer) && view.s > 1.2) {
        const c = o.geom === 'point' ? o.pts[0] : o.pts.reduce((a, p) => [a[0] + p[0] / o.pts.length, a[1] + p[1] / o.pts.length], [0, 0]);
        label(ctx, o.props.name, tx(c[0]), ty(c[1]) - 9, '#eaf2c8');
      }
    }
    // startplek
    if (st.start) {
      const h = (st.start.heading * Math.PI) / 180;
      ctx.save(); ctx.translate(tx(st.start.x), ty(st.start.y)); ctx.rotate(h);
      ctx.beginPath(); ctx.moveTo(0, -10); ctx.lineTo(7, 7); ctx.lineTo(0, 3); ctx.lineTo(-7, 7); ctx.closePath();
      ctx.fillStyle = '#b9e34a'; ctx.fill(); ctx.strokeStyle = '#10180a'; ctx.lineWidth = 1.2; ctx.stroke(); ctx.restore();
    }
    // grepen van de selectie
    if (tool === 'select') for (const o of selObjs) {
      if (!schema[o.layer]?.edit) continue;
      const many = o.pts.length > 400 && view.s < 1.5;
      for (const [ring, hi] of rings(o)) {
        const closed = o.geom === 'polygon';
        if (!many && o.geom !== 'point') for (let i = 0; i < (closed ? ring.length : ring.length - 1); i++) {
          const a = ring[i], b = ring[(i + 1) % ring.length];
          if (dist(a, b) * view.s < 34) continue;
          ctx.beginPath(); ctx.arc(tx((a[0] + b[0]) / 2), ty((a[1] + b[1]) / 2), 3, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,255,255,0.55)'; ctx.fill();
        }
        ring.forEach((p, i) => {
          if (many && i % 4) return;
          const on = selVertex && selVertex.k === key(o) && selVertex.hole === hi && selVertex.i === i;
          ctx.beginPath(); ctx.rect(tx(p[0]) - (on ? 5 : 3.5), ty(p[1]) - (on ? 5 : 3.5), on ? 10 : 7, on ? 10 : 7);
          ctx.fillStyle = on ? '#ffb23e' : '#ffffff'; ctx.fill(); ctx.strokeStyle = '#10180a'; ctx.lineWidth = 1; ctx.stroke();
        });
      }
    }
    // wat je nu tekent
    if (draft.length || (cursor && tool !== 'select')) {
      const pts = cursor ? [...draft, cursor.p] : draft;
      const closed = (tool === 'draw' && schema[drawLayer]?.geom === 'polygon') || tool === 'hole';
      if (pts.length > 1) {
        ctx.beginPath(); path(ctx, pts, false);
        if (closed && pts.length > 2) ctx.closePath();
        ctx.strokeStyle = tool === 'measure' ? '#ffd76a' : tool === 'split' ? '#ff9f26' : '#ffffff';
        ctx.lineWidth = 1.8; ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);
      }
      for (const p of draft) { ctx.beginPath(); ctx.arc(tx(p[0]), ty(p[1]), 3.5, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill(); }
      if (pts.length > 1) {
        const a = pts[pts.length - 2], b = pts[pts.length - 1];
        const text = tool === 'measure' ? `${fmtLen(lineLength(pts))}${pts.length > 2 ? ` (laatste ${fmtLen(dist(a, b))})` : ''}`
          : closed && pts.length > 2 ? `${fmtLen(dist(a, b))} · ${fmtArea(polyArea(pts))}` : fmtLen(dist(a, b));
        label(ctx, text, tx(b[0]) + 14, ty(b[1]) - 14, '#ffd76a');
      }
    }
    if (cursor?.kind) {
      const [x, y] = cursor.p;
      ctx.beginPath(); ctx.arc(tx(x), ty(y), 7, 0, Math.PI * 2);
      ctx.strokeStyle = cursor.kind === 'punt' ? '#ffb23e' : cursor.kind === 'rand' ? '#4fb3ff' : 'rgba(255,255,255,0.6)';
      ctx.lineWidth = 2; ctx.stroke();
    }
  }

  // Schaalbalk
  let scaleBar = $state.raw(null);
  function updateScale() {
    const want = 90 / view.s;
    const m = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000].reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a));
    scaleBar = { m, px: m * view.s };
  }

  // ------------------------------------------------------------------ muis en toetsen
  const tol = () => 9 / view.s;
  const snapObjs = () => objs.filter(visible);

  function evPos(e) {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  function findHandle(p) {
    for (const o of selObjs) {
      if (!schema[o.layer]?.edit || layerUi[o.layer]?.locked) continue;
      for (const [ring, hi] of rings(o)) {
        for (let i = 0; i < ring.length; i++) if (dist(p, ring[i]) <= tol()) return { o, hole: hi, i, insert: false };
        if (o.geom === 'point') continue;
        const closed = o.geom === 'polygon';
        for (let i = 0; i < (closed ? ring.length : ring.length - 1); i++) {
          const a = ring[i], b = ring[(i + 1) % ring.length];
          if (dist(a, b) * view.s < 34) continue;
          if (dist(p, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]) <= tol()) return { o, hole: hi, i: i + 1, insert: true };
        }
      }
    }
    return null;
  }

  function withRing(o, hole, fn) {
    return hole >= 0 ? { holes: o.holes.map((h, i) => (i === hole ? { ...h, pts: fn(h.pts) } : h)) } : { pts: fn(o.pts) };
  }

  function pointerDown(e) {
    canvas.setPointerCapture(e.pointerId);
    const [px, py] = evPos(e), p = toWorld(px, py);
    if (e.button === 1 || e.button === 2 || spaceDown) { drag = { type: 'pan', px, py, cx: view.cx, cy: view.cy }; return; }
    if (tool === 'select') {
      const h = findHandle(p);
      if (h) {
        let o = h.o;
        if (h.insert) {                                  // punt invoegen op het midden van een rand en meteen verslepen
          const next = touch(o, withRing(o, h.hole, (r) => [...r.slice(0, h.i), round3(p), ...r.slice(h.i)]));
          commit(replaceObj(o, next)); o = next;
        } else undo.push(snapshot());
        selVertex = { k: key(o), hole: h.hole, i: h.i };
        drag = { type: 'vertex', k: key(o), hole: h.hole, i: h.i, moved: h.insert, pushed: !h.insert };
        redraw();
        return;
      }
      const o = hitTest(p, objs, tol() * 0.7, usable);
      selVertex = null;
      if (o) sel = e.shiftKey ? (sel.includes(key(o)) ? sel.filter((k) => k !== key(o)) : [...sel, key(o)]) : [key(o)];
      else if (!e.shiftKey) sel = [];
      drag = { type: 'pan', px, py, cx: view.cx, cy: view.cy };
      redraw();
      return;
    }
    const sp = snap(p, snapObjs(), tol(), e.altKey ? {} : snapOpt).p;
    if (tool === 'draw' && schema[drawLayer].geom === 'point') { addObject(drawLayer, [sp]); return; }
    if (draft.length && dist(draft[draft.length - 1], sp) * view.s < 3) return;      // dubbelklik: geen dubbel punt
    draft = [...draft, round3(sp)];
    redraw();
  }

  function pointerMove(e) {
    const [px, py] = evPos(e), p = toWorld(px, py);
    if (drag?.type === 'pan') {
      view.cx = drag.cx - (px - drag.px) / view.s; view.cy = drag.cy + (py - drag.py) / view.s;
      redraw(); return;
    }
    if (drag?.type === 'vertex') {
      const o = objs.find((x) => key(x) === drag.k);
      if (!o) return;
      const sp = snap(p, snapObjs(), tol(), e.altKey ? {} : snapOpt, { o, hole: drag.hole, i: drag.i });
      cursor = sp;
      objs = replaceObj(o, touch(o, withRing(o, drag.hole, (r) => r.map((q, i) => (i === drag.i ? round3(sp.p) : q)))));
      drag.moved = true; dirty = true;
      redraw(); return;
    }
    cursor = tool === 'select' ? { p, kind: '' } : snap(p, snapObjs(), tol(), e.altKey ? {} : snapOpt);
    redraw();
  }

  function pointerUp() {
    if (drag?.type === 'vertex') {
      if (drag.pushed && !drag.moved) undo.pop();
      else { redo = []; canUndo = true; canRedo = false; }
    }
    drag = null;
  }

  function wheel(e) {
    e.preventDefault();
    const [px, py] = evPos(e), before = toWorld(px, py);
    view.s = Math.max(0.02, Math.min(300, view.s * Math.exp(-e.deltaY * 0.0014)));
    const after = toWorld(px, py);
    view.cx += before[0] - after[0]; view.cy += before[1] - after[1];
    updateScale(); redraw();
  }

  function finish() {
    const pts = draft;
    draft = [];
    if (tool === 'draw') {
      const g = schema[drawLayer].geom, need = g === 'polygon' ? 3 : 2;
      if (pts.length >= need) addObject(drawLayer, pts);
      else if (pts.length) say(`Minstens ${need} punten nodig.`, 'warn');
    } else if (tool === 'hole') {
      const o = one;
      if (o?.layer === 'fields' && pts.length >= 3) {
        commit(replaceObj(o, touch(o, { holes: [...(o.holes ?? []), { pts, passable: false, name: `gat ${(o.holes?.length ?? 0) + 1}` }] })));
        tool = 'select';
      }
    } else if (tool === 'split') {
      if (pts.length >= 2) splitField(pts);
      tool = 'select';
    }
    redraw();
  }

  function setTool(t, layer = null) {
    if ((t === 'hole' || t === 'split') && one?.layer !== 'fields') { say('Kies eerst één veld.', 'warn'); return; }
    tool = t; draft = [];
    if (layer) { drawLayer = layer; layerUi[layer].visible = true; layerUi[layer].locked = false; }
    redraw();
  }

  function onKeyDown(e) {
    if (e.key === ' ') spaceDown = true;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'y') { e.preventDefault(); doRedo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === 's') { e.preventDefault(); if (dirty) save(); return; }
    if (k === 'escape') {
      e.stopPropagation();
      if (draft.length) draft = []; else if (tool !== 'select') tool = 'select'; else if (sel.length) { sel = []; selVertex = null; }
      redraw(); return;
    }
    if (k === 'enter') { finish(); return; }
    if (k === 'backspace' && draft.length) { draft = draft.slice(0, -1); redraw(); return; }
    if (k === 'delete' || k === 'backspace') { if (!(selVertex && deleteVertex())) removeSelected(); return; }
    if (k === 'f') fit();
    if (k === 'm') setTool('measure');
    if (k === 'v') setTool('select');
  }

  onMount(() => {
    const ro = new ResizeObserver(() => { size = { w: wrap.clientWidth, h: wrap.clientHeight }; updateScale(); redraw(); });
    ro.observe(wrap);
    size = { w: wrap.clientWidth, h: wrap.clientHeight };
    load().then(updateScale);
    const kd = (e) => onKeyDown(e), ku = (e) => { if (e.key === ' ') spaceDown = false; };
    window.addEventListener('keydown', kd, true);
    window.addEventListener('keyup', ku);
    return () => { ro.disconnect(); window.removeEventListener('keydown', kd, true); window.removeEventListener('keyup', ku); };
  });

  $effect(() => { layerUi; showHidden; side; JSON.stringify(layerUi); redraw(); });

  function close() {
    if (dirty && !confirm('Er zijn wijzigingen die nog niet bewaard zijn. Toch sluiten?')) return;
    onClose?.();
  }

  const pos = $derived(cursor && st ? { xy: cursor.p, ll: approxLatLon(st.origin, cursor.p[0], cursor.p[1]) } : null);
  const drawables = $derived(Object.entries(schema).filter(([, s]) => s.edit));
  const toolHint = $derived(tool === 'draw' ? `${schema[drawLayer]?.label} tekenen: klik voor punten${schema[drawLayer]?.geom === 'point' ? '' : ', dubbelklik of Enter = klaar'}, Esc = stoppen`
    : tool === 'hole' ? 'Gat in het veld: klik de hoekpunten, dubbelklik = klaar'
      : tool === 'split' ? 'Veld splitsen: teken een lijn van rand tot rand, dubbelklik = klaar'
        : tool === 'measure' ? 'Meetlat: klik punten, Esc = wissen'
          : 'Klik = kiezen (shift = meer) · sleep een punt · klik op een rand-bolletje = punt erbij · Delete = weg');
</script>

<div class="editor">
  <div class="bar top">
    <button class="ghost" onclick={close} title="Terug naar het hoofdmenu"><Icon name="back" size={18} /></button>
    <div class="ttl"><span>Wereld bewerken</span><b>{st?.label ?? '...'}</b></div>
    <span class="sep"></span>
    <button class:active={tool === 'select'} onclick={() => setTool('select')} title="Kiezen en verslepen (V)">Kiezen</button>
    <select value={tool === 'draw' ? drawLayer : ''} onchange={(e) => e.currentTarget.value && setTool('draw', e.currentTarget.value)}
      class:active={tool === 'draw'} title="Nieuw object tekenen">
      <option value="">+ Tekenen...</option>
      {#each drawables as [l, s]}<option value={l}>{s.label}</option>{/each}
    </select>
    <button class:active={tool === 'hole'} onclick={() => setTool('hole')} title="Gat in het gekozen veld tekenen">Gat</button>
    <button class:active={tool === 'split'} onclick={() => setTool('split')} title="Gekozen veld splitsen met een lijn">Splitsen</button>
    <button onclick={mergeFields} disabled={selObjs.filter((o) => o.layer === 'fields').length < 2} title="Gekozen velden samenvoegen">Samenvoegen</button>
    <button class:active={tool === 'measure'} onclick={() => setTool('measure')} title="Meetlat (M)">Meten</button>
    <span class="sep"></span>
    <button onclick={doUndo} disabled={!canUndo} title="Ongedaan maken (Ctrl+Z)">↶</button>
    <button onclick={doRedo} disabled={!canRedo} title="Opnieuw (Ctrl+Y)">↷</button>
    <span class="sep"></span>
    <label class="chk" title="Snappen aan hoekpunten"><input type="checkbox" bind:checked={snapOpt.vertex} />punt</label>
    <label class="chk" title="Snappen aan randen"><input type="checkbox" bind:checked={snapOpt.edge} />rand</label>
    <label class="chk" title="Snappen aan het raster"><input type="checkbox" bind:checked={snapOpt.grid} onchange={redraw} />raster
      <input class="mini" type="number" min="0.1" max="50" step="0.1" bind:value={snapOpt.gridM} onchange={redraw} /></label>
    <button class:active={aerialOn} onclick={toggleAerial} title="Luchtfoto van PDOK als achtergrond">Luchtfoto</button>
    <span class="grow"></span>
    <button onclick={suggest} title="Doorgangen voorstellen: plekken waar twee gebieden zonder sloot of hek aan elkaar grenzen">Voorstel doorgangen</button>
    <button onclick={runCheck} title="Welke velden zijn bereikbaar, welke doorgangen kloppen niet">Controleer</button>
    <button class="primary" onclick={save} disabled={!dirty || !!busy} title="Bewaren in farm.json (Ctrl+S)">Bewaren{dirty ? ' •' : ''}</button>
  </div>

  <div class="left panel">
    <div class="sec">Lagen</div>
    {#each Object.entries(schema) as [l, s]}
      <div class="layer" class:off={layerUi[l]?.visible === false}>
        <button class="ghost eye" class:on={layerUi[l]?.visible !== false} onclick={() => (layerUi[l].visible = !layerUi[l].visible)}
          title="Zichtbaar"><Icon name="eye" size={15} /></button>
        <button class="ghost lock" class:on={layerUi[l]?.locked} onclick={() => (layerUi[l].locked = !layerUi[l].locked)}
          title={layerUi[l]?.locked ? 'Vergrendeld: niet te kiezen' : 'Vergrendelen'}>{layerUi[l]?.locked ? '🔒' : '·'}</button>
        <span class="lname"><i style="background:{STYLE[l]?.stroke ?? '#888'}"></i>{s.plural}</span>
        <span class="muted num">{counts[l] ?? 0}</span>
        {#if s.edit}<button class="ghost add" onclick={() => setTool('draw', l)} title="{s.label} tekenen">+</button>{:else}<span class="add"></span>{/if}
      </div>
    {/each}
    {#if hidden.length}
      <div class="sec" style="margin-top:10px">Verborgen <span class="muted">({hidden.length})</span></div>
      <label class="chk"><input type="checkbox" bind:checked={showHidden} />vaag tonen</label>
      <div class="hid">
        {#each hidden as h}
          <div class="row small"><span class="grow">{h.obj?.props?.name || h.id}</span>
            <button class="ghost" onclick={() => unhide(h)} title="Weer tonen">terug</button></div>
        {/each}
      </div>
    {/if}
    <div class="legend tiny muted">
      <div><i class="sw zone"></i>slootzone (stop)</div>
      <div><i class="sw skip"></i>niet bewerken</div>
      <div><i class="b bron"></i>uit de kaart (bron)</div>
      <div><i class="b eigen"></i>eigen · <i class="b over"></i>overschreven</div>
    </div>
  </div>

  <div class="canvas" bind:this={wrap}>
    <canvas bind:this={canvas} style="cursor:{tool === 'select' ? 'default' : 'crosshair'}"
      onpointerdown={pointerDown} onpointermove={pointerMove} onpointerup={pointerUp} onwheel={wheel}
      ondblclick={() => tool !== 'select' && tool !== 'measure' && finish()} oncontextmenu={(e) => e.preventDefault()}></canvas>
    <div class="north">N</div>
    {#if scaleBar}<div class="scale"><span style="width:{scaleBar.px}px"></span>{scaleBar.m} m</div>{/if}
    <div class="hint">{toolHint}</div>
    {#if busy}<div class="busy"><span class="spin"></span>{busy}</div>{/if}
  </div>

  <div class="right panel">
    <div class="tabs">
      {#each [['eigenschappen', 'Object'], ['boerderij', 'Boerderij'], ['import', 'Import / export']] as [k, t]}
        <button class:active={side === k} onclick={() => (side = k)}>{t}</button>
      {/each}
    </div>
    <div class="body">
      {#if side === 'eigenschappen'}
        {#if one}
          {@const s = schema[one.layer]}
          <div class="row"><b class="grow">{s.label}</b>
            <span class="badge {one.source === 'eigen' ? 'eigen' : one.dirty ? 'over' : 'bron'}">{one.source === 'eigen' ? 'eigen' : one.dirty ? 'overschreven' : 'bron'}</span></div>
          <div class="tiny muted">{one.id}</div>
          {#each s.props as [k, lab, typ, def, choices]}
            <label class="prop">{lab}
              {#if typ === 'select'}
                <select value={one.props[k] ?? def} disabled={!s.edit} onchange={(e) => setProp(one, k, e.currentTarget.value, typ)}>
                  {#each choices as c}<option value={c}>{c.replaceAll('_', ' ')}</option>{/each}
                </select>
              {:else if typ === 'bool'}
                <input type="checkbox" checked={!!(one.props[k] ?? def)} disabled={!s.edit} onchange={(e) => setProp(one, k, e.currentTarget.checked, typ)} />
              {:else}
                <input type={typ === 'text' ? 'text' : 'number'} step={typ === 'int' ? 1 : 0.1} value={one.props[k] ?? def ?? ''} disabled={!s.edit}
                  placeholder={def === null ? 'instelling van de missie' : ''} onchange={(e) => setProp(one, k, e.currentTarget.value, typ)} />
              {/if}
            </label>
          {/each}
          <div class="facts small num">
            {#if one.geom === 'polygon'}
              <span>{fmtArea(polyArea(one.pts) - (one.holes ?? []).reduce((a, h) => a + polyArea(h.pts), 0))}</span>
              <span>omtrek {fmtLen(lineLength(one.pts, true))}</span><span>{one.pts.length} punten</span>
            {:else if one.geom === 'line'}
              <span>lengte {fmtLen(lineLength(one.pts))}</span><span>{one.pts.length} punten</span>
            {:else}<span>x {one.pts[0][0].toFixed(2)} · y {one.pts[0][1].toFixed(2)}</span>{/if}
          </div>
          {#if one.layer === 'fields'}
            {#if one.holes?.length}
              <div class="sec">Gaten</div>
              {#each one.holes as h, i}
                <div class="row small">
                  <span class="grow">{h.name || `gat ${i + 1}`} · {fmtArea(polyArea(h.pts))}</span>
                  <label class="chk" title="De robot mag erdoor rijden, maar werkt er niet"><input type="checkbox" checked={h.passable}
                    onchange={(e) => commit(replaceObj(one, touch(one, { holes: one.holes.map((x, j) => (j === i ? { ...x, passable: e.currentTarget.checked } : x)) })))} />doorrijdbaar</label>
                  <button class="ghost" onclick={() => commit(replaceObj(one, touch(one, { holes: one.holes.filter((_, j) => j !== i) })))}>×</button>
                </div>
              {/each}
            {/if}
            <div class="sec">Rand</div>
            <div class="row"><input class="mini" type="number" step="0.1" bind:value={offsetM} />
              <button onclick={offsetField} title="Negatief = naar binnen, positief = naar buiten">m verschuiven</button></div>
            <div class="row"><input class="mini" type="number" step="0.05" min="0.01" bind:value={simplifyM} />
              <button onclick={simplifyField} title="Punten op een rechte lijn weghalen">m vereenvoudigen</button></div>
            <div class="row"><button onclick={() => setTool('hole')}>Gat tekenen</button><button onclick={() => setTool('split')}>Splitsen</button></div>
            <div class="row"><a class="btn" href="/api/editor/export?format=agopengps&field={one.id}" download
              title="Bewaarde versie van dit veld als AgOpenGPS-map (zip)">naar AgOpenGPS</a></div>
          {/if}
          {#if one.layer === 'passages'}
            <div class="row"><button onclick={() => commit(replaceObj(one, touch(one, { pts: [...one.pts].reverse() })))}>Richting omdraaien</button></div>
            <div class="tiny muted">Teken de rijlijn van binnen het ene veld (of het erf) tot binnen het andere. De robot gaat
              alleen hier over een sloot, dijk, hek of weg.</div>
          {/if}
          {#if one.layer === 'zones'}
            <div class="tiny muted">niet bewerken = dit stuk van het veld slaat de missie over (een punt, een uitstulping); de
              robot mag er wel rijden en keren. alleen rijden = transport, werktuig omhoog. verboden = robot stopt.</div>
          {/if}
          {#if one.layer === 'dikes'}
            <div class="tiny muted">ontwijken = eromheen · negeren = telt niet mee · doorgang = alleen over bij een doorgang.</div>
          {/if}
          <div class="row" style="margin-top:10px">
            <button class="danger" onclick={removeSelected}>{one.source === 'eigen' ? 'Verwijderen' : 'Verbergen'}</button>
            <button onclick={() => zoomTo(bounds([one]), 1.6)}>Inzoomen</button>
          </div>
        {:else if selObjs.length > 1}
          <b>{selObjs.length} objecten</b>
          <div class="row" style="margin-top:8px"><button onclick={mergeFields}>Velden samenvoegen</button>
            <button class="danger" onclick={removeSelected}>Verwijderen / verbergen</button></div>
        {:else}
          <div class="muted small">Klik een object aan, of kies bij Lagen de + om er een te tekenen.</div>
          <div class="keys tiny muted">
            <div><kbd>scroll</kbd> zoomen · <kbd>rechtermuis</kbd> of <kbd>spatie</kbd>+sleep = schuiven</div>
            <div><kbd>Alt</kbd> = even niet snappen · <kbd>F</kbd> = alles in beeld</div>
            <div><kbd>Ctrl+Z</kbd> / <kbd>Ctrl+Y</kbd> · <kbd>Ctrl+S</kbd> bewaren</div>
          </div>
        {/if}
      {:else if side === 'boerderij'}
        <div class="sec">Dijken</div>
        <label class="prop">voor alle dijken
          <select bind:value={settings.dike_mode} onchange={() => (dirty = true)}>
            <option value={null}>per dijk (zoals ingesteld)</option>
            <option value="ontwijken">ontwijken</option><option value="negeren">niet meenemen</option>
            <option value="doorgang">alleen over bij een doorgang</option>
          </select>
        </label>
        <div class="sec">Vrije ruimte (m vanaf het obstakel)</div>
        <div class="grid2">
          {#each clearanceKinds as [k, lab]}
            <label class="prop">{lab}<input type="number" min="0" max="10" step="0.1" value={settings.clearance?.[k] ?? clearanceDefault[k]}
              onchange={(e) => { settings.clearance[k] = Number(e.currentTarget.value); dirty = true; }} /></label>
          {/each}
        </div>
        <div class="sec">Veiligheid in de sim</div>
        <label class="chk"><input type="checkbox" checked={settings.safety?.pause !== false} onchange={(e) => { settings.safety.pause = e.currentTarget.checked; dirty = true; }} />pauzeren bij een verboden gebied of sloot</label>
        <label class="chk"><input type="checkbox" checked={settings.safety?.stop_robot !== false} onchange={(e) => { settings.safety.stop_robot = e.currentTarget.checked; dirty = true; }} />ook de sim-robot stoppen</label>
        <label class="prop"><span><i class="sw zone"></i>slootzone: verboden strook langs sloot en water (m)</span>
          <input type="number" min="0" max="10" step="0.25" value={settings.safety?.ditch_zone_m ?? 2}
            onchange={(e) => { settings.safety.ditch_zone_m = Number(e.currentTarget.value); dirty = true; redraw(); }} /></label>
        <label class="prop">de planner blijft daar nog zoveel buiten (m)<input type="number" min="0" max="2" step="0.05" value={settings.safety?.plan_margin_m ?? 0.3}
          onchange={(e) => { settings.safety.plan_margin_m = Number(e.currentTarget.value); dirty = true; }} /></label>
        <div class="tiny muted">Komt robot of werktuig in de oranje zone, dan stopt hij. Missies worden er vanzelf buiten gepland.</div>
        {#if check}
          <div class="sec">Controle</div>
          <div class="small">{check.ok} doorgangen bruikbaar · blauw = rijgebied, groen = waar de robot in het veld mag komen</div>
          {#each check.unusable as p}<div class="small bad">doorgang {p.name || p.id}: {p.reason}</div>{/each}
          {#each check.alone as n}<div class="small warnc">{n}: geen doorgang, niet bereikbaar vanaf een ander gebied</div>{/each}
          {#each check.skipped as n}<div class="small bad">{n}</div>{/each}
          {#if !check.unusable.length && !check.alone.length && !check.skipped.length}<div class="small okc">Alle velden hebben een bruikbare doorgang.</div>{/if}
        {/if}
        {#if st?.backups?.length}
          <div class="sec">Back-ups</div>
          {#each st.backups.slice(0, 6) as b}
            <div class="row small"><span class="grow num">{b.replace('farm_', '').replace('.json', '')}</span>
              <button class="ghost" onclick={() => restore(b)}>terugzetten</button></div>
          {/each}
        {/if}
      {:else}
        <div class="sec">Inlezen</div>
        <label class="prop">grens vereenvoudigen (m, 0 = alle punten)<input type="number" min="0" max="1" step="0.01" bind:value={importSimplify} /></label>
        <label class="file">AgOpenGPS-veldmap (of de map met al je velden)
          <input type="file" webkitdirectory multiple onchange={(e) => doImport(e)} /></label>
        <label class="file">KML (Google Earth) of GeoJSON
          <input type="file" accept=".kml,.geojson,.json" multiple onchange={(e) => doImport(e)} /></label>
        <div class="tiny muted">Wat je inleest komt in de editor te staan en is pas definitief na Bewaren. Vlaggen worden
          markers; "nest" of "boom" in de notitie maakt er een nest of boom van.</div>
        <div class="sec">Als nieuwe wereld (tweede boerderij)</div>
        <label class="prop">naam (kleine letters, cijfers, _)<input type="text" bind:value={newWorld} placeholder="bv. boerderij_oost" /></label>
        <label class="file" class:off={!/^[a-z0-9_]{2,40}$/.test(newWorld)}>AgOpenGPS-map(pen), KML of GeoJSON
          <input type="file" webkitdirectory multiple disabled={!/^[a-z0-9_]{2,40}$/.test(newWorld)} onchange={(e) => doImport(e, true)} /></label>
        <div class="sec">Overnemen uit een andere wereld</div>
        <div class="row"><select class="grow" bind:value={copyWorld}><option value="">kies een wereld</option>
          {#each worlds.filter((w) => w.name !== st?.world) as w}<option value={w.name}>{w.label}</option>{/each}</select>
          <button onclick={copyFrom} disabled={!copyWorld}>Neem over</button></div>
        <div class="sec">Uitvoeren (bewaarde versie)</div>
        <div class="row wrapr">
          <a class="btn" href="/api/editor/export?format=agopengps" download>AgOpenGPS (alle velden)</a>
          <a class="btn" href="/api/editor/export?format=kml" download>KML</a>
          <a class="btn" href="/api/editor/export?format=geojson" download>GeoJSON (alles)</a>
          <a class="btn" href="/api/editor/export?format=geojson&own=true" download>GeoJSON (eigen)</a>
        </div>
        <div class="tiny muted">AgOpenGPS kent alleen grens, gaten en vlaggen: sloten, dijken, verboden gebieden en nesten
          in een veld gaan mee als gat.</div>
      {/if}
    </div>
    {#if messages.length}
      <div class="log">
        {#each messages as m (m.t + m.text)}<div class="small {m.kind}">{m.text}</div>{/each}
      </div>
    {/if}
  </div>

  <div class="bar bottom num">
    {#if pos}<span>x {pos.xy[0].toFixed(2)} · y {pos.xy[1].toFixed(2)} m</span>
      <span class="muted">{pos.ll[0].toFixed(6)}, {pos.ll[1].toFixed(6)}</span>{/if}
    {#if cursor?.kind}<span class="snapk">snap: {cursor.kind}</span>{/if}
    <span class="grow"></span>
    <span class="muted">{objs.length} objecten · {objs.filter((o) => o.source === 'eigen').length} eigen · {objs.filter((o) => o.source !== 'eigen' && o.dirty).length} overschreven · {hidden.length} verborgen</span>
    <span class:dirty>{dirty ? 'niet bewaard' : 'bewaard'}</span>
  </div>
</div>

<style>
  .editor { position: absolute; inset: 0; z-index: 30; background: #0b1114; display: grid;
    grid-template: 48px 1fr 28px / 232px 1fr 318px; animation: fade 0.2s; }
  .bar { grid-column: 1 / -1; display: flex; align-items: center; gap: 6px; padding: 0 10px; background: var(--panel-solid);
    border-bottom: 1px solid var(--line); }
  .bar.bottom { border-top: 1px solid var(--line); border-bottom: 0; font-size: 12px; gap: 14px; }
  .bar.top { overflow-x: auto; overflow-y: hidden; }
  .bar button, .bar select, .bar .chk { white-space: nowrap; flex: none; }
  .bar .sep { flex: none; width: 1px; height: 22px; background: var(--line-strong); margin: 0 4px; }
  .bar select.active { border-color: var(--accent); color: var(--accent); }
  .ttl { display: flex; flex-direction: column; line-height: 1.05; margin-right: 4px; }
  .ttl span { font-family: var(--display); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); }
  .ttl b { font-family: var(--display); font-size: 17px; font-weight: 600; letter-spacing: 0.03em; text-transform: uppercase; white-space: nowrap; }
  .ghost { background: transparent; border-color: transparent; padding: 3px 6px; min-height: 24px; display: inline-flex; align-items: center; }
  .chk { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; color: var(--ink); white-space: nowrap; }
  .mini { width: 62px; min-height: 26px !important; padding: 2px 6px !important; }
  .left, .right { position: static; border-radius: 0; border-width: 0 1px 0 0; box-shadow: none; overflow: auto; padding: 10px; }
  .right { border-width: 0 0 0 1px; padding: 0; display: flex; flex-direction: column; }
  .layer { display: grid; grid-template-columns: 26px 24px 1fr auto 24px; align-items: center; gap: 2px; font-size: 13px; min-height: 27px; }
  .layer.off .lname { opacity: 0.4; }
  .layer .eye { color: var(--faint); } .layer .eye.on { color: var(--ink); }
  .layer .lock { color: var(--faint); font-size: 11px; justify-content: center; } .layer .lock.on { color: var(--amber); }
  .layer .add { color: var(--accent); font-size: 17px; justify-content: center; width: 24px; }
  .lname { display: flex; align-items: center; gap: 7px; white-space: nowrap; overflow: hidden; }
  .lname i { width: 10px; height: 10px; border-radius: 2px; flex: none; }
  .hid { max-height: 150px; overflow: auto; margin-top: 4px; }
  .legend { margin-top: 14px; display: grid; gap: 3px; }
  .b { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
  .b.bron, .badge.bron { background: #6f8089; } .b.eigen, .badge.eigen { background: var(--accent); } .b.over, .badge.over { background: var(--amber); }
  .sw { display: inline-block; width: 12px; height: 9px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }
  .sw.zone { background: rgba(255, 122, 40, 0.75); } .sw.skip { background: rgba(181, 140, 255, 0.8); }
  .badge { font-size: 11px; padding: 1px 7px; border-radius: 9px; color: #10180a; font-weight: 600; }
  .canvas { position: relative; overflow: hidden; min-width: 0; min-height: 0; }
  canvas { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; }
  .north { position: absolute; top: 10px; right: 12px; width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center;
    font-family: var(--display); font-weight: 700; color: var(--accent); background: rgba(8, 12, 14, 0.75); pointer-events: none; }
  .scale { position: absolute; left: 12px; bottom: 10px; display: flex; align-items: center; gap: 7px; font-size: 12px; color: #fff;
    text-shadow: 0 1px 2px #000; pointer-events: none; }
  .scale span { height: 5px; border: 1px solid #fff; border-top: 0; }
  .hint { position: absolute; left: 50%; bottom: 10px; transform: translateX(-50%); font-size: 12px; padding: 4px 12px; border-radius: 12px;
    background: rgba(8, 12, 14, 0.78); color: var(--muted); pointer-events: none; white-space: nowrap; max-width: 92%; overflow: hidden; text-overflow: ellipsis; }
  .busy { position: absolute; left: 50%; top: 14px; transform: translateX(-50%); display: flex; gap: 10px; align-items: center; padding: 7px 16px;
    border-radius: 18px; background: var(--panel-solid); border: 1px solid var(--line-strong); font-size: 13px; }
  .spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--track); border-top-color: var(--accent); animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .tabs { display: flex; border-bottom: 1px solid var(--line); }
  .tabs button { flex: 1; border: 0; border-radius: 0; background: transparent; color: var(--muted); font-family: var(--display); font-size: 14px;
    letter-spacing: 0.06em; text-transform: uppercase; min-height: 36px; }
  .tabs button.active { color: var(--accent); box-shadow: inset 0 -2px 0 var(--accent); background: var(--accent-soft); }
  .body { padding: 12px; overflow: auto; flex: 1; display: flex; flex-direction: column; gap: 6px; }
  .prop { display: flex; flex-direction: column; gap: 2px; font-size: 12px; }
  .prop input[type='checkbox'] { align-self: flex-start; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 5px 8px; }
  .facts { display: flex; flex-wrap: wrap; gap: 3px 12px; color: var(--muted); margin: 4px 0; }
  .sec { margin-top: 8px; }
  .keys { display: grid; gap: 5px; margin-top: 12px; }
  kbd { font-family: var(--display); font-size: 12px; padding: 0 5px; border: 1px solid var(--line-strong); border-bottom-width: 2px; border-radius: 4px;
    color: var(--ink); background: rgba(255, 255, 255, 0.07); }
  .file { display: flex; flex-direction: column; gap: 3px; font-size: 12px; padding: 7px 8px; border: 1px dashed var(--line-strong); border-radius: 5px; }
  .file.off { opacity: 0.45; }
  .btn { display: inline-block; padding: 4px 10px; border: 1px solid var(--line-strong); border-radius: 4px; color: inherit; text-decoration: none; font-size: 12.5px; }
  .btn:hover { background: rgba(255, 255, 255, 0.1); }
  .wrapr { flex-wrap: wrap; }
  .log { border-top: 1px solid var(--line); padding: 8px 12px; max-height: 170px; overflow: auto; display: grid; gap: 4px; user-select: text; }
  .log .fout, .bad { color: #ff9a90; } .log .warn, .warnc { color: var(--amber); } .log .ok, .okc { color: var(--green); }
  .snapk { color: var(--amber); }
  .dirty { color: var(--amber); font-weight: 600; }
</style>
