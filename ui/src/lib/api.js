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
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => onStatus?.(true);
    ws.onmessage = (e) => onState(JSON.parse(e.data));
    ws.onclose = () => {
      onStatus?.(false);
      if (!closed) setTimeout(open, 1000);
    };
  };
  open();
  return () => { closed = true; ws?.close(); };
}

export async function agbot(url, path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) };
  const r = await fetch(url + path, { ...opts, signal: AbortSignal.timeout(2500) });
  return r.json();
}
