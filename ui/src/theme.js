// Stijl van de sim: een heldere 'simulator'-look (lucht, zon, schaduw, texturen) met een donkere HUD.
// De robotkleuren blijven die van build_agbot.py, zodat het model 1-op-1 met de FCStd te vergelijken is.
// Kleuren in 3D staan als RGB (0-1, sRGB).

export const ui = {
  font: '"Barlow", "Segoe UI", Arial, sans-serif',
  display: '"Barlow Condensed", "Bahnschrift", "Segoe UI", sans-serif',
  ink: '#E9F0F2',
  muted: '#8FA1AA',
  panel: 'rgba(13,19,23,0.82)',
  accent: '#B9E34A',
  amber: '#FFB23E',
  red: '#FF5A4D',
  blue: '#4FB3FF',
};

export const color = {
  // veld en missie
  field: [0.36, 0.56, 0.22],
  headland: [0.48, 0.62, 0.32],
  lines: [1.0, 1.0, 1.0],             // geplande banen
  treated: [0.72, 0.89, 0.29],        // bewerkte strook (accentkleur van de HUD)
  boundary: [1.0, 0.95, 0.72],        // perceelgrens
  path: [1.0, 0.70, 0.24],            // route die de robot zelf plant
  nogo: [1.0, 0.35, 0.30],            // sloten markeren
  post: [0.42, 0.31, 0.20],
  wire: [0.62, 0.64, 0.66],
  // ondergrond (ook de kleuren van de minikaart)
  grass: [0.38, 0.52, 0.22],
  verge: [0.50, 0.56, 0.28],
  yard: [0.66, 0.65, 0.61],
  paved: [0.60, 0.60, 0.58],
  road: [0.29, 0.29, 0.31],
  track: [0.70, 0.60, 0.42],
  water: [0.20, 0.55, 0.86],
  wall: [0.80, 0.79, 0.75],
  roof_house: [0.62, 0.30, 0.22],
  roof_barn: [0.46, 0.48, 0.50],
  concrete: [0.68, 0.68, 0.66],
  trunk: [0.36, 0.27, 0.18],
  crown: [0.40, 0.62, 0.26],
  crown_dark: [0.26, 0.48, 0.28],
  // robot (build_agbot.py)
  tire: [0.07, 0.07, 0.07],
  rim: [0.14, 0.14, 0.15],
  hub: [0.32, 0.33, 0.35],
  fork: [0.46, 0.47, 0.50],
  top_plate: [0.50, 0.38, 0.30],
  zinc: [0.82, 0.84, 0.87],
  beam_lower: [0.47, 0.31, 0.22],
  beam_upper: [0.11, 0.11, 0.12],
  bearing: [0.10, 0.27, 0.62],
  stepper: [0.06, 0.06, 0.07],
  wood: [0.74, 0.54, 0.31],
  white: [0.96, 0.96, 0.95],
  // status-baken
  beacon_green: [0.10, 0.80, 0.25],
  beacon_amber: [1.00, 0.55, 0.05],
  beacon_red: [0.85, 0.08, 0.06],
};

export const edgeColor = 0x101010;     // CAD-randen van robot en werktuig (instelling)
export const fovDeg = 40;

export const css = (c, a = 1) => `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;
