"""De simulatie: wereld + voertuig + apparaten, in één vaste-stap lus (200 Hz, real-time).

Draaiboek per stap:
  1. steppers lopen naar hun doel (stuur-emulator)
  2. VESC's volgen hun toerental, met de last uit het voertuig
  3. het voertuig rijdt (kinematica + terrein)
  4. elke 40 ms een GNSS-epoch, elke 20 ms de VESC-statusframes

De apparaten luisteren op TCP-poorten (zie devices/); de robotsoftware praat daar via zijn gewone drivers mee.
De UI leest `snapshot()`; storingen gaan via `set_faults()`.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

from openagbot_fieldsim.devices.can_link import CanHub
from openagbot_fieldsim.devices.gnss import GnssEmulator, GnssTruth
from openagbot_fieldsim.devices.steering import SteeringEmulator
from openagbot_fieldsim.devices.vesc import VescEmulator, WheelLoad, arbitration
from openagbot_fieldsim.geo import heading_deg
from openagbot_fieldsim.vehicle.model import VehicleModel
from openagbot_fieldsim.vehicle.params import VehicleParams
from openagbot_fieldsim.world.terrain import build_terrain

log = logging.getLogger(__name__)

DT = 0.005
VESC_STATUS_PERIOD = 0.02


@dataclass
class Ports:
    gnss: int = 5101
    steering: int = 5102
    can: int = 5103


class Battery:
    """14S Li-ion (EGO 56 V, 5 Ah): rustspanning uit de lading, spanningsval over de inwendige weerstand."""

    def __init__(self, cells: int, capacity_ah: float, resistance: float, soc: float = 0.9):
        self.cells, self.capacity_ah, self.r, self.soc = cells, capacity_ah, resistance, soc
        self.current = 0.0

    def voltage(self) -> float:
        cell = 3.3 + 0.85 * max(0.0, min(1.0, self.soc))
        return self.cells * cell - self.r * self.current

    def step(self, current_a: float, dt: float):
        self.current = current_a
        self.soc -= current_a * dt / 3600.0 / self.capacity_ah


class Simulation:
    def __init__(self, world, agbot_config: dict, ports: Ports | None = None, seed: int = 1):
        self.world = world
        self.agbot_config = agbot_config
        self.params = VehicleParams.from_agbot_config(agbot_config)
        self.terrain = build_terrain(world)
        self.vehicle = VehicleModel(self.params, self.terrain, world)
        ports = ports or Ports()
        p = self.params
        self.gnss = GnssEmulator(ports.gnss, p.heading_offset_deg, p.pitch_offset_deg, seed=seed)
        self.steering = SteeringEmulator(ports.steering, p.extras.steps_per_degree)
        vesc_cfg = agbot_config.get("vesc", {})
        self.vescs = {
            p.left_id: VescEmulator(p.left_id, "links", p.erpm_per_mps, p.extras.vesc_tau_s, p.extras.vesc_min_erpm,
                                    p.extras.vesc_timeout_s, max_erpm=float(vesc_cfg.get("max_erpm", 3700)) * 1.2),
            p.right_id: VescEmulator(p.right_id, "rechts", p.erpm_per_mps, p.extras.vesc_tau_s,
                                     p.extras.vesc_min_erpm, p.extras.vesc_timeout_s,
                                     max_erpm=float(vesc_cfg.get("max_erpm", 3700)) * 1.2),
        }
        self.can = CanHub(ports.can, self._on_can_frame)
        self.battery = Battery(p.battery_cells, p.battery_capacity_ah, p.battery_resistance_ohm)

        self._lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None
        self.t = 0.0                     # simulatietijd (s)
        self.paused = False
        self.events: deque = deque(maxlen=200)
        self.trail: deque = deque(maxlen=20000)
        self._next_status = 0.0
        self._prev_ant1 = None
        self._ant1_vel = (0.0, 0.0)
        self._booted = False
        self.loop_overrun = 0
        self.implement = None            # openagbot_fieldsim.implements.state.ImplementState of None
        self.geofence = None             # missions.safety.Geofence: bewaakt verboden gebieden en sloten
        self.on_geofence = None          # callback(overtreding) bij een stop (SimApp stopt dan de sim-robot)
        self.geofence_alarm: dict | None = None
        self._gf_tick = 0
        try:
            from openagbot_fieldsim.missions.safety import Geofence
            self.geofence = Geofence(world)
        except Exception:                # noqa: BLE001 - zonder shapely draait de sim zonder bewaking
            log.exception("geofence niet beschikbaar")
        self.reset_pose()

    # ------------------------------------------------------------------ besturing
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="sim-loop", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        for dev in (self.gnss, self.steering, self.can):
            dev.close()

    @property
    def ports(self) -> Ports:
        return Ports(self.gnss.port.port, self.steering.port.port, self.can.port.port)

    def reset_pose(self, x: float | None = None, y: float | None = None, heading: float | None = None):
        with self._lock:
            sx, sy, sh = self.world.start_xy_heading()
            self.vehicle.place(sx if x is None else x, sy if y is None else y, sh if heading is None else heading)
            for v in self.vescs.values():
                v.erpm = 0.0
            self._prev_ant1 = None
            self.trail.clear()
            self.geofence_alarm = None
            if self.geofence is not None:
                # wat de robot op de nieuwe plek al raakt telt niet als 'erin gereden' (hij is daar neergezet)
                self.geofence.reset()
                self.geofence.check(self._footprint())
            self._event("info", "robot geplaatst")

    def set_implement(self, implement_id: str | None):
        """Aanbouwdeel kiezen (id uit openagbot_fieldsim.implements.catalog) of None voor geen."""
        from openagbot_fieldsim.implements.catalog import get
        from openagbot_fieldsim.implements.state import ImplementState
        with self._lock:
            self.implement = None if not implement_id else ImplementState(get(implement_id), self.params.wheelbase_m)
            if self.geofence is not None:      # een breder werktuig kan al in een gebied steken: niet als alarm
                self.geofence.check(self._footprint())
            self._event("info", f"werktuig: {implement_id or 'geen'}")
            return self.implement

    def set_faults(self, **kw):
        """Storingen en GNSS-instellingen live aanpassen (zie api: POST /api/sim/faults)."""
        with self._lock:
            g = self.gnss.settings
            for key in ("fix", "heading_valid", "stream_enabled", "correction_age_s", "noise_scale", "mount_errors"):
                if key in kw and kw[key] is not None:
                    setattr(g, key, kw[key])
            if kw.get("steering_responsive") is not None:
                self.steering.responsive = bool(kw["steering_responsive"])
            if kw.get("steering_stalled") is not None:
                for m in self.steering.motors:
                    m.stalled = bool(kw["steering_stalled"])
            if kw.get("vesc_online") is not None:
                for v in self.vescs.values():
                    v.online = bool(kw["vesc_online"])
            if kw.get("vesc_fault_code") is not None:
                for v in self.vescs.values():
                    v.fault_code = int(kw["vesc_fault_code"])
            if kw.get("clear_stuck"):
                self.vehicle.s.stuck = None
            self._event("fault", ", ".join(f"{k}={v}" for k, v in kw.items() if v is not None))

    def set_geofence(self, date=None, mission_dike_mode: str | None = None):
        """Bewaking opnieuw opbouwen voor een missiedatum (nesten in hun periode) of een andere dijkmodus."""
        from openagbot_fieldsim.missions.safety import Geofence
        with self._lock:
            self.geofence = Geofence(self.world, date, mission_dike_mode)
            self.geofence.check(self._footprint())
            self.geofence_alarm = None

    def _footprint(self):
        from openagbot_fieldsim.missions.safety import footprint_points
        s = self.vehicle.s
        return footprint_points(s.x, s.y, s.heading, self.params.wheelbase_m, self.params.track_width_m,
                                self.implement)

    def _check_geofence(self):
        """Hoeken van robot en werktuig tegen de verboden gebieden; bij een stop-gebied pauzeert de sim."""
        new = self.geofence.check(self._footprint())
        for v in new:
            what = f"{v['label']}{' ' + v['name'] if v['name'] and v['name'] != v['label'] else ''}"
            if v["level"] == "stop":
                self.geofence_alarm = {**v, "t": round(self.t, 2), "text": what}
                self._event("geofence", f"GESTOPT: robot of werktuig komt in {what}")
                if self.geofence.settings.get("pause", True):
                    self.paused = True
                if self.on_geofence is not None:
                    try:
                        self.on_geofence(v)
                    except Exception:        # noqa: BLE001
                        log.exception("on_geofence")
            else:
                self._event("waarschuwing", f"robot of werktuig raakt {what}")

    def _event(self, kind: str, text: str):
        self.events.append({"t": round(self.t, 2), "wall": time.time(), "kind": kind, "text": text})

    # ------------------------------------------------------------------ CAN
    def _on_can_frame(self, arb: int, data: bytes):
        cmd, target = arb >> 8, arb & 0xFF
        vesc = self.vescs.get(target)
        if vesc is None:
            return
        with self._lock:
            replies = vesc.handle(cmd, data, self.t)
        for rcmd, rtarget, rdata in replies:
            self.can.send(arbitration(rcmd, rtarget), rdata)

    # ------------------------------------------------------------------ lus
    def _loop(self):
        next_t = time.perf_counter()
        while self._running:
            now = time.perf_counter()
            if now < next_t:
                time.sleep(min(next_t - now, 0.004))
                continue
            steps = 0
            while next_t <= now and steps < 20:
                if not self.paused:
                    self._step(DT)
                next_t += DT
                steps += 1
            if next_t < now - 0.5:          # te ver achter (debugger, slaapstand): niet inhalen
                self.loop_overrun += 1
                next_t = now

    def _step(self, dt: float):
        with self._lock:
            self.t += dt
            veh = self.vehicle
            self.steering.step(dt)
            # Motorframe -> voertuigframe met dezelfde vlaggen als de robot
            ml, mr = self.steering.angles()
            wl = -ml if self.params.invert_left else ml
            wr = -mr if self.params.invert_right else mr
            force = veh.drive_force()
            blocked = veh.s.stuck is not None
            vbat = self.battery.voltage()
            coast = 9.81 * (self.params.extras.rolling_resistance + 0.05)
            for v in self.vescs.values():
                v.step(dt, self.t, WheelLoad(force, blocked), vbat, coast)
            left = self.vescs[self.params.left_id]
            right = self.vescs[self.params.right_id]
            was_stuck = veh.s.stuck
            veh.step(dt, left.speed_mps, right.speed_mps, wl, wr)
            if veh.s.stuck and not was_stuck:
                self._event("hazard", veh.s.stuck)
            tool_w = self.implement.step(dt, veh.s) if self.implement is not None else 0.0
            self._gf_tick += 1
            if self.geofence is not None and self._gf_tick >= 10:      # 20 Hz: hooguit 7 cm rijden per controle
                self._gf_tick = 0
                self._check_geofence()
            self.battery.step(sum(max(0.0, v.input_current) for v in self.vescs.values()) + 0.6
                              + tool_w / max(30.0, vbat), dt)
            if not self.trail or math.hypot(veh.s.x - self.trail[-1][0], veh.s.y - self.trail[-1][1]) > 0.2:
                self.trail.append((round(veh.s.x, 3), round(veh.s.y, 3)))
            wall = time.time()
            if self.gnss.due(wall):
                self.gnss.emit(self._gnss_truth(wall))
            if self.t >= self._next_status:
                self._next_status = self.t + VESC_STATUS_PERIOD
                self._send_vesc_status(vbat)

    def _send_vesc_status(self, vbat: float):
        if not self.can.port.connected:
            self._booted = False
            return
        frames = []
        if not self._booted:
            self._booted = True
            frames += [v.boot_frame() for v in self.vescs.values() if v.online]
        for v in self.vescs.values():
            frames += v.status_frames(vbat)
        for cmd, node, data in frames:
            self.can.send(arbitration(cmd, node), data)

    def _gnss_truth(self, wall: float) -> GnssTruth:
        a1, a2 = self.vehicle.antennas()
        frame = self.world.frame
        lat, lon = frame.to_latlon(a1[0], a1[1])
        if self._prev_ant1 is not None:
            pt, px, py = self._prev_ant1
            dtg = max(1e-3, self.t - pt)
            vx, vy = (a1[0] - px) / dtg, (a1[1] - py) / dtg
            k = 0.5
            self._ant1_vel = (self._ant1_vel[0] + k * (vx - self._ant1_vel[0]),
                              self._ant1_vel[1] + k * (vy - self._ant1_vel[1]))
        self._prev_ant1 = (self.t, a1[0], a1[1])
        vx, vy = self._ant1_vel
        speed = math.hypot(vx, vy)
        cog = heading_deg(vx, vy) if speed > 0.02 else self.vehicle.s.heading
        return GnssTruth(t=wall, ant1_lat=lat, ant1_lon=lon, ant1_alt=a1[2] + frame.h0,
                         rel_n=a2[1] - a1[1], rel_e=a2[0] - a1[0], rel_d=-(a2[2] - a1[2]),
                         speed_mps=speed, cog_deg=cog)

    # ------------------------------------------------------------------ uitlezen
    def snapshot(self, trail_from: int = 0, strip_from: int = 0) -> dict:
        with self._lock:
            s = self.vehicle.s
            lat, lon = self.world.frame.to_latlon(s.x, s.y)
            ml, mr = self.steering.angles()
            tl, tr = self.steering.targets()
            inv = (-1 if self.params.invert_left else 1, -1 if self.params.invert_right else 1)
            trail = list(self.trail)
            return {
                "t": round(self.t, 3),
                "paused": self.paused,
                "robot": {"x": s.x, "y": s.y, "z": s.z, "heading": s.heading, "pitch": s.pitch, "roll": s.roll,
                          "v": s.v, "speed_kmh": s.v * 3.6, "yaw_rate": s.yaw_rate, "lat": lat, "lon": lon,
                          "wheel_left_deg": s.wheel_left_deg, "wheel_right_deg": s.wheel_right_deg,
                          "target_left_deg": tl * inv[0], "target_right_deg": tr * inv[1],
                          "spin": dict(s.spin), "odometer_m": s.odometer_m, "surface": s.surface,
                          "stuck": s.stuck},
                "gnss": {**self.gnss.last, "settings": vars(self.gnss.settings).copy(),
                         "connected": self.gnss.port.connected, "epochs": self.gnss.epochs},
                "steering": {"connected": self.steering.port.connected, "commands": self.steering.commands,
                             "motor_deg": [ml, mr], "responsive": self.steering.responsive},
                "vesc": {str(k): {"erpm": v.erpm, "erpm_cmd": v.erpm_cmd, "mode": v.mode,
                                  "current_a": v.motor_current, "online": v.online, "fault": v.fault_code}
                         for k, v in self.vescs.items()},
                "can": {"connected": self.can.port.connected, "frames_in": self.can.frames_in,
                        "frames_out": self.can.frames_out},
                "battery": {"v": self.battery.voltage(), "soc": self.battery.soc, "a": self.battery.current},
                "trail": {"from": min(trail_from, len(trail)), "points": trail[trail_from:],
                          "total": len(trail)},
                "events": list(self.events)[-20:],
                "implement": self.implement.snapshot(strip_from) if self.implement is not None else None,
                "geofence": None if self.geofence is None else {**self.geofence.state(),
                                                                "alarm": self.geofence_alarm},
            }
