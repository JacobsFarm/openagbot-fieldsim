"""Catalogus van de aanbouwdelen in de sim. Eén plek per werktuig: bron in de aanbouwdelen-map, montage, werkwijze.

Alleen standaardbibliotheek: ook tools/export_implements.py (in FreeCAD) leest dit bestand.

Coördinaten zoals in het FreeCAD-model van het werktuig (mm, x rechts, y vooruit). `mount` zegt welke as van de
robot in dat model op welke y ligt; de sim legt die as op zijn eigen as (achteras = oorsprong, vooras = wielbasis
uit de robotconfig). Zo blijft het werktuig goed staan ten opzichte van de as waaraan het hangt, ook al heeft de
sim-robot (config 1,20 x 1,00 m) andere maten dan het FreeCAD-robotmodel (1,00 x 0,75 m).

Een nieuw werktuig toevoegen:
  1. hier een ImplementSpec (en in tools/export_implements.py een recept dat de standen bouwt);
  2. `freecadcmd tools/export_implements.py` -> openagbot_fieldsim/implements/assets/<id>/*.glb + meta.json;
  3. kind "strip" (strook bewerken) of "spot" (punten behandelen) werkt dan zonder verdere code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

SOURCE_ROOT = os.environ.get("FIELDSIM_IMPLEMENTS_SRC", r"F:\veldrobot\aanbouwdelen")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


@dataclass(frozen=True)
class Section:
    """Bewerkte strook in het werktuigmodel: x van .. tot, op hoogte y (mm). Voor 'spot': het gereedschap."""
    x0: float
    x1: float
    y: float


@dataclass(frozen=True)
class ImplementSpec:
    id: str
    name: str
    kind: str                       # "strip" of "spot"
    source: str                     # map onder SOURCE_ROOT
    module: str                     # bouwscript (build_<p>)
    prefix: str                     # code van het werktuig (sbm, dwd8, lfa2)
    axle: str                       # "front" of "rear": as waaraan het werktuig hangt
    axle_y_mm: float                # y van die as in het werktuigmodel
    sections: tuple[Section, ...]
    work_speed_kmh: float
    power_w: float = 0.0            # elektrisch vermogen in het werk (uit <p>_calc / README)
    color: str = "#6aa84f"          # kleur van de bewerkte strook
    rate_l_ha: float = 0.0          # vloeistof: standaarddosis
    reach_x_mm: float = 0.0         # spot: zijwaartse slag van het gereedschap (+-)
    cycle_s: float = 0.0            # spot: tijd per behandeling (robot staat stil)
    layers: dict = field(default_factory=dict)   # glb-node -> FreeCAD-Parts die los bewegen
    note: str = ""

    @property
    def width_m(self) -> float:
        return (max(s.x1 for s in self.sections) - min(s.x0 for s in self.sections)) / 1000.0

    def to_robot(self, x_mm: float, y_mm: float, wheelbase_m: float) -> tuple[float, float]:
        """Punt in het werktuigmodel -> robotframe van de sim (m, oorsprong midden achteras)."""
        base = wheelbase_m if self.axle == "front" else 0.0
        return x_mm / 1000.0, base + (y_mm - self.axle_y_mm) / 1000.0

    def to_dict(self, wheelbase_m: float) -> dict:
        ox, oy = self.to_robot(0.0, 0.0, wheelbase_m)
        return {"id": self.id, "name": self.name, "kind": self.kind, "width_m": self.width_m,
                "work_speed_kmh": self.work_speed_kmh, "power_w": self.power_w, "color": self.color,
                "rate_l_ha": self.rate_l_ha, "reach_x_m": self.reach_x_mm / 1000.0, "cycle_s": self.cycle_s,
                "offset": [ox, oy],     # waar de oorsprong van het glb op de robot komt
                "sections": [[s.x0 / 1000.0, s.x1 / 1000.0, self.to_robot(0, s.y, wheelbase_m)[1]]
                             for s in self.sections],
                "layers": list(self.layers), "note": self.note,
                "poses": sorted(p[:-4] for p in os.listdir(os.path.join(ASSETS, self.id)) if p.endswith(".glb"))
                if os.path.isdir(os.path.join(ASSETS, self.id)) else []}


CATALOG: tuple[ImplementSpec, ...] = (
    ImplementSpec(
        id="sickle_bar_mower", name="Messenbalk (3 balken, 3,25 m)", kind="strip",
        source=r"sickle bar mower\triple", module="build_sbm", prefix="sbm",
        axle="front", axle_y_mm=500.0,
        # voorbalk 1,25 m voor de vooras, vleugels 2 x 1,0 m tussen de assen (sbm_params: y_front 1020, vleugels y 140)
        sections=(Section(-625, 625, 1020), Section(-1625, -625, 140), Section(625, 1625, 140)),
        work_speed_kmh=3.6, power_w=2200.0, color="#d9cf6e",
        note="2,2 kW maaien bij 1 m/s in 3 t ds/ha (sbm_calc)."),
    ImplementSpec(
        id="dockweed_drill_v8", name="Ridderzuringboor v8", kind="spot",
        source=r"dockweed drill\v8", module="build_dwd8", prefix="dwd8",
        axle="rear", axle_y_mm=-500.0,
        sections=(Section(0, 0, -954),),          # spilhart (meta.json van de export wint)
        work_speed_kmh=1.5, power_w=0.0, color="#c0504d",
        reach_x_mm=500.0, cycle_s=21.8,
        layers={"carriage": ("X_carriage", "Pot")},
        note="X-slede +-500 mm; de robot moet bij elke plant stilstaan zolang de boor werkt."),
    ImplementSpec(
        id="liquid_fertilizer_v2", name="Vloeibare-mestinjecteur advanced V2 (5 rijen)", kind="strip",
        source=r"liquid fertilizer applicator\concept versions\advanced_V2", module="build_lfa2", prefix="lfa2",
        axle="rear", axle_y_mm=75.0,               # model-y 0 = achterste onderbalk, achteras 75 mm ervoor
        sections=tuple(Section(x - 100, x + 100, -730) for x in (-400, -200, 0, 200, 400)),
        work_speed_kmh=2.7, power_w=150.0, color="#4f81bd", rate_l_ha=500.0,
        note="5 rijen op 200 mm, V-schijven met dubbel mes; dosis per vloeistof (lfa2_params.dose_l_ha)."),
)


def get(implement_id: str) -> ImplementSpec:
    for s in CATALOG:
        if s.id == implement_id:
            return s
    raise KeyError(implement_id)
