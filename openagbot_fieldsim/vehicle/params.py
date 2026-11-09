"""Maten en instellingen van de robot, gelezen uit dezelfde OpenAgbot-config als de robot zelf gebruikt.

Eén bron: wielbasis, spoorbreedte, antenneposities, eRPM-omrekening en stuurrichting komen uit
`data/config.sim.json` van OpenAgbot. Wat daar niet in staat (antenne-baseline, massa, stuurmechaniek) staat hier
als `SimExtras`, met de bron erbij. Zo hoeft er nooit een getal dubbel bijgehouden te worden.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimExtras:
    """Waarden die de robotsoftware niet nodig heeft maar de fysica wel. Geschat, niet gemeten (zie README)."""

    # Dubbele antenne: GPS2 (slave, voor) ligt baseline_m recht voor GPS1. 0,997 m uit de ritlog van 2 nov 2026
    # (kolom Baseline_m).
    baseline_m: float = 0.997
    # Stuurmechaniek, gelijk aan de firmware (arduino_controlling_dual_steppercontroller.cpp)
    steps_per_degree: float = 3200.0 * 5.0 / 360.0
    # Reactie van de VESC-snelheidsregelaar op een nieuw toerental (eerste orde)
    vesc_tau_s: float = 0.15
    # Onder deze eRPM houdt de VESC het wiel niet aan het draaien (s_pid_min_erpm)
    vesc_min_erpm: float = 900.0
    # VESC App Settings timeout: zonder commando's binnen deze tijd gaan de motoren uit
    vesc_timeout_s: float = 1.0
    # Slip: fractie van de theoretische draaisnelheid die de robot in een bocht haalt op 'grip 1'
    yaw_efficiency: float = 0.97
    # Massa voor stroom en verbruik (robot zonder werktuig), geschat
    mass_kg: float = 180.0
    rolling_resistance: float = 0.06
    motor_kv_amp_per_nm: float = 0.0  # niet gebruikt in v1; stroom komt uit een eenvoudig krachtmodel


@dataclass
class VehicleParams:
    wheelbase_m: float = 1.20
    track_width_m: float = 1.00
    max_wheel_angle_deg: float = 30.0
    invert_left: bool = True
    invert_right: bool = True
    # GNSS: regelpunt (midden achteras) t.o.v. antenne GPS1, vooruit/rechts (OpenAgbot-conventie)
    antenna_offset_forward_m: float = -0.10
    antenna_offset_right_m: float = 0.70
    antenna_height_m: float = 0.70
    heading_offset_deg: float = 0.0
    pitch_offset_deg: float = 0.0
    # Aandrijving
    pole_pairs: int = 10
    gear_ratio: float = 5.0
    wheel_circumference_m: float = 1.277
    left_id: int = 1
    right_id: int = 2
    battery_cells: int = 14
    battery_capacity_ah: float = 5.0
    battery_resistance_ohm: float = 0.2
    extras: SimExtras = field(default_factory=SimExtras)

    @property
    def erpm_per_mps(self) -> float:
        return self.pole_pairs * self.gear_ratio * 60.0 / self.wheel_circumference_m

    @property
    def tacho_per_m(self) -> float:
        return 6.0 * self.pole_pairs * self.gear_ratio / self.wheel_circumference_m

    @property
    def antenna1_from_axle(self) -> tuple[float, float]:
        """Positie van antenne GPS1 t.o.v. het midden van de achteras (vooruit, rechts) = min de config-offset."""
        return -self.antenna_offset_forward_m, -self.antenna_offset_right_m

    @classmethod
    def from_agbot_config(cls, cfg: dict) -> "VehicleParams":
        v, s, g, e, b = (cfg.get(k, {}) for k in ("vehicle", "steering", "gnss", "vesc", "battery"))
        return cls(
            wheelbase_m=float(v.get("wheelbase_m", 1.2)),
            track_width_m=float(v.get("track_width_m", 1.0)),
            max_wheel_angle_deg=float(s.get("max_angle_degrees", 30.0)),
            invert_left=bool(s.get("invert_left", s.get("invert_direction", False))),
            invert_right=bool(s.get("invert_right", s.get("invert_direction", False))),
            antenna_offset_forward_m=float(g.get("antenna_offset_forward_m", 0.0)),
            antenna_offset_right_m=float(g.get("antenna_offset_right_m", 0.0)),
            antenna_height_m=float(g.get("antenna_height_m", 0.7)) or 0.7,
            heading_offset_deg=float(g.get("heading_offset_deg", 0.0)),
            pitch_offset_deg=float(g.get("pitch_offset_deg", 0.0)),
            pole_pairs=int(e.get("pole_pairs", 10)),
            gear_ratio=float(e.get("gear_ratio", 5.0)),
            wheel_circumference_m=float(e.get("wheel_circumference_m", 1.277)),
            left_id=int(e.get("left_id", 1)),
            right_id=int(e.get("right_id", 2)),
            battery_cells=int(b.get("cells", 14)),
            battery_capacity_ah=float(b.get("capacity_ah", 5.0)),
            battery_resistance_ohm=float(b.get("internal_resistance_ohm", 0.2)),
        )
