"""Wereldmodel: wat er in `worlds/<naam>/world.json` staat, plus de omrekening naar lokale meters.

Alles in het bestand staat in lat/lon (ETRS89), zodat een wereld los van de oorsprong blijft kloppen en direct met
RTK-metingen te vergelijken is. In het geheugen heeft elk object ook zijn geometrie in lokale meters (`xy`), die
de rest van de sim gebruikt.

Een wereld van een echte plek (BGT/3D BAG/AHN) en een fantasiewereld hebben hetzelfde formaat; alleen de bron
verschilt. Nieuwe soorten objecten krijgen een eigen lijst met een eigen dataclass, zodat oudere bestanden blijven
werken (onbekende sleutels worden bewaard in `extra`).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

from openagbot_fieldsim.geo import LocalFrame
from openagbot_fieldsim.world.geometry import Point, ccw, point_in_polygon

FORMAT = "openagbot_fieldsim.world"
LEGACY_FORMATS = ("simworld.world",)      # werelden van voor de hernoeming laden nog
VERSION = 1

# Soorten oppervlak. De kleur staat in de UI (theme.js), de fysica gebruikt `grip` en `no_go`.
SURFACE_KINDS = {
    "grass": {"grip": 0.85, "no_go": False},
    "verge": {"grip": 0.8, "no_go": False},
    "yard": {"grip": 1.0, "no_go": False},
    "paved": {"grip": 1.0, "no_go": False},
    "road": {"grip": 1.0, "no_go": False},
    "track": {"grip": 0.9, "no_go": False},
    "water": {"grip": 0.2, "no_go": True},
}

LatLon = tuple[float, float]


def _round_ll(pts) -> list[list[float]]:
    return [[round(a, 9), round(b, 9)] for a, b in pts]


@dataclass
class Surface:
    kind: str
    polygon: list[LatLon]
    name: str = ""
    level_m: float | None = None      # waterpeil (alleen water uit de BGT); None = volgt het terrein
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        d = {"kind": self.kind, "polygon": _round_ll(self.polygon)}
        if self.name:
            d["name"] = self.name
        if self.level_m is not None:
            d["level_m"] = self.level_m
        return d


@dataclass
class Field:
    id: str
    name: str
    polygon: list[LatLon]
    crop: str = ""
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "crop": self.crop, "polygon": _round_ll(self.polygon)}


@dataclass
class Roof:
    type: str = "flat"            # flat | gable | cone (silo)
    ridge_height_m: float | None = None
    ridge_axis: str = "long"      # long | short: nok langs de lange of de korte zijde

    def to_dict(self):
        d = {"type": self.type, "ridge_axis": self.ridge_axis}
        if self.ridge_height_m is not None:
            d["ridge_height_m"] = self.ridge_height_m
        return d


@dataclass
class Building:
    id: str
    name: str
    kind: str                     # house | barn | shed | silo | wall
    footprint: list[LatLon]
    height_m: float               # goothoogte (bovenkant muur)
    roof: Roof = field(default_factory=Roof)
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "footprint": _round_ll(self.footprint),
                "height_m": self.height_m, "roof": self.roof.to_dict()}


@dataclass
class Tree:
    lat: float
    lon: float
    height_m: float = 12.0
    crown: str = "ball"           # ball | cone
    xy: Point = (0.0, 0.0)

    def to_dict(self):
        return {"lat": round(self.lat, 9), "lon": round(self.lon, 9), "height_m": self.height_m, "crown": self.crown}


@dataclass
class Ditch:
    """Sloot als hartlijn met breedte en diepte; wordt in het terrein uitgegraven en als water getekend."""
    line: list[LatLon]
    width_m: float = 2.0
    depth_m: float = 0.8
    water_depth_m: float = 0.3
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"line": _round_ll(self.line), "width_m": self.width_m, "depth_m": self.depth_m,
                "water_depth_m": self.water_depth_m}


@dataclass
class Fence:
    line: list[LatLon]
    height_m: float = 1.2
    post_spacing_m: float = 4.0
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"line": _round_ll(self.line), "height_m": self.height_m, "post_spacing_m": self.post_spacing_m}


@dataclass
class Pose:
    lat: float
    lon: float
    heading_deg: float = 0.0

    def to_dict(self):
        return {"lat": round(self.lat, 9), "lon": round(self.lon, 9), "heading_deg": self.heading_deg}


@dataclass
class World:
    name: str
    label: str
    origin: tuple[float, float, float]
    area: list[LatLon]
    terrain: dict[str, Any]
    margin_m: float = 50.0
    surfaces: list[Surface] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    buildings: list[Building] = field(default_factory=list)
    trees: list[Tree] = field(default_factory=list)
    ditches: list[Ditch] = field(default_factory=list)
    fences: list[Fence] = field(default_factory=list)
    start_pose: Pose | None = None
    style: str = "freecad"
    sources: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    folder: str | None = None

    def __post_init__(self):
        self._grid = None
        self.frame = LocalFrame(*self.origin)
        self.area_xy = ccw([self.frame.to_xy(*p) for p in self.area])
        for s in self.surfaces:
            s.xy = ccw([self.frame.to_xy(*p) for p in s.polygon])
        for f in self.fields:
            f.xy = ccw([self.frame.to_xy(*p) for p in f.polygon])
        for b in self.buildings:
            b.xy = ccw([self.frame.to_xy(*p) for p in b.footprint])
        for t in self.trees:
            t.xy = self.frame.to_xy(t.lat, t.lon)
        for d in self.ditches:
            d.xy = [self.frame.to_xy(*p) for p in d.line]
        for f in self.fences:
            f.xy = [self.frame.to_xy(*p) for p in f.line]

    # ------------------------------------------------------------------ queries
    def field_by_id(self, field_id: str) -> Field:
        for f in self.fields:
            if f.id == field_id:
                return f
        raise KeyError(field_id)

    def surface_at(self, x: float, y: float) -> str:
        """Soort oppervlak op (x, y); later getekende vlakken liggen bovenop eerdere.

        Via een raster van 0,5 m (eenmalig opgebouwd): een echte wereld heeft honderden BGT-vlakken en de fysica
        vraagt dit 1000 keer per seconde.
        """
        if self._grid is None:
            self._grid = SurfaceGrid(self)
        hit = self._grid.lookup(x, y)
        return hit if hit is not None else self._surface_at_slow(x, y)

    def _surface_at_slow(self, x: float, y: float) -> str:
        for d in self.ditches:
            if _near_line(x, y, d.xy, d.width_m / 2.0):
                return "water"
        for f in self.fields:
            if point_in_polygon(x, y, f.xy):
                return "field"
        for s in reversed(self.surfaces):
            if point_in_polygon(x, y, s.xy):
                return s.kind
        return "grass"

    def start_xy_heading(self) -> tuple[float, float, float]:
        if self.start_pose is None:
            return 0.0, 0.0, 0.0
        x, y = self.frame.to_xy(self.start_pose.lat, self.start_pose.lon)
        return x, y, self.start_pose.heading_deg

    # ------------------------------------------------------------------ io
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "format": FORMAT,
            "version": VERSION,
            "name": self.name,
            "label": self.label,
            "datum": "ETRS89",
            "style": self.style,
            "origin": {"lat": self.origin[0], "lon": self.origin[1], "h": self.origin[2]},
            "area": {"polygon": _round_ll(self.area), "margin_m": self.margin_m},
            "terrain": self.terrain,
            "sources": self.sources,
            "start_pose": self.start_pose.to_dict() if self.start_pose else None,
            "fields": [f.to_dict() for f in self.fields],
            "surfaces": [s.to_dict() for s in self.surfaces],
            "ditches": [x.to_dict() for x in self.ditches],
            "buildings": [b.to_dict() for b in self.buildings],
            "trees": [t.to_dict() for t in self.trees],
            "fences": [f.to_dict() for f in self.fences],
        }
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], folder: str | None = None) -> "World":
        if d.get("format", FORMAT) not in (FORMAT, *LEGACY_FORMATS):
            raise ValueError(f"geen fieldsim-wereld: format={d.get('format')!r}")
        if int(d.get("version", 1)) > VERSION:
            raise ValueError(f"wereldversie {d['version']} is nieuwer dan deze sim ({VERSION})")
        known = {"format", "version", "name", "label", "datum", "style", "origin", "area", "terrain", "sources",
                 "start_pose", "fields", "surfaces", "ditches", "buildings", "trees", "fences"}
        o = d["origin"]
        area = d["area"]
        sp = d.get("start_pose")
        return cls(
            name=d["name"],
            label=d.get("label", d["name"]),
            origin=(float(o["lat"]), float(o["lon"]), float(o.get("h") or 0.0)),
            area=[tuple(p) for p in area["polygon"]],
            margin_m=float(area.get("margin_m", 50.0)),
            terrain=d.get("terrain") or {"type": "flat"},
            style=d.get("style", "freecad"),
            sources=d.get("sources", {}),
            start_pose=Pose(sp["lat"], sp["lon"], sp.get("heading_deg") or 0.0)
            if sp and sp.get("lat") is not None else None,
            fields=[Field(f["id"], f.get("name", f["id"]), [tuple(p) for p in f["polygon"]], f.get("crop", ""))
                    for f in d.get("fields", [])],
            surfaces=[Surface(s["kind"], [tuple(p) for p in s["polygon"]], s.get("name", ""), s.get("level_m"))
                      for s in d.get("surfaces", [])],
            ditches=[Ditch([tuple(p) for p in x["line"]], x.get("width_m", 2.0), x.get("depth_m", 0.8),
                           x.get("water_depth_m", 0.3)) for x in d.get("ditches", [])],
            buildings=[Building(b["id"], b.get("name", b["id"]), b.get("kind", "shed"),
                                [tuple(p) for p in b["footprint"]], float(b["height_m"]),
                                Roof(**(b.get("roof") or {}))) for b in d.get("buildings", [])],
            trees=[Tree(t["lat"], t["lon"], t.get("height_m", 12.0), t.get("crown", "ball"))
                   for t in d.get("trees", [])],
            fences=[Fence([tuple(p) for p in f["line"]], f.get("height_m", 1.2), f.get("post_spacing_m", 4.0))
                    for f in d.get("fences", [])],
            extra={k: v for k, v in d.items() if k not in known},
            folder=folder,
        )

    def save(self, folder: str) -> str:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "world.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        self.folder = folder
        return path


def load_world(folder: str) -> World:
    with open(os.path.join(folder, "world.json"), encoding="utf-8") as fh:
        return World.from_dict(json.load(fh), folder=folder)


def _near_line(x: float, y: float, line: list[Point], r: float) -> bool:
    from openagbot_fieldsim.world.geometry import distance_to_segment
    for a, b in zip(line, line[1:]):
        if min(a[0], b[0]) - r <= x <= max(a[0], b[0]) + r and min(a[1], b[1]) - r <= y <= max(a[1], b[1]) + r:
            if distance_to_segment(x, y, a, b) <= r:
                return True
    return False


class SurfaceGrid:
    """Raster met de soort ondergrond per cel (zelfde regels als _surface_at_slow, maar vooraf uitgerekend)."""

    CELL = 0.5

    def __init__(self, world: "World"):
        import numpy as np
        from openagbot_fieldsim.world.geometry import bbox
        from openagbot_fieldsim.world.terrain import points_in_polygon
        x0, y0, x1, y1 = bbox(world.area_xy)
        m = world.margin_m
        c = self.CELL
        self.x0, self.y0 = x0 - m, y0 - m
        self.nx = int((x1 - x0 + 2 * m) / c) + 1
        self.ny = int((y1 - y0 + 2 * m) / c) + 1
        self.kinds = ["grass", "field", *sorted(SURFACE_KINDS)]
        code = {k: i for i, k in enumerate(self.kinds)}
        g = np.zeros((self.ny, self.nx), dtype=np.uint8)
        gx, gy = np.meshgrid(self.x0 + (np.arange(self.nx) + 0.5) * c, self.y0 + (np.arange(self.ny) + 0.5) * c)

        def paint(poly, value):
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            i0, i1 = max(0, int((min(xs) - self.x0) / c)), min(self.nx, int((max(xs) - self.x0) / c) + 2)
            j0, j1 = max(0, int((min(ys) - self.y0) / c)), min(self.ny, int((max(ys) - self.y0) / c) + 2)
            if i0 >= i1 or j0 >= j1:
                return
            sub = points_in_polygon(gx[j0:j1, i0:i1], gy[j0:j1, i0:i1], poly)
            g[j0:j1, i0:i1][sub] = value

        for s in world.surfaces:
            paint(s.xy, code.get(s.kind, 0))
        for f in world.fields:
            paint(f.xy, code["field"])
        for d in world.ditches:
            r = d.width_m / 2.0
            for a, b in zip(d.xy, d.xy[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                ll = math.hypot(dx, dy) or 1.0
                nx_, ny_ = -dy / ll * r, dx / ll * r
                paint([(a[0] + nx_, a[1] + ny_), (b[0] + nx_, b[1] + ny_), (b[0] - nx_, b[1] - ny_),
                       (a[0] - nx_, a[1] - ny_)], code["water"])
        self.g = g.tolist()

    def lookup(self, x: float, y: float):
        i = int((x - self.x0) / self.CELL)
        j = int((y - self.y0) / self.CELL)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return self.kinds[self.g[j][i]]
        return None


def new_world_from_points(name: str, label: str, points: list[tuple[float, float]], margin_m: float = 50.0) -> World:
    """Nieuwe (lege) wereld uit hoekpunten in willekeurige volgorde: gesorteerd rond het midden (geen vlinderdas)."""
    if len(points) < 3:
        raise ValueError("minstens drie punten nodig")
    lat0 = sum(p[0] for p in points) / len(points)
    lon0 = sum(p[1] for p in points) / len(points)
    k = math.cos(math.radians(lat0))
    ring = sorted(points, key=lambda p: math.atan2(p[0] - lat0, (p[1] - lon0) * k))
    return World(name=name, label=label or name, origin=(lat0, lon0, 0.0), area=[tuple(p) for p in ring],
                 margin_m=margin_m, terrain={"type": "flat"}, sources={"created": "openagbot_fieldsim"})


def parse_points(text: str) -> list[tuple[float, float]]:
    """'52.2147, 4.5889 52.2150, 4.6057 ...' of een punt per regel -> [(lat, lon), ...]."""
    import re
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if len(nums) % 2:
        raise ValueError("oneven aantal getallen: geef lat, lon paren")
    pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    for lat, lon in pts:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"geen geldige coördinaat: {lat}, {lon}")
    return pts
