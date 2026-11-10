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
BRIDGING = ("dam", "duiker", "brug")      # doorgangen die een sloot dichtmaken (berijdbaar, geen water)


def _round_ll(pts) -> list[list[float]]:
    return [[round(a, 9), round(b, 9)] for a, b in pts]


@dataclass
class Surface:
    kind: str
    polygon: list[LatLon]
    name: str = ""
    level_m: float | None = None      # waterpeil (alleen water uit de BGT); None = volgt het terrein
    id: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        d = {"id": self.id, "kind": self.kind, "polygon": _round_ll(self.polygon)}
        if self.name:
            d["name"] = self.name
        if self.level_m is not None:
            d["level_m"] = self.level_m
        return d


@dataclass
class Hole:
    """Gat in een veld (zoals een binnengrens in AgOpenGPS). `passable`: de robot mag erdoor rijden, niet werken."""
    polygon: list[LatLon]
    passable: bool = False
    name: str = ""
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        d = {"polygon": _round_ll(self.polygon), "passable": self.passable}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class Field:
    id: str
    name: str
    polygon: list[LatLon]
    crop: str = ""
    holes: list[Hole] = field(default_factory=list)
    headland_rounds: int | None = None      # standaard voor de missie van dit veld; None = instelling van de missie
    work_width_m: float | None = None
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        d = {"id": self.id, "name": self.name, "crop": self.crop, "polygon": _round_ll(self.polygon)}
        if self.holes:
            d["holes"] = [h.to_dict() for h in self.holes]
        if self.headland_rounds is not None:
            d["headland_rounds"] = self.headland_rounds
        if self.work_width_m is not None:
            d["work_width_m"] = self.work_width_m
        return d


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
    source: str = "bron"
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
    id: str = ""
    clearance_m: float | None = None    # eigen vrije ruimte vanaf de stam; None = de standaard voor bomen
    source: str = "bron"
    xy: Point = (0.0, 0.0)

    def to_dict(self):
        d = {"id": self.id, "lat": round(self.lat, 9), "lon": round(self.lon, 9), "height_m": self.height_m,
             "crown": self.crown}
        if self.clearance_m is not None:
            d["clearance_m"] = self.clearance_m
        return d


@dataclass
class Ditch:
    """Sloot als hartlijn met breedte en diepte; wordt in het terrein uitgegraven en als water getekend.
    Niet over te steken, behalve bij een doorgang (Passage)."""
    line: list[LatLon]
    width_m: float = 2.0
    depth_m: float = 0.8
    water_depth_m: float = 0.3
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        d = {"id": self.id, "line": _round_ll(self.line), "width_m": self.width_m, "depth_m": self.depth_m,
             "water_depth_m": self.water_depth_m}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class Fence:
    line: list[LatLon]
    height_m: float = 1.2
    post_spacing_m: float = 4.0
    id: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"id": self.id, "line": _round_ll(self.line), "height_m": self.height_m,
                "post_spacing_m": self.post_spacing_m}


DIKE_MODES = ("ontwijken", "negeren", "doorgang")     # doorgang = alleen over bij een helling/doorgang


@dataclass
class Dike:
    """Dijk als kruinlijn met kruinbreedte, hoogte en talud (1 : slope). Verhoogt het terrein."""
    line: list[LatLon]
    crest_width_m: float = 2.0
    height_m: float = 1.5
    slope: float = 2.5
    mode: str = "ontwijken"
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    @property
    def half_base_m(self) -> float:
        return self.crest_width_m / 2.0 + self.height_m * self.slope

    def to_dict(self):
        return {"id": self.id, "name": self.name, "line": _round_ll(self.line), "crest_width_m": self.crest_width_m,
                "height_m": self.height_m, "slope": self.slope, "mode": self.mode}


@dataclass
class NoGo:
    """Verboden gebied. `stoppen`: de robot stopt zodra hij erin komt; `mijden`: de planner rijdt eromheen."""
    polygon: list[LatLon]
    action: str = "stoppen"
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "action": self.action, "polygon": _round_ll(self.polygon)}


ZONE_CLASSES = ("werken", "alleen_rijden", "niet_bewerken", "verboden")


@dataclass
class Zone:
    """Rijzone: `werken`, `alleen_rijden` (transport, werktuig omhoog), `niet_bewerken` (stuk van een veld dat de
    missie overslaat, bv. een punt of uitstulping; rijden en keren mag er wel) of `verboden`."""
    polygon: list[LatLon]
    klass: str = "alleen_rijden"
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "klass": self.klass, "polygon": _round_ll(self.polygon)}


PASSAGE_KINDS = ("dam", "duiker", "brug", "hek", "oversteek", "oprit")


@dataclass
class Passage:
    """Doorgang: de enige plek waar de robot een sloot, dijk, hek of weg mag passeren, en van veld naar veld mag.

    `line` is de rijlijn door de doorgang (van binnen het ene veld tot binnen het andere). `direction`: beide,
    heen (alleen in de richting van de lijn) of terug. `confirm`: eerst stoppen en wachten op vrijgave (oversteek).
    """
    line: list[LatLon]
    width_m: float = 4.0
    kind: str = "dam"
    direction: str = "beide"
    max_speed_kmh: float = 2.0
    confirm: bool = False
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: list[Point] = field(default_factory=list, repr=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "line": _round_ll(self.line),
                "width_m": self.width_m, "direction": self.direction, "max_speed_kmh": self.max_speed_kmh,
                "confirm": self.confirm}


@dataclass
class Nest:
    """Vogelnest: punt met straal, alleen verboden in zijn periode (maand-dag, bv. 03-15 tot 08-15)."""
    lat: float
    lon: float
    radius_m: float = 5.0
    start: str = "03-15"
    end: str = "08-15"
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: Point = (0.0, 0.0)

    def active(self, date=None) -> bool:
        import datetime
        if date is None:
            date = datetime.date.today()
        elif isinstance(date, str):
            date = datetime.date.fromisoformat(date[:10])
        try:
            a = tuple(int(v) for v in self.start.split("-"))
            b = tuple(int(v) for v in self.end.split("-"))
        except ValueError:
            return True                      # onleesbare periode: voor de zekerheid altijd actief
        d = (date.month, date.day)
        return a <= d <= b if a <= b else (d >= a or d <= b)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "lat": round(self.lat, 9), "lon": round(self.lon, 9),
                "radius_m": self.radius_m, "start": self.start, "end": self.end}


@dataclass
class Marker:
    """Steen, paal, put of kabel: een punt om omheen te rijden (zoals een vlag in AgOpenGPS)."""
    lat: float
    lon: float
    kind: str = "marker"          # marker | steen | paal | put | kabel
    radius_m: float = 0.5         # 0 = alleen een aantekening, geen obstakel
    note: str = ""
    id: str = ""
    name: str = ""
    source: str = "bron"
    xy: Point = (0.0, 0.0)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "lat": round(self.lat, 9),
                "lon": round(self.lon, 9), "radius_m": self.radius_m, "note": self.note}


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
    dikes: list[Dike] = field(default_factory=list)
    no_go: list[NoGo] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    nests: list[Nest] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    farm_settings: dict[str, Any] = field(default_factory=dict)    # uit farm.json (dijkmodus, vrije ruimtes, veiligheid)
    overlay: bool = False               # farm.json is toegepast: niet meer als world.json opslaan
    overlay_report: list[str] = field(default_factory=list)
    start_pose: Pose | None = None
    style: str = "freecad"
    sources: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    folder: str | None = None

    def __post_init__(self):
        self._grid = None
        self.frame = LocalFrame(*self.origin)
        for layer, (_, prefix) in LAYERS.items():        # in het geheugen gemaakte objecten: ook een id
            for i, obj in enumerate(getattr(self, layer)):
                if not obj.id:
                    obj.id = f"{prefix}_m{i + 1}"
        self.area_xy = ccw([self.frame.to_xy(*p) for p in self.area])
        for s in self.surfaces:
            s.xy = ccw([self.frame.to_xy(*p) for p in s.polygon])
        for f in self.fields:
            f.xy = ccw([self.frame.to_xy(*p) for p in f.polygon])
            for h in f.holes:
                h.xy = ccw([self.frame.to_xy(*p) for p in h.polygon])
        for g in (*self.no_go, *self.zones):
            g.xy = ccw([self.frame.to_xy(*p) for p in g.polygon])
        for g in (*self.dikes, *self.passages):
            g.xy = [self.frame.to_xy(*p) for p in g.line]
        for g in (*self.nests, *self.markers):
            g.xy = self.frame.to_xy(g.lat, g.lon)
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
        for p in self.passages:
            if p.kind in BRIDGING and _near_line(x, y, p.xy, p.width_m / 2.0):
                return "track"
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
        for key in NEW_LAYERS:
            if getattr(self, key):
                d[key] = [x.to_dict() for x in getattr(self, key)]
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], folder: str | None = None) -> "World":
        if d.get("format", FORMAT) not in (FORMAT, *LEGACY_FORMATS):
            raise ValueError(f"geen fieldsim-wereld: format={d.get('format')!r}")
        if int(d.get("version", 1)) > VERSION:
            raise ValueError(f"wereldversie {d['version']} is nieuwer dan deze sim ({VERSION})")
        known = {"format", "version", "name", "label", "datum", "style", "origin", "area", "terrain", "sources",
                 "start_pose", "fields", "surfaces", "ditches", "buildings", "trees", "fences", *NEW_LAYERS,
                 "farm_settings", "_overlay", "_overlay_report"}
        ensure_ids(d)
        src = lambda x: x.get("_source", "bron")      # noqa: E731
        ring = lambda pts: [tuple(p) for p in pts]    # noqa: E731
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
            fields=[Field(f["id"], f.get("name", f["id"]), ring(f["polygon"]), f.get("crop", ""),
                          [Hole(ring(h["polygon"]), bool(h.get("passable", False)), h.get("name", ""))
                           for h in f.get("holes", []) if len(h.get("polygon", [])) >= 3],
                          f.get("headland_rounds"), f.get("work_width_m"), src(f))
                    for f in d.get("fields", [])],
            surfaces=[Surface(s["kind"], ring(s["polygon"]), s.get("name", ""), s.get("level_m"), s["id"], src(s))
                      for s in d.get("surfaces", [])],
            ditches=[Ditch(ring(x["line"]), x.get("width_m", 2.0), x.get("depth_m", 0.8),
                           x.get("water_depth_m", 0.3), x["id"], x.get("name", ""), src(x))
                     for x in d.get("ditches", [])],
            buildings=[Building(b["id"], b.get("name", b["id"]), b.get("kind", "shed"),
                                ring(b["footprint"]), float(b["height_m"]),
                                Roof(**(b.get("roof") or {})), src(b)) for b in d.get("buildings", [])],
            trees=[Tree(t["lat"], t["lon"], t.get("height_m", 12.0), t.get("crown", "ball"), t["id"],
                        t.get("clearance_m"), src(t)) for t in d.get("trees", [])],
            fences=[Fence(ring(f["line"]), f.get("height_m", 1.2), f.get("post_spacing_m", 4.0), f["id"], src(f))
                    for f in d.get("fences", [])],
            dikes=[Dike(ring(x["line"]), float(x.get("crest_width_m", 2.0)), float(x.get("height_m", 1.5)),
                        float(x.get("slope", 2.5)), x.get("mode", "ontwijken") if x.get("mode") in DIKE_MODES
                        else "ontwijken", x["id"], x.get("name", ""), src(x)) for x in d.get("dikes", [])],
            no_go=[NoGo(ring(x["polygon"]), "mijden" if x.get("action") == "mijden" else "stoppen", x["id"],
                        x.get("name", ""), src(x)) for x in d.get("no_go", [])],
            zones=[Zone(ring(x["polygon"]), x.get("klass") if x.get("klass") in ZONE_CLASSES else "alleen_rijden",
                        x["id"], x.get("name", ""), src(x)) for x in d.get("zones", [])],
            passages=[Passage(ring(x["line"]), float(x.get("width_m", 4.0)), x.get("kind", "dam"),
                              x.get("direction", "beide"), float(x.get("max_speed_kmh", 2.0)),
                              bool(x.get("confirm", x.get("kind") == "oversteek")), x["id"], x.get("name", ""),
                              src(x)) for x in d.get("passages", [])],
            nests=[Nest(x["lat"], x["lon"], float(x.get("radius_m", 5.0)), x.get("start", "03-15"),
                        x.get("end", "08-15"), x["id"], x.get("name", ""), src(x)) for x in d.get("nests", [])],
            markers=[Marker(x["lat"], x["lon"], x.get("kind", "marker"), float(x.get("radius_m", 0.5)),
                            x.get("note", ""), x["id"], x.get("name", ""), src(x)) for x in d.get("markers", [])],
            farm_settings=d.get("farm_settings") or {},
            overlay=bool(d.get("_overlay")),
            overlay_report=list(d.get("_overlay_report") or []),
            extra={k: v for k, v in d.items() if k not in known},
            folder=folder,
        )

    def save(self, folder: str) -> str:
        if self.overlay:
            raise RuntimeError("deze wereld bevat de aanpassingen uit farm.json; bewaar die met world.farm.save_farm "
                               "(world.json blijft de bron)")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "world.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        self.folder = folder
        return path


def load_world(folder: str, overlay: bool = True) -> World:
    """Wereld laden. Met `overlay` worden de eigen aanpassingen uit farm.json (world/farm.py) eroverheen gelegd;
    de wereldbouwer laadt zonder, zodat world.json alleen de brondata blijft."""
    with open(os.path.join(folder, "world.json"), encoding="utf-8") as fh:
        base = json.load(fh)
    if overlay:
        from openagbot_fieldsim.world.farm import apply_farm, load_farm
        farm = load_farm(folder)
        if farm is not None:
            base = apply_farm(base, farm)
    return World.from_dict(base, folder=folder)


# Lagen die er na versie 1 bij kwamen (eigen lijst per soort, oudere bestanden hebben ze niet)
NEW_LAYERS = ("dikes", "no_go", "zones", "passages", "nests", "markers")
# laag -> (sleutel van de geometrie, voorvoegsel van het id)
LAYERS = {
    "fields": ("polygon", "veld"), "surfaces": ("polygon", "vlak"), "ditches": ("line", "sloot"),
    "fences": ("line", "hek"), "buildings": ("footprint", "pand"), "trees": ("point", "boom"),
    "dikes": ("line", "dijk"), "no_go": ("polygon", "verboden"), "zones": ("polygon", "zone"),
    "passages": ("line", "doorgang"), "nests": ("point", "nest"), "markers": ("point", "marker"),
}


def geometry_of(layer: str, obj: dict) -> list:
    """Punten [[lat, lon], ...] van een object (een punt-object geeft één punt)."""
    key = LAYERS[layer][0]
    if key == "point":
        return [[obj["lat"], obj["lon"]]]
    return obj[key]


def ensure_ids(d: dict) -> None:
    """Elk object een vast id: wat er al staat blijft, de rest krijgt een id uit zijn geometrie (op 1 cm afgerond).
    Zo houdt een object uit PDOK hetzelfde id als de wereld opnieuw gebouwd wordt en de kaart niet veranderd is."""
    import hashlib
    for layer, (_, prefix) in LAYERS.items():
        seen: set[str] = set()
        for obj in d.get(layer, []):
            oid = obj.get("id")
            if not oid:
                geom = [[round(a, 7), round(b, 7)] for a, b in geometry_of(layer, obj)]
                oid = f"{prefix}_{hashlib.sha1(json.dumps(geom).encode()).hexdigest()[:8]}"
            base, n = oid, 1
            while oid in seen:
                n += 1
                oid = f"{base}_{n}"
            seen.add(oid)
            obj["id"] = oid


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
        for p in world.passages:          # dam, duiker of brug: daar is de sloot dicht
            if p.kind not in BRIDGING:
                continue
            r = p.width_m / 2.0
            for a, b in zip(p.xy, p.xy[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                ll = math.hypot(dx, dy) or 1.0
                nx_, ny_ = -dy / ll * r, dx / ll * r
                paint([(a[0] + nx_, a[1] + ny_), (b[0] + nx_, b[1] + ny_), (b[0] - nx_, b[1] - ny_),
                       (a[0] - nx_, a[1] - ny_)], code["track"])
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
