// Instellingen van de weergave; blijven bewaard in de browser (of het venster van de .exe).
const KEY = 'fieldsim.settings.v1';

export const DEFAULTS = {
  quality: 'normaal',     // laag | normaal | hoog
  shadows: true,
  hour: 17,               // tijd van de dag (zonnestand)
  clouds: true,
  edges: false,           // CAD-randen op robot en werktuig
  boundaries: true,       // perceelgrenzen
  labels: true,           // veldnamen
  nogo: false,            // rand van sloten en water markeren (daar loopt de robot vast)
  hud: true,
};

export function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveSettings(s) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* privé-venster: dan alleen deze sessie */ }
}

// Zet alles op de 3D-wereld; `only` = de sleutel die net veranderde (anders alles)
export function applySettings(w3d, s, only = null) {
  if (!w3d) return;
  const on = (k) => only === null || only === k;
  if (on('quality')) w3d.setQuality(s.quality);
  if (on('shadows') || on('quality')) w3d.setShadows(s.shadows && s.quality !== 'laag');
  if (on('hour')) w3d.setHour(s.hour);
  if (on('clouds')) w3d.setClouds(s.clouds);
  if (on('edges')) w3d.setEdges(s.edges);
  for (const k of ['boundaries', 'labels', 'nogo']) if (on(k)) w3d.setLayerVisible(k, s[k]);
}
