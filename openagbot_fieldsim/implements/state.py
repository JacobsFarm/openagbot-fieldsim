"""Werking van een aanbouwdeel in de sim: per stap wat het bewerkt, en wat dat kost.

- "strip": een strook (maaibalk, injecteur). Bewerkte strook als randen links/rechts, oppervlak, liters, energie.
- "spot": een gereedschap op een X-slede (ridderzuringboor). Per plant: in bereik? stond de robot lang genoeg
  stil om te boren? Zo zie je of een route en de robotsoftware de planten echt kunnen behandelen.

Stand van het werktuig: "auto" = omlaag in een veld (ondergrond "field") en omhoog daarbuiten, of vast omhoog/omlaag.
De robotsoftware stuurt nog geen werktuig aan (zie PLAN fase 6); tot dan bedient de sim het zelf.
"""
from __future__ import annotations

import json
import math
import os

from openagbot_fieldsim.implements.catalog import ASSETS, ImplementSpec

STOP_SPEED = 0.05          # m/s: daaronder staat de robot stil voor de boor
ALONG_TOL = 0.05           # m: plant moet binnen 5 cm voor/achter het spilhart liggen tijdens het boren
NEAR_X = 2.0              # m: verder opzij telt een plant niet als "langsgereden"


def load_meta(spec: ImplementSpec) -> dict:
    p = os.path.join(ASSETS, spec.id, "meta.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh).get("meta", {})
    return {}


class ImplementState:
    def __init__(self, spec: ImplementSpec, wheelbase_m: float):
        self.spec = spec
        self.wheelbase = wheelbase_m
        meta = load_meta(spec)
        self.cycle_s = float(meta.get("cycle_s", spec.cycle_s))
        self.sections = [(s.x0 / 1000.0, s.x1 / 1000.0, spec.to_robot(0.0, s.y, wheelbase_m)[1])
                         for s in spec.sections]
        if spec.kind == "spot" and "tool_y_mm" in meta:
            self.sections = [(0.0, 0.0, spec.to_robot(0.0, meta["tool_y_mm"], wheelbase_m)[1])]
        self.mode = "auto"
        self.rate_l_ha = spec.rate_l_ha
        self.lowered = False
        self.carriage_x = 0.0
        self.drilling = 0.0           # s bezig met de huidige plant
        self.targets: list[dict] = []
        self.generation = 0           # telt bij elke reset: de UI begint de strook dan opnieuw
        self.reset()

    # ------------------------------------------------------------------ bediening
    def reset(self):
        self.generation += 1
        self.area_m2 = 0.0
        self.liquid_l = 0.0
        self.energy_wh = 0.0
        self.distance_m = 0.0
        self.strip: list = []         # [lx, ly, rx, ry] of None (onderbreking)
        self._last = None
        for t in self.targets:
            t.update(status="open", dx=None, dwell=0.0, near=False)

    def set_targets(self, xy: list[tuple[float, float]], names: list[str] | None = None):
        names = names or [f"punt {i + 1}" for i in range(len(xy))]
        self.targets = [{"x": x, "y": y, "name": n, "status": "open", "dx": None, "dwell": 0.0}
                        for (x, y), n in zip(xy, names)]

    # ------------------------------------------------------------------ meetkunde
    @staticmethod
    def _axes(heading):
        h = math.radians(heading)
        return (math.sin(h), math.cos(h)), (math.cos(h), -math.sin(h))   # vooruit, rechts

    def _world(self, s, x, y):
        f, r = self._axes(s.heading)
        return s.x + x * r[0] + y * f[0], s.y + x * r[1] + y * f[1]

    def _local(self, s, wx, wy):
        f, r = self._axes(s.heading)
        dx, dy = wx - s.x, wy - s.y
        return dx * r[0] + dy * r[1], dx * f[0] + dy * f[1]

    # ------------------------------------------------------------------ stap
    def step(self, dt: float, s) -> float:
        """Eén simulatiestap; geeft het elektrisch vermogen (W) van het werktuig terug."""
        self.lowered = self.mode == "down" or (self.mode == "auto" and s.surface == "field")
        moving = abs(s.v) > 0.02
        if self.spec.kind == "strip":
            return self._step_strip(dt, s, moving)
        return self._step_spot(dt, s)

    def _step_strip(self, dt, s, moving) -> float:
        if not self.lowered:
            if self.strip and self.strip[-1] is not None:
                self.strip.append(None)
            self._last = None
            return 0.0
        d = abs(s.v) * dt
        w = self.spec.width_m
        self.area_m2 += w * d
        self.distance_m += d
        self.liquid_l += self.rate_l_ha * w * d / 1e4
        x0 = min(a for a, _, _ in self.sections)
        x1 = max(b for _, b, _ in self.sections)
        y = min(c for _, _, c in self.sections)           # achterste sectie: daar is alles bewerkt
        here = (s.x, s.y)
        if self._last is None or math.hypot(here[0] - self._last[0], here[1] - self._last[1]) > 0.25:
            lx, ly = self._world(s, x0, y)
            rx, ry = self._world(s, x1, y)
            self.strip.append([round(lx, 3), round(ly, 3), round(rx, 3), round(ry, 3)])
            self._last = here
        p = self.spec.power_w * min(1.0, abs(s.v) / max(0.1, self.spec.work_speed_kmh / 3.6)) if moving else 0.0
        self.energy_wh += p * dt / 3600.0
        return p

    def _step_spot(self, dt, s) -> float:
        reach = self.spec.reach_x_mm / 1000.0
        ty = self.sections[0][2]
        best = None
        for t in self.targets:
            if t["status"] != "open":
                continue
            dx, dy = self._local(s, t["x"], t["y"])
            along = dy - ty                            # + = plant ligt nog voor de spil
            if abs(dx) > NEAR_X or abs(along) > 2.0:
                t["near"] = False                      # niet in de buurt van het werktuig
                continue
            if t.get("near") and along < -0.3:         # de spil is er net langs gegaan: kans gemist
                t["status"] = "missed_speed" if abs(t["dx"] if t["dx"] is not None else dx) <= reach else "out_of_reach"
                continue
            if along >= -0.3:
                t["near"] = True                       # plant komt eraan (voor of bij de spil)
            if abs(along) <= ALONG_TOL:
                t["dx"] = round(dx, 3)
            if -ALONG_TOL <= along < 1.5 and (best is None or along < best[0]):
                best = (along, dx, t)
        power = 0.0
        if best is not None and abs(best[1]) <= reach:
            self.carriage_x = max(-reach, min(reach, best[1]))
            along, dx, t = best
            if self.lowered and abs(along) <= ALONG_TOL and abs(s.v) < STOP_SPEED:
                t["dwell"] += dt
                self.drilling = t["dwell"]
                power = 600.0
                if t["dwell"] >= self.cycle_s:
                    t["status"] = "done"
                    self.drilling = 0.0
            else:
                self.drilling = 0.0
        else:
            self.drilling = 0.0
        self.energy_wh += power * dt / 3600.0
        return power

    # ------------------------------------------------------------------ uitlezen
    def snapshot(self, strip_from: int = 0) -> dict:
        counts = {}
        for t in self.targets:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        out = {"id": self.spec.id, "gen": self.generation, "mode": self.mode, "lowered": self.lowered, "rate_l_ha": self.rate_l_ha,
               "area_ha": self.area_m2 / 1e4, "liquid_l": self.liquid_l, "energy_wh": self.energy_wh,
               "distance_m": self.distance_m, "carriage_x": self.carriage_x,
               "drilling_s": self.drilling, "cycle_s": self.cycle_s,
               "strip": {"from": min(strip_from, len(self.strip)), "points": self.strip[strip_from:],
                         "total": len(self.strip)}}
        if self.spec.kind == "spot":
            out["targets"] = counts
            dxs = [abs(t["dx"]) for t in self.targets if t["dx"] is not None]
            out["dx_mean_m"] = sum(dxs) / len(dxs) if dxs else None
        return out

    def target_list(self) -> list[dict]:
        return [dict(t) for t in self.targets]
