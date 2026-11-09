"""Voertuigmodel van de Bruut (2WD): gestuurde voorwielen (Ackermann, twee steppers), aangedreven achterwielen
(twee VESC-hubmotoren), op een terrein met reliëf.

Kinematisch met slip: bij 1-5 km/h is dat genoeg, en de parameters (yaw_efficiency, grip) zijn te ijken op de
ritlogs. Staat in lokale meters (x = oost, y = noord), koers in kompasgraden zoals de robot (0 = noord, rechtsom +),
stuurhoek + = naar rechts (conventie van vehicle_controller.py).

Het regelpunt is het midden van de achteras (daar rekent OpenAgbot vanaf).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from openagbot_fieldsim.vehicle.params import VehicleParams

G = 9.81


@dataclass
class VehicleState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    heading: float = 0.0          # kompasgraden
    pitch: float = 0.0            # graden, + = neus omhoog
    roll: float = 0.0             # graden, + = rechterkant omlaag
    v: float = 0.0                # m/s langs de neus (midden achteras)
    yaw_rate: float = 0.0         # graden/s, + = rechtsom
    wheel_left_deg: float = 0.0   # stuurhoek voorwielen (voertuigframe)
    wheel_right_deg: float = 0.0
    spin: dict = field(default_factory=lambda: {"RL": 0.0, "RR": 0.0, "FL": 0.0, "FR": 0.0})  # wielrotatie (rad)
    odometer_m: float = 0.0
    surface: str = "grass"
    stuck: str | None = None      # reden als de robot vastzit (sloot, gebouw)


def body_axes(heading: float, pitch: float, roll: float):
    """Eenheidsvectoren vooruit, rechts en op van de robot in ENU."""
    h, p, r = math.radians(heading), math.radians(pitch), math.radians(roll)
    f = (math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p))
    r0 = (math.cos(h), -math.sin(h), 0.0)
    up0 = (r0[1] * f[2] - r0[2] * f[1], r0[2] * f[0] - r0[0] * f[2], r0[0] * f[1] - r0[1] * f[0])
    cr, sr = math.cos(r), math.sin(r)
    right = tuple(r0[i] * cr - up0[i] * sr for i in range(3))
    up = tuple(up0[i] * cr + r0[i] * sr for i in range(3))
    return f, right, up


class VehicleModel:
    def __init__(self, params: VehicleParams, terrain, world=None):
        self.p = params
        self.terrain = terrain
        self.world = world
        self.s = VehicleState()
        self._rad = params.wheel_circumference_m / (2.0 * math.pi)

    def place(self, x: float, y: float, heading: float) -> None:
        self.s = VehicleState(x=x, y=y, heading=heading % 360.0)
        self._update_attitude()

    # ------------------------------------------------------------------ één stap
    def step(self, dt: float, v_left: float, v_right: float, wheel_left_deg: float, wheel_right_deg: float) -> None:
        """v_left/right: omtreksnelheid van de achterwielen (m/s) uit de VESC's; stuurhoeken uit de steppers."""
        s, p = self.s, self.p
        s.wheel_left_deg, s.wheel_right_deg = wheel_left_deg, wheel_right_deg
        grip = self._grip()
        v = 0.5 * (v_left + v_right)
        if s.stuck:
            v = 0.0
        L, T = p.wheelbase_m, p.track_width_m
        # Kromming uit elk voorwiel apart (wiel op dwarspositie y: tan(d) = L / (R - y)), dan het gemiddelde
        kappas = []
        for ang, yw in ((wheel_left_deg, -T / 2.0), (wheel_right_deg, T / 2.0)):
            t = math.tan(math.radians(ang))
            kappas.append(t / (L + yw * t) if abs(L + yw * t) > 1e-6 else 0.0)
        kappa = 0.5 * (kappas[0] + kappas[1])
        yaw_front = v * kappa                                   # rad/s, + = rechtsom
        yaw_rear = (v_left - v_right) / T                       # wat de achterwielen samen opleggen
        yaw = 0.85 * yaw_front + 0.15 * yaw_rear
        yaw *= p.extras.yaw_efficiency * (0.6 + 0.4 * grip)
        s.yaw_rate = math.degrees(yaw)
        hdg = s.heading + 0.5 * s.yaw_rate * dt
        # Op een helling is de horizontale verplaatsing korter
        dh = v * dt * math.cos(math.radians(s.pitch))
        s.x += dh * math.sin(math.radians(hdg))
        s.y += dh * math.cos(math.radians(hdg))
        s.heading = (s.heading + s.yaw_rate * dt) % 360.0
        s.v = v
        s.odometer_m += abs(v * dt)
        # Wielen draaien (voor de animatie): achter uit de VESC, voor uit de afgelegde weg van elk voorwiel
        s.spin["RL"] += v_left * dt / self._rad
        s.spin["RR"] += v_right * dt / self._rad
        for tag, yw in (("FL", -T / 2.0), ("FR", T / 2.0)):
            # Afstand voorwiel tot het draaipunt gedeeld door die van het midden van de achteras
            factor = 1.0 if abs(kappa) < 1e-6 else math.hypot(L, 1.0 / kappa - yw) * abs(kappa)
            s.spin[tag] += v * factor * dt / self._rad
        self._update_attitude()
        self._check_hazards()

    # ------------------------------------------------------------------ terrein
    def _grip(self) -> float:
        if self.world is None:
            return 1.0
        from openagbot_fieldsim.world.model import SURFACE_KINDS
        kind = self.s.surface
        return 0.9 if kind == "field" else SURFACE_KINDS.get(kind, {"grip": 0.9})["grip"]

    def wheel_contacts(self):
        """(tag, x, y) van de vier wielen op de grond (voor = +wielbasis)."""
        s, p = self.s, self.p
        hx, hy = math.sin(math.radians(s.heading)), math.cos(math.radians(s.heading))
        rx, ry = hy, -hx
        T, L = p.track_width_m / 2.0, p.wheelbase_m
        out = []
        for tag, fwd, right in (("RL", 0.0, -T), ("RR", 0.0, T), ("FL", L, -T), ("FR", L, T)):
            out.append((tag, s.x + hx * fwd + rx * right, s.y + hy * fwd + ry * right))
        return out

    def _update_attitude(self):
        s = self.s
        h = {tag: self.terrain.height(x, y) for tag, x, y in self.wheel_contacts()}
        rear = 0.5 * (h["RL"] + h["RR"])
        front = 0.5 * (h["FL"] + h["FR"])
        left = 0.5 * (h["RL"] + h["FL"])
        right = 0.5 * (h["RR"] + h["FR"])
        s.z = rear
        s.pitch = math.degrees(math.atan2(front - rear, self.p.wheelbase_m))
        s.roll = math.degrees(math.atan2(left - right, self.p.track_width_m))
        if self.world is not None:
            s.surface = self.world.surface_at(s.x, s.y)

    def _check_hazards(self):
        s = self.s
        if s.stuck or self.world is None:
            return
        for tag, x, y in self.wheel_contacts():
            kind = self.world.surface_at(x, y)
            if kind == "water":
                s.stuck = f"wiel {tag} in de sloot"
                s.v = 0.0
                return
        for b in self.world.buildings:
            from openagbot_fieldsim.world.geometry import point_in_polygon
            for tag, x, y in self.wheel_contacts():
                if point_in_polygon(x, y, b.xy):
                    s.stuck = f"tegen {b.name}"
                    s.v = 0.0
                    return

    # ------------------------------------------------------------------ GNSS
    def body_point(self, forward: float, right: float, up: float) -> tuple[float, float, float]:
        s = self.s
        f, r, u = body_axes(s.heading, s.pitch, s.roll)
        return (s.x + f[0] * forward + r[0] * right + u[0] * up,
                s.y + f[1] * forward + r[1] * right + u[1] * up,
                s.z + f[2] * forward + r[2] * right + u[2] * up)

    def antennas(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """ENU-posities van antenne GPS1 (master, achter) en GPS2 (slave, baseline_m ervoor)."""
        fwd, right = self.p.antenna1_from_axle
        h = self.p.antenna_height_m
        a1 = self.body_point(fwd, right, h)
        a2 = self.body_point(fwd + self.p.extras.baseline_m, right, h)
        return a1, a2

    def drive_force(self) -> float:
        """Benodigde trekkracht per achterwiel (N): rolweerstand plus helling."""
        m = self.p.extras.mass_kg
        return 0.5 * m * G * (self.p.extras.rolling_resistance + math.sin(math.radians(self.s.pitch)))
