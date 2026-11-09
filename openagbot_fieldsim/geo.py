"""Coördinaten: geodetisch (lat/lon/h op het GRS80-ellipsoïde) <-> lokaal ENU in meters.

Intern rekent de sim in een lokaal oost-noord-op stelsel (x = oost, y = noord, z = op) rond de oorsprong van de
wereld. Opgeslagen wordt in lat/lon. De omrekening is exact via ECEF (geen vlakke-aarde benadering), zodat ook een
wereld van een paar kilometer op de millimeter klopt.

Datum: alle coördinaten zijn ETRS89 (wat Centipede-RTK levert). GRS80 en WGS84 verschillen in de afplatting pas op
de 0,1 mm, dus dezelfde formules gelden; het datumverschil (ca. 0,8 m in 2026) zit in de kaartbronnen, niet hier.

Koersen: kompasgraden zoals de robot, 0 = noord, met de klok mee. In ENU is dat atan2(dx, dy).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# GRS80
A = 6378137.0
F = 1.0 / 298.257222101
E2 = F * (2.0 - F)


def geodetic_to_ecef(lat: float, lon: float, h: float = 0.0) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    s, c = math.sin(la), math.cos(la)
    n = A / math.sqrt(1.0 - E2 * s * s)
    return ((n + h) * c * math.cos(lo), (n + h) * c * math.sin(lo), (n * (1.0 - E2) + h) * s)


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Bowring met twee iteraties: ruim onder de 0,1 mm op aardoppervlak-hoogtes."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - E2))
    h = 0.0
    for _ in range(5):
        s = math.sin(lat)
        n = A / math.sqrt(1.0 - E2 * s * s)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - E2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


@dataclass(frozen=True)
class LocalFrame:
    """Lokaal ENU-stelsel met de oorsprong op (lat0, lon0, h0)."""

    lat0: float
    lon0: float
    h0: float = 0.0

    def __post_init__(self):
        la, lo = math.radians(self.lat0), math.radians(self.lon0)
        object.__setattr__(self, "_ecef0", geodetic_to_ecef(self.lat0, self.lon0, self.h0))
        sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
        # Rijen: oost, noord, op (in ECEF)
        object.__setattr__(self, "_r", ((-so, co, 0.0), (-sl * co, -sl * so, cl), (cl * co, cl * so, sl)))

    def to_enu(self, lat: float, lon: float, h: float = 0.0) -> tuple[float, float, float]:
        x, y, z = geodetic_to_ecef(lat, lon, h)
        x0, y0, z0 = self._ecef0
        d = (x - x0, y - y0, z - z0)
        r = self._r
        return tuple(r[i][0] * d[0] + r[i][1] * d[1] + r[i][2] * d[2] for i in range(3))  # type: ignore[return-value]

    def to_geodetic(self, e: float, n: float, u: float = 0.0) -> tuple[float, float, float]:
        r = self._r
        x0, y0, z0 = self._ecef0
        # Transponeren = inverse van de rotatie
        x = x0 + r[0][0] * e + r[1][0] * n + r[2][0] * u
        y = y0 + r[0][1] * e + r[1][1] * n + r[2][1] * u
        z = z0 + r[0][2] * e + r[1][2] * n + r[2][2] * u
        return ecef_to_geodetic(x, y, z)

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        e, n, _ = self.to_enu(lat, lon, self.h0)
        return e, n

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        """Inverse van to_xy: het punt op hoogte h0 waarvan de loodrechte projectie op het raakvlak (x, y) is.

        Zonder de kromming mee te nemen (u = 0) zou een punt op 10 km ca. 2 cm verschuiven; daarom wordt u zo
        gekozen dat de ellipsoïdische hoogte weer h0 is.
        """
        u = -(x * x + y * y) / (2.0 * A)
        for _ in range(3):
            lat, lon, h = self.to_geodetic(x, y, u)
            u -= h - self.h0
        lat, lon, _ = self.to_geodetic(x, y, u)
        return lat, lon


def heading_deg(dx: float, dy: float) -> float:
    """Kompaskoers (0 = noord, met de klok mee) van een verplaatsing in ENU."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def heading_to_unit(heading: float) -> tuple[float, float]:
    """Eenheidsvector (oost, noord) voor een kompaskoers."""
    h = math.radians(heading)
    return math.sin(h), math.cos(h)


def norm180(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0
