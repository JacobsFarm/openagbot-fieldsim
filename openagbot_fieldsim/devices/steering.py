"""Emulator van de stuur-Arduino (UNO R4, firmware arduino_controlling_dual_steppercontroller.cpp, ID 2.0).

Zelfde regelprotocol (A:, AL:, AR:, J:, E:, EL:, ER:, Z:, P:?, S:?, I:?, V:, M:), dezelfde antwoorden (OK:, ERR:,
POS:, STA:, ID:) en dezelfde bewegingswet: stappen met een lineaire optrek/afrem-ramp tussen startStepDelayUs en
minStepDelayUs. Hoeken zijn in het MOTORframe; de omkering per wiel (invert_left/right) doet OpenAgbot, en de
fysica rekent ze terug met dezelfde vlaggen uit de config.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from openagbot_fieldsim.devices.transport import TcpPort

FIRMWARE_ID = "ID:OPENAGBOT-STEER,2.0,MOTORS=2"


@dataclass
class _Motor:
    current: int = 0
    target: int = 0
    enabled: bool = True
    into_move: int = 0
    t_acc_us: float = 0.0
    stalled: bool = False      # storing: motor volgt niet (stapverlies)


class SteeringEmulator:
    def __init__(self, port: int, steps_per_degree: float = 3200.0 * 5.0 / 360.0):
        self.spd = steps_per_degree
        self.min_delay_us = 300
        self.start_delay_us = 1200
        self.ramp_steps = 400
        self.max_angle = 50.0
        self.motors = [_Motor(), _Motor()]
        self.commands = 0
        self.responsive = True    # storing: Arduino antwoordt niet meer
        self._lock = threading.Lock()
        self._rx = bytearray()
        self.port = TcpPort("steering", port=port, on_data=self._on_data, on_connect=self._on_connect)

    # ------------------------------------------------------------------ hoeken (motorframe, graden)
    def angles(self) -> tuple[float, float]:
        return self.motors[0].current / self.spd, self.motors[1].current / self.spd

    def targets(self) -> tuple[float, float]:
        return self.motors[0].target / self.spd, self.motors[1].target / self.spd

    # ------------------------------------------------------------------ beweging
    def step(self, dt: float) -> None:
        with self._lock:
            for m in self.motors:
                if not m.enabled or m.stalled:
                    continue
                if m.current == m.target:
                    m.into_move = 0
                    m.t_acc_us = 0.0
                    continue
                m.t_acc_us += dt * 1e6
                while m.current != m.target:
                    interval = self._interval(m)
                    if m.t_acc_us < interval:
                        break
                    m.t_acc_us -= interval
                    m.current += 1 if m.target > m.current else -1
                    m.into_move = min(m.into_move + 1, 1_000_000)
                if m.current == m.target:
                    m.t_acc_us = 0.0

    def _interval(self, m: _Motor) -> float:
        if self.ramp_steps <= 0:
            return self.min_delay_us
        remaining = abs(m.target - m.current)
        phase = min(m.into_move, remaining)
        if phase >= self.ramp_steps:
            return self.min_delay_us
        span = self.start_delay_us - self.min_delay_us
        if span <= 0:
            return self.min_delay_us
        return self.start_delay_us - span * phase / self.ramp_steps

    # ------------------------------------------------------------------ serieel
    def _on_connect(self):
        # Een echte UNO R4 herstart niet bij het openen van de USB-poort; hij meldt READY bij het opstarten.
        self.port.send(b"READY\r\n")

    def _on_data(self, data: bytes):
        self._rx += data
        while True:
            i = self._rx.find(b"\n")
            if i < 0:
                if len(self._rx) > 200:
                    self._rx.clear()
                    self._reply("ERR:LINE_TOO_LONG")
                return
            line = bytes(self._rx[:i]).decode("ascii", "replace").replace("\r", "").strip()
            del self._rx[:i + 1]
            if line:
                self.commands += 1
                if self.responsive:
                    with self._lock:
                        reply = self.process(line)
                    self._reply(reply)

    def _reply(self, text: str):
        self.port.send((text + "\r\n").encode("ascii"))

    def _set_target(self, m: _Motor, deg: float) -> bool:
        clamped = False
        if deg > self.max_angle:
            deg, clamped = self.max_angle, True
        elif deg < -self.max_angle:
            deg, clamped = -self.max_angle, True
        m.target = round(deg * self.spd)
        return clamped

    @staticmethod
    def _floats(arg: str, max_count: int):
        vals = []
        for tok in arg.split(","):
            if tok == "":
                continue
            if len(vals) >= max_count:
                return None
            try:
                vals.append(float(tok))
            except ValueError:
                return None
        return vals

    def process(self, line: str) -> str:
        if ":" not in line:
            return "ERR:NO_COLON"
        cmd, arg = line.split(":", 1)
        cmd = cmd.upper()
        left, right = self.motors

        def ok(c, clamped=False):
            return f"OK:{c}" + (";CLAMPED" if clamped else "")

        if cmd == "A":
            v = self._floats(arg, 2)
            if not v:
                return "ERR:A_BAD_ARG"
            c = self._set_target(left, v[0])
            c |= self._set_target(right, v[1] if len(v) == 2 else v[0])
            return ok("A", c)
        if cmd in ("AL", "AR"):
            v = self._floats(arg, 1)
            if not v or len(v) != 1:
                return "ERR:A_BAD_ARG"
            return ok(cmd, self._set_target(left if cmd == "AL" else right, v[0]))
        if cmd == "J":
            v = self._floats(arg, 2)
            if not v:
                return "ERR:J_BAD_ARG"
            dl, dr = v[0], v[1] if len(v) == 2 else v[0]
            c = self._set_target(left, left.target / self.spd + dl)
            c |= self._set_target(right, right.target / self.spd + dr)
            return ok("J", c)
        if cmd in ("E", "EL", "ER"):
            v = self._floats(arg, 1)
            if not v or len(v) != 1:
                return "ERR:E_BAD_ARG"
            on = v[0] != 0.0
            for m in ((left, right) if cmd == "E" else ((left,) if cmd == "EL" else (right,))):
                m.enabled = on
                if on:
                    m.target, m.into_move = m.current, 0
            return ok(cmd)
        if cmd in ("Z", "ZL", "ZR"):
            for m in ((left, right) if cmd == "Z" else ((left,) if cmd == "ZL" else (right,))):
                m.current = m.target = m.into_move = 0
            return ok(cmd)
        if cmd == "P":
            return f"POS:{left.current / self.spd:.2f},{right.current / self.spd:.2f}"
        if cmd == "S":
            moving = any(m.enabled and m.current != m.target for m in self.motors)
            return (f"STA:POS={left.current / self.spd:.2f},{right.current / self.spd:.2f};"
                    f"TGT={left.target / self.spd:.2f},{right.target / self.spd:.2f};"
                    f"EN={int(left.enabled)},{int(right.enabled)};MOV={int(moving)};LIM={self.max_angle:.2f};"
                    f"VMIN={self.min_delay_us};VSTART={self.start_delay_us};RAMP={self.ramp_steps}")
        if cmd == "I":
            return FIRMWARE_ID
        if cmd == "V":
            v = self._floats(arg, 3)
            if not v:
                return "ERR:V_BAD_ARG"
            if not 20.0 <= v[0] <= 100000.0:
                return "ERR:V_OUT_OF_RANGE"
            self.min_delay_us = int(v[0])
            if len(v) >= 2:
                if v[1] < self.min_delay_us:
                    return "ERR:V_START_BELOW_MIN"
                self.start_delay_us = int(v[1])
            if len(v) >= 3:
                if v[2] < 0:
                    return "ERR:V_RAMP_NEGATIVE"
                self.ramp_steps = int(v[2])
            self.start_delay_us = max(self.start_delay_us, self.min_delay_us)
            return ok("V")
        if cmd == "M":
            v = self._floats(arg, 1)
            if not v or len(v) != 1:
                return "ERR:M_BAD_ARG"
            if not 0.0 < v[0] <= 180.0:
                return "ERR:M_OUT_OF_RANGE"
            self.max_angle = v[0]
            for m in self.motors:
                self._set_target(m, m.target / self.spd)
            return ok("M")
        return "ERR:UNKNOWN_CMD"

    def close(self):
        self.port.close()
