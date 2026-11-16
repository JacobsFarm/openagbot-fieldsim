// Praten met de sim (zelfde origin) en met de OpenAgbot-backend (eigen poort, CORS staat open).

export async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function post(path, body = {}) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function put(path, body = {}) {
  const r = await fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  if (!r.ok) {
    const t = await r.text();
    let msg = t;
    try { msg = JSON.parse(t).detail ?? t; } catch { /* geen json */ }
    throw new Error(msg);
  }
  return r.json();
}

export async function del(path) {
  const r = await fetch(path, { method: 'DELETE' });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

// Foutmelding van de sim-API zonder het pad ervoor: "/api/x: 502 {"detail":"..."}" -> "..."
export function errorText(e) {
  const t = String(e?.message || e);
  const m = t.match(/\{.*\}$/s);
  try { return m ? JSON.parse(m[0]).detail ?? t : t; } catch { return t; }
}

export async function getTerrain(cell = 2) {
  const r = await fetch(`/api/world/terrain?cell=${cell}`);
  const header = JSON.parse(r.headers.get('X-Terrain'));
  return { header, heights: new Float32Array(await r.arrayBuffer()) };
}

// Live toestand over een WebSocket; verbindt zelf opnieuw als de sim herstart.
export function connectState(onState, onStatus) {
  let ws = null;
  let closed = false;
  const open = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const sock = ws = new WebSocket(`${proto}://${location.host}/ws`);
    sock.onopen = () => onStatus?.(true);
    sock.onmessage = (e) => { if (sock === ws) onState(JSON.parse(e.data)); };
    sock.onclose = () => {
      if (sock !== ws) return;                 // vervangen door restart(): geen melding, geen tweede verbinding
      onStatus?.(false);
      if (!closed) setTimeout(open, 1000);
    };
  };
  open();
  const stop = () => { closed = true; ws?.close(); };
  // Nieuwe verbinding: de server stuurt dan alle sporen opnieuw vanaf het begin (na het laden van een wereld)
  stop.restart = () => { const old = ws; open(); old?.close(); };
  return stop;
}

export async function agbot(url, path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) };
  const r = await fetch(url + path, { ...opts, signal: AbortSignal.timeout(2500) });
  return r.json();
}
