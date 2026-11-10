"""Dubins-paden: kortste pad tussen twee standen (x, y, koers) voor een voertuig dat alleen vooruit rijdt met een
minimale draaicirkel R. Zes woorden: LSL, RSR, LSR, RSL (boog-recht-boog) en RLR, LRL (drie bogen, de 'omega' of
peervormige bocht als de zijsprong kleiner is dan 2R). Zo plannen AgOpenGPS (CDubins) en Fields2Cover hun
kopakkerbochten ook.

Hoeken hier wiskundig: theta in radialen tegen de klok in vanaf de x-as (oost). De missieplanner rekent in
lokale meters (x = oost, y = noord); kompaskoers = 90 - theta.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TAU = 2.0 * math.pi


def _mod(a: float) -> float:
    return a % TAU


@dataclass
class DubinsPath:
    word: str              # bv. "LSR"
    lengths: tuple         # drie stukken, in meters
    start: tuple           # (x, y, theta)
    radius: float

    @property
    def length(self) -> float:
        return sum(self.lengths)

    def sample(self, step: float = 0.25) -> list[tuple[float, float, float]]:
        """Punten (x, y, theta) om de `step` meter, begin en eind inbegrepen."""
        x, y, th = self.start
        out = [(x, y, th)]
        r = self.radius
        for kind, seg in zip(self.word, self.lengths):
            if seg <= 1e-9:
                continue
            n = max(1, int(math.ceil(seg / step)))
            ds = seg / n
            for _ in range(n):
                if kind == "S":
                    x += ds * math.cos(th)
                    y += ds * math.sin(th)
                else:
                    sgn = 1.0 if kind == "L" else -1.0
                    dth = sgn * ds / r
                    # exacte boog
                    x += r * sgn * (math.sin(th + dth) - math.sin(th))
                    y += r * sgn * (-math.cos(th + dth) + math.cos(th))
                    th += dth
                out.append((x, y, th))
        return out


def _words(alpha: float, beta: float, d: float):
    """Genormaliseerde Dubins-oplossingen (Shkel & Lumelsky). Geeft (woord, t, p, q) in eenheden van R."""
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    cab = math.cos(alpha - beta)
    res = []
    # LSL
    p2 = 2 + d * d - 2 * cab + 2 * d * (sa - sb)
    if p2 >= 0:
        tmp = math.atan2(cb - ca, d + sa - sb)
        res.append(("LSL", _mod(-alpha + tmp), math.sqrt(p2), _mod(beta - tmp)))
    # RSR
    p2 = 2 + d * d - 2 * cab + 2 * d * (sb - sa)
    if p2 >= 0:
        tmp = math.atan2(ca - cb, d - sa + sb)
        res.append(("RSR", _mod(alpha - tmp), math.sqrt(p2), _mod(-beta + tmp)))
    # LSR
    p2 = -2 + d * d + 2 * cab + 2 * d * (sa + sb)
    if p2 >= 0:
        p = math.sqrt(p2)
        tmp = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2.0, p)
        res.append(("LSR", _mod(-alpha + tmp), p, _mod(-_mod(beta) + tmp)))
    # RSL
    p2 = -2 + d * d + 2 * cab - 2 * d * (sa + sb)
    if p2 >= 0:
        p = math.sqrt(p2)
        tmp = math.atan2(ca + cb, d - sa - sb) - math.atan2(2.0, p)
        res.append(("RSL", _mod(alpha - tmp), p, _mod(beta - tmp)))
    # RLR
    tmp = (6.0 - d * d + 2 * cab + 2 * d * (sa - sb)) / 8.0
    if abs(tmp) <= 1:
        p = _mod(TAU - math.acos(tmp))
        t = _mod(alpha - math.atan2(ca - cb, d - sa + sb) + p / 2.0)
        res.append(("RLR", t, p, _mod(alpha - beta - t + p)))
    # LRL
    tmp = (6.0 - d * d + 2 * cab + 2 * d * (sb - sa)) / 8.0
    if abs(tmp) <= 1:
        p = _mod(TAU - math.acos(tmp))
        t = _mod(-alpha + math.atan2(-ca + cb, d + sa - sb) + p / 2.0)
        res.append(("LRL", t, p, _mod(beta - alpha - t + p)))
    return res


def dubins_all(start, goal, radius: float) -> list[DubinsPath]:
    """Alle geldige Dubins-paden van start naar goal (elk (x, y, theta)), kortste eerst."""
    x0, y0, th0 = start
    x1, y1, th1 = goal
    dx, dy = x1 - x0, y1 - y0
    D = math.hypot(dx, dy)
    d = D / radius
    phi = math.atan2(dy, dx) if D > 1e-12 else 0.0
    alpha, beta = _mod(th0 - phi), _mod(th1 - phi)
    out = [DubinsPath(w, (t * radius, p * radius, q * radius), (x0, y0, th0), radius)
           for w, t, p, q in _words(alpha, beta, d)]
    out.sort(key=lambda p: p.length)
    return out


def dubins_shortest(start, goal, radius: float) -> DubinsPath | None:
    paths = dubins_all(start, goal, radius)
    return paths[0] if paths else None
