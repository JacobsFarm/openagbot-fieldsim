// Luchtfoto van PDOK voor een kader in lokale meters, in tegels (PDOK weigert beelden boven ca. 2500 pixels).
// Geeft [{ img: ImageBitmap, b: { x0, y0, x1, y1 } }, ...]; onProgress(klaar, totaal) is optioneel.
const TILE_PX = 2048;

export async function loadAerialTiles(x0, y0, x1, y1, { mPerPx = 0.2, maxTiles = 16, onProgress = null } = {}) {
  const w = x1 - x0, h = y1 - y0;
  let nx = Math.max(1, Math.ceil(w / mPerPx / TILE_PX)), ny = Math.max(1, Math.ceil(h / mPerPx / TILE_PX));
  while (nx * ny > maxTiles) { if (nx >= ny && nx > 1) nx--; else if (ny > 1) ny--; else break; }
  const jobs = [];
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    jobs.push([x0 + (w * i) / nx, y0 + (h * j) / ny, x0 + (w * (i + 1)) / nx, y0 + (h * (j + 1)) / ny]);
  }
  const tiles = [];
  let done = 0, next = 0;
  const worker = async () => {
    while (next < jobs.length) {
      const [a, b, c, d] = jobs[next++];
      const r = await fetch(`/api/editor/aerial?x0=${a}&y0=${b}&x1=${c}&y1=${d}&px=${TILE_PX}`);
      if (!r.ok) {
        const t = await r.text();
        let msg = t;
        try { msg = JSON.parse(t).detail ?? t; } catch { /* geen json */ }
        throw new Error(msg);
      }
      tiles.push({ img: await createImageBitmap(await r.blob()), b: JSON.parse(r.headers.get('X-Bounds')) });
      onProgress?.(++done, jobs.length);
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  return tiles;
}
