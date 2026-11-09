"""Reliëf als hoogteraster in lokale meters.

Bronnen (`world.json` -> "terrain"):
  {"type": "flat"}
  {"type": "procedural", "cell_m": 1.0, "base_m": 0.0, "slope_pct": [oost, noord],
   "bumps": [{"lat", "lon", "radius_m", "height_m"}], "noise": {"amplitude_m", "wavelength_m", "seed"}}
  {"type": "grid", "file": "terrain.npz"}          # uit AHN, gemaakt door de wereldbouwer (x0, y0, cell, z)

Sloten uit de wereld worden altijd in het raster uitgegraven, ook bij een AHN-raster (daar zitten ze al in, maar
met 0,5 m-cellen niet scherp genoeg voor een wiel dat erin rijdt).
"""
from __future__ import annotations

import math
import os

import numpy as np

from openagbot_fieldsim.world.geometry import bbox


class Terrain:
    def __init__(self, x0: float, y0: float, cell: float, z: np.ndarray):
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self.z = np.ascontiguousarray(z, dtype=np.float32)  # z[iy, ix]
        self.ny, self.nx = self.z.shape
        self._zl = self.z.tolist()  # snelle scalaire toegang voor de fysica
        self.ditch_levels: list[float] = []

    # ------------------------------------------------------------------ opvragen
    def height(self, x: float, y: float) -> float:
        fx = (x - self.x0) / self.cell
        fy = (y - self.y0) / self.cell
        ix = min(max(int(math.floor(fx)), 0), self.nx - 2)
        iy = min(max(int(math.floor(fy)), 0), self.ny - 2)
        tx = min(max(fx - ix, 0.0), 1.0)
        ty = min(max(fy - iy, 0.0), 1.0)
        r0, r1 = self._zl[iy], self._zl[iy + 1]
        a = r0[ix] + (r0[ix + 1] - r0[ix]) * tx
        b = r1[ix] + (r1[ix + 1] - r1[ix]) * tx
        return a + (b - a) * ty

    def heights(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        fx = (np.asarray(xs) - self.x0) / self.cell
        fy = (np.asarray(ys) - self.y0) / self.cell
        ix = np.clip(np.floor(fx).astype(int), 0, self.nx - 2)
        iy = np.clip(np.floor(fy).astype(int), 0, self.ny - 2)
        tx = np.clip(fx - ix, 0.0, 1.0)
        ty = np.clip(fy - iy, 0.0, 1.0)
        z = self.z
        a = z[iy, ix] + (z[iy, ix + 1] - z[iy, ix]) * tx
        b = z[iy + 1, ix] + (z[iy + 1, ix + 1] - z[iy + 1, ix]) * tx
        return a + (b - a) * ty

    def resampled(self, cell: float) -> "Terrain":
        """Grover raster voor de UI (minder driehoeken); de fysica blijft het fijne raster gebruiken."""
        if cell <= self.cell:
            return self
        nx = int(math.floor((self.nx - 1) * self.cell / cell)) + 1
        ny = int(math.floor((self.ny - 1) * self.cell / cell)) + 1
        xs = self.x0 + np.arange(nx) * cell
        ys = self.y0 + np.arange(ny) * cell
        gx, gy = np.meshgrid(xs, ys)
        return Terrain(self.x0, self.y0, cell, self.heights(gx, gy))

    def save(self, path: str) -> None:
        np.savez_compressed(path, x0=self.x0, y0=self.y0, cell=self.cell, z=self.z)


def build_terrain(world) -> Terrain:
    spec = world.terrain or {"type": "flat"}
    kind = spec.get("type", "flat")
    if kind == "grid":
        data = np.load(os.path.join(world.folder or ".", spec["file"]))
        t = Terrain(float(data["x0"]), float(data["y0"]), float(data["cell"]), data["z"])
        z, gx, gy = t.z.copy(), *_grid_xy(t.x0, t.y0, t.cell, t.nx, t.ny)
    else:
        cell = float(spec.get("cell_m", 1.0))
        x_min, y_min, x_max, y_max = bbox(world.area_xy)
        m = world.margin_m
        x0, y0 = math.floor((x_min - m) / cell) * cell, math.floor((y_min - m) / cell) * cell
        nx = int(math.ceil((x_max + m - x0) / cell)) + 1
        ny = int(math.ceil((y_max + m - y0) / cell)) + 1
        gx, gy = _grid_xy(x0, y0, cell, nx, ny)
        z = np.full(gx.shape, float(spec.get("base_m", 0.0)))
        if kind == "procedural":
            z += _procedural(world, spec, gx, gy)
        t = Terrain(x0, y0, cell, z)
    uncarved = Terrain(t.x0, t.y0, t.cell, z)
    carve = np.zeros_like(z)
    for d in world.ditches:
        _carve_ditch(carve, gx, gy, d)
    out = Terrain(t.x0, t.y0, t.cell, z - carve)
    # Waterpeil per sloot (zelfde volgorde als world.ditches), voor de fysica en de UI
    out.ditch_levels = [ditch_water_level(uncarved.height, d) for d in world.ditches]
    return out


def ditch_water_level(terrain_uncarved_height, ditch) -> float:
    """Waterpeil van een sloot: gemiddeld maaiveld langs de hartlijn min de diepte plus de waterdiepte."""
    samples = []
    for a, b in zip(ditch.xy, ditch.xy[1:]):
        n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 5.0))
        for i in range(n):
            s = i / (n - 1)
            samples.append(terrain_uncarved_height(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s))
    return sum(samples) / len(samples) - ditch.depth_m + ditch.water_depth_m


def _grid_xy(x0, y0, cell, nx, ny):
    return np.meshgrid(x0 + np.arange(nx) * cell, y0 + np.arange(ny) * cell)


def _procedural(world, spec, gx, gy) -> np.ndarray:
    z = np.zeros_like(gx)
    sx, sy = spec.get("slope_pct", [0.0, 0.0])
    z += gx * sx / 100.0 + gy * sy / 100.0
    for b in spec.get("bumps", []):
        bx, by = world.frame.to_xy(b["lat"], b["lon"])
        r = float(b["radius_m"])
        z += float(b["height_m"]) * np.exp(-((gx - bx) ** 2 + (gy - by) ** 2) / (2.0 * (r / 2.0) ** 2))
    noise = spec.get("noise")
    if noise:
        rng = np.random.default_rng(int(noise.get("seed", 1)))
        amp, wl = float(noise.get("amplitude_m", 0.1)), float(noise.get("wavelength_m", 50.0))
        # Som van een paar schuine golven: glad, herhaalbaar, zonder extra afhankelijkheden.
        for k in range(6):
            ang = rng.uniform(0, math.pi)
            lam = wl * rng.uniform(0.6, 1.6)
            ph = rng.uniform(0, 2 * math.pi)
            z += amp / 3.0 * np.sin((gx * math.cos(ang) + gy * math.sin(ang)) * 2 * math.pi / lam + ph)
    for flat in spec.get("flatten", []):
        _flatten(z, gx, gy, [world.frame.to_xy(*p) for p in flat["polygon"]], float(flat.get("edge_m", 5.0)))
    return z


def points_in_polygon(px: np.ndarray, py: np.ndarray, poly) -> np.ndarray:
    """Vectorvorm van geometry.point_in_polygon (even-odd)."""
    inside = np.zeros(px.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[i - 1]
        cross = (yi > py) != (yj > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (xj - xi) * (py - yi) / (yj - yi) + xi
        inside ^= cross & (px < xint)
    return inside


def _flatten(z, gx, gy, poly, edge) -> None:
    """Maak een vlak (erf) waterpas op zijn gemiddelde hoogte, met een zachte overgang van `edge` meter."""
    xs, ys = zip(*poly)
    sel = (gx >= min(xs) - edge) & (gx <= max(xs) + edge) & (gy >= min(ys) - edge) & (gy <= max(ys) + edge)
    px, py = gx[sel], gy[sel]
    inside = points_in_polygon(px, py, poly)
    if not inside.any():
        return
    level = float(z[sel][inside].mean())
    dist = np.full(px.shape, np.inf)
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy or 1.0
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / ll, 0.0, 1.0)
        dist = np.minimum(dist, np.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    w = np.where(inside, 1.0, np.clip(1.0 - dist / edge, 0.0, 1.0))
    w = w * w * (3.0 - 2.0 * w)   # smoothstep
    zs = z[sel]
    z[sel] = zs + (level - zs) * w


def _carve_ditch(carve, gx, gy, ditch) -> None:
    """Diepte van de sloot per cel; de grootste van alle segmenten telt (geen dubbele diepte in de knikken)."""
    half = ditch.width_m / 2.0
    talud = max(0.2, min(ditch.depth_m * 1.0, half * 0.7))
    for (ax, ay), (bx, by) in zip(ditch.xy, ditch.xy[1:]):
        x_lo, x_hi = min(ax, bx) - half, max(ax, bx) + half
        y_lo, y_hi = min(ay, by) - half, max(ay, by) + half
        sel = (gx >= x_lo) & (gx <= x_hi) & (gy >= y_lo) & (gy <= y_hi)
        if not sel.any():
            continue
        px, py = gx[sel], gy[sel]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy or 1.0
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / ll, 0.0, 1.0)
        d = np.hypot(px - (ax + t * dx), py - (ay + t * dy))
        frac = np.clip((half - d) / talud, 0.0, 1.0)
        carve[sel] = np.maximum(carve[sel], ditch.depth_m * frac)
