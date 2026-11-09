"""Emulator van een VESC (Classic 100V, firmware 6.x) op de CAN-bus.

Ontvangt SET_RPM, SET_CURRENT_BRAKE en PROCESS_SHORT_BUFFER (COMM_GET_VALUES_SELECTIVE met de foutcode), en zendt
STATUS 1 t/m 5 op 50 Hz plus NOTIFY_BOOT bij het opstarten. Het wiel volgt het gevraagde toerental met een eerste-
orde reactie; onder s_pid_min_erpm en na de app-timeout laat de VESC het wiel uitrollen, zoals de echte.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

# CAN_PACKET_ID uit bldc/datatypes.h (zelfde getallen als app/hardware/vesc_can.py)
SET_CURRENT_BRAKE = 2
SET_RPM = 3
PROCESS_SHORT_BUFFER = 8
STATUS_1 = 9
STATUS_2 = 14
STATUS_3 = 15
STATUS_4 = 16
STATUS_5 = 27
NOTIFY_BOOT = 57
COMM_GET_VALUES_SELECTIVE = 50
FAULT_CODE_MASK = 1 << 15


@dataclass
class WheelLoad:
    """Wat de fysica per wiel teruggeeft: benodigde trekkracht (N) en of het wiel vrij kan draaien."""
    force_n: float = 0.0
    blocked: bool = False


class VescEmulator:
    def __init__(self, vesc_id: int, name: str, erpm_per_mps: float, tau_s: float = 0.15,
                 min_erpm: float = 900.0, timeout_s: float = 1.0, max_erpm: float = 6000.0):
        self.id = vesc_id
        self.name = name
        self.erpm_per_mps = erpm_per_mps
        self.tau = tau_s
        self.min_erpm = min_erpm
        self.timeout_s = timeout_s
        self.max_erpm = max_erpm
        self.erpm = 0.0               # werkelijk toerental van de motor
        self.erpm_cmd = 0.0
        self.mode = "release"         # rpm | brake | release
        self.brake_a = 0.0
        self.last_cmd_t = -1e9
        self.tacho = 0.0
        self.motor_current = 0.0
        self.input_current = 0.0
        self.duty = 0.0
        self.ah = self.ah_charged = self.wh = self.wh_charged = 0.0
        self.temp_fet = 22.0
        self.temp_motor = 25.0
        self.fault_code = 0
        self.online = True            # storing: VESC zendt niets en reageert niet
        self.booted = False

    # ---------------------------------------------------------------- ontvangen
    def handle(self, cmd: int, data: bytes, now: float):
        """Frame aan deze VESC; geeft een lijst (cmd, data) antwoorden terug."""
        if not self.online:
            return []
        if cmd == SET_RPM and len(data) >= 4:
            self.erpm_cmd = float(struct.unpack(">i", data[:4])[0])
            self.mode = "rpm"
            self.last_cmd_t = now
        elif cmd == SET_CURRENT_BRAKE and len(data) >= 4:
            self.brake_a = struct.unpack(">i", data[:4])[0] / 1000.0
            self.mode = "brake"
            self.last_cmd_t = now
        elif cmd == PROCESS_SHORT_BUFFER and len(data) >= 3:
            sender, _send, comm = data[0], data[1], data[2]
            if comm == COMM_GET_VALUES_SELECTIVE and len(data) >= 7:
                mask = struct.unpack(">I", data[3:7])[0]
                if mask & FAULT_CODE_MASK:
                    reply = bytes((self.id, 1, COMM_GET_VALUES_SELECTIVE)) + struct.pack(">IB", FAULT_CODE_MASK,
                                                                                         self.fault_code)
                    return [(PROCESS_SHORT_BUFFER, sender, reply)]
        return []

    # ---------------------------------------------------------------- fysica
    def target_erpm(self, now: float) -> float | None:
        """Toerental dat de regelaar nastreeft, of None als het wiel vrij uitrolt."""
        if now - self.last_cmd_t > self.timeout_s:
            self.mode = "release"
        if self.mode == "rpm" and abs(self.erpm_cmd) >= self.min_erpm:
            return max(-self.max_erpm, min(self.max_erpm, self.erpm_cmd))
        if self.mode == "brake":
            return 0.0
        return None

    def step(self, dt: float, now: float, load: WheelLoad, battery_v: float, coast_decel_mps2: float) -> None:
        target = self.target_erpm(now)
        prev = self.erpm
        if load.blocked:
            self.erpm = 0.0
        elif target is None:
            # Uitrollen: rolweerstand remt af
            dv = coast_decel_mps2 * dt * self.erpm_per_mps
            self.erpm = 0.0 if abs(self.erpm) <= dv else self.erpm - math.copysign(dv, self.erpm)
        else:
            a = 1.0 - math.exp(-dt / self.tau)
            self.erpm += (target - self.erpm) * a
        accel_erpm = (self.erpm - prev) / dt if dt > 0 else 0.0
        # Stroom: kracht per wiel naar motorstroom (14S-hubmotor, ca. 9 A bij 150 N, geschat)
        drive = target is not None and not load.blocked
        self.motor_current = (load.force_n / 16.7) if drive else 0.0
        if load.blocked and target:
            self.motor_current = 25.0      # vastgelopen: stroom loopt op, toerental blijft 0
        v = max(battery_v, 1.0)
        self.duty = max(-1.0, min(1.0, self.erpm / (95.0 * v)))
        self.input_current = self.motor_current * abs(self.duty)
        self.tacho += self.erpm / 60.0 * 6.0 * dt
        if self.input_current >= 0:
            self.ah += self.input_current * dt / 3600.0
            self.wh += self.input_current * v * dt / 3600.0
        else:
            self.ah_charged += -self.input_current * dt / 3600.0
            self.wh_charged += -self.input_current * v * dt / 3600.0
        # Temperatuur FET: langzaam mee met de stroom
        self.temp_fet += ((22.0 + 1.5 * abs(self.motor_current)) - self.temp_fet) * dt / 120.0
        del accel_erpm

    @property
    def speed_mps(self) -> float:
        return self.erpm / self.erpm_per_mps

    # ---------------------------------------------------------------- zenden
    def status_frames(self, battery_v: float):
        if not self.online:
            return []
        i = self.id
        return [
            (STATUS_1, i, struct.pack(">ihh", int(round(self.erpm)), int(round(self.motor_current * 10)),
                                      int(round(self.duty * 1000)))),
            (STATUS_2, i, struct.pack(">ii", int(self.ah * 1e4), int(self.ah_charged * 1e4))),
            (STATUS_3, i, struct.pack(">ii", int(self.wh * 1e4), int(self.wh_charged * 1e4))),
            (STATUS_4, i, struct.pack(">hhhh", int(self.temp_fet * 10), int(self.temp_motor * 10),
                                      int(round(self.input_current * 10)), 0)),
            (STATUS_5, i, struct.pack(">ihxx", int(self.tacho), int(round(battery_v * 10)))),
        ]

    def boot_frame(self):
        self.booted = True
        return (NOTIFY_BOOT, self.id, b"60_mk5\0")


def arbitration(cmd: int, node_id: int) -> int:
    return (cmd << 8) | (node_id & 0xFF)
