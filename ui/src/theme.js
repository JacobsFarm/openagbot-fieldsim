// Stijl van de sim = stijl van de veldanimaties (PLAN.md hoofdstuk 3).
// Bronnen: make_video_fld.py (panelen, letters), build_fld.py (veldkleuren), build_agbot.py (robotkleuren).
// Kleuren in 3D staan als FreeCAD-RGB (0-1, sRGB), zodat ze 1-op-1 met de FCStd-modellen te vergelijken zijn.

export const ui = {
  font: '"Segoe UI", Arial, sans-serif',
  ink: '#1E1E1E',
  muted: '#5F5F5F',
  panel: 'rgba(255,255,255,0.87)',
  radius: 10,
  margin: 14,
  blue: '#1E64C8',
  green: '#3C8232',
  orange: '#E66E14',
  purple: '#7846AA',
  track: '#E6E6E6',
};

export const color = {
  // veld (build_fld.py)
  field: [0.47, 0.63, 0.31],
  headland: [0.60, 0.70, 0.44],
  lines: [0.66, 0.78, 0.50],
  treated: [0.33, 0.50, 0.24],
  slot: [0.22, 0.14, 0.08],
  path: [0.96, 0.96, 0.92],
  post: [0.45, 0.33, 0.20],
  wire: [0.75, 0.75, 0.75],
  // speelwereld (zelfde palet, zie PLAN.md hoofdstuk 4)
  grass: [0.60, 0.70, 0.44],
  verge: [0.64, 0.73, 0.48],
  yard: [0.80, 0.79, 0.75],
  paved: [0.72, 0.72, 0.70],
  road: [0.55, 0.55, 0.54],
  track: [0.78, 0.72, 0.58],
  water: [0.55, 0.72, 0.85],
  wall: [0.93, 0.92, 0.88],
  roof_house: [0.60, 0.33, 0.25],
  roof_barn: [0.55, 0.57, 0.58],
  concrete: [0.80, 0.80, 0.78],
  trunk: [0.45, 0.33, 0.20],
  crown: [0.36, 0.53, 0.26],
  crown_dark: [0.27, 0.43, 0.22],
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
  // status-baken (animate_fld.py)
  beacon_green: [0.10, 0.80, 0.25],
  beacon_amber: [1.00, 0.55, 0.05],
  beacon_red: [0.85, 0.08, 0.06],
};

export const edgeColor = 0x191919;     // FreeCAD-randen: bijna zwart
export const background = 0xffffff;    // saveImage(..., "White")
export const fovDeg = 40;              // track_camera(fov=40)

export const surfaceColor = (kind) => color[kind] || color.grass;
