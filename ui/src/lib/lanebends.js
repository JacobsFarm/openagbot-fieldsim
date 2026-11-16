// Rijpad met bredere bochten: de stukken van de lijn waar de bochtbreedte geldt (rond elke knik en aan de
// einden). Zelfde regel als bend_pieces in openagbot_fieldsim/missions/lanes.py; punten in meters.
const MIN_TURN = (2 * Math.PI) / 180;
const REACH = 4;

export function bendPieces(pts, width, bendWidth) {
  if (!bendWidth || bendWidth <= width || !pts || pts.length < 2) return [];
  const reach = Math.max(REACH, bendWidth);
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  const total = cum[cum.length - 1];
  if (total <= 0) return [];
  const at = [0];
  for (let i = 1; i < pts.length - 1; i++) {
    const ux = pts[i][0] - pts[i - 1][0], uy = pts[i][1] - pts[i - 1][1];
    const vx = pts[i + 1][0] - pts[i][0], vy = pts[i + 1][1] - pts[i][1];
    if (Math.abs(Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)) >= MIN_TURN) at.push(cum[i]);
  }
  at.push(total);
  const spans = [];
  for (const s of at) {
    const a = Math.max(0, s - reach), b = Math.min(total, s + reach);
    if (spans.length && a <= spans[spans.length - 1][1]) spans[spans.length - 1][1] = Math.max(spans[spans.length - 1][1], b);
    else spans.push([a, b]);
  }
  const point = (s) => {
    let i = 1;
    while (i < cum.length - 1 && cum[i] < s) i++;
    const k = (s - cum[i - 1]) / Math.max(1e-9, cum[i] - cum[i - 1]);
    return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k];
  };
  return spans.filter(([a, b]) => b - a > 1e-6).map(([a, b]) => {
    const out = [point(a)];
    for (let i = 1; i < pts.length - 1; i++) if (cum[i] > a && cum[i] < b) out.push(pts[i]);
    out.push(point(b));
    return out;
  });
}
