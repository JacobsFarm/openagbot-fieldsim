"""GNSS-emulator: een ZED-X20D met dubbele antenne (simpleRTK4 Dual) op 25 Hz.

Geeft per epoch dezelfde berichten als het echte board: GGA, THS, VTG, RMC (NMEA) en NAV-DAHEADING (UBX), en
beantwoordt CFG-VALSET met een ACK. RTCM-correcties van NTRIP worden geaccepteerd en genegeerd.

De meetfout is instelbaar per fix-soort (Gauss-Markov: langzaam zwervend plus wit), net als het wegvallen van de
fix, van de heading of van de hele stroom. Montagefouten uit de robotconfig (heading_offset_deg, pitch_offset_deg)
worden er juist IN gezet, zodat de correctie in de software precies klopt zoals in het veld.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from openagbot_fieldsim.devices import nmea, ubx
from openagbot_fieldsim.devices.transport import TcpPort

FIX_QUALITY = {"rtk_fixed": 4, "rtk_float": 5, "dgps": 2, "gps": 1, "none": 0}
# Horizontale fout (1 sigma, m) en hoe snel die zwerft (s)
FIX_SIGMA = {"rtk_fixed": (0.008, 3.0), "rtk_float": (0.12, 10.0), "dgps": (0.45, 20.0), "gps": (1.5, 30.0),
             "none": (0.0, 1.0)}


@dataclass
class GnssTruth:
    """Wat de fysica aanlevert: de echte posities van de twee antennes."""
    t: float
    ant1_lat: float
    ant1_lon: float
    ant1_alt: float
    rel_n: float          # GPS1 -> GPS2 in meters
    rel_e: float
    rel_d: float
    speed_mps: float      # grondsnelheid van antenne GPS1
    cog_deg: float        # bewegingsrichting van antenne GPS1


@dataclass
class GnssSettings:
    rate_hz: float = 25.0
    fix: str = "rtk_fixed"
    heading_valid: bool = True
    heading_sigma_deg: float = 0.08
    correction_age_s: float = 1.0
    stream_enabled: bool = True
    sats: int = 28
    noise_scale: float = 1.0
    mount_errors: bool = True     # heading_offset_deg en pitch_offset_deg uit de config nabootsen
    extra: dict = field(default_factory=dict)


class GnssEmulator:
    def __init__(self, port: int, heading_offset_deg: float = 0.0, pitch_offset_deg: float = 0.0,
                 seed: int = 1):
        self.settings = GnssSettings()
        self.heading_offset_deg = heading_offset_deg
        self.pitch_offset_deg = pitch_offset_deg
        self.rng = random.Random(seed)
        self._splitter = ubx.UbxNmeaSplitter()
        self.port = TcpPort("gnss", port=port, on_data=self._on_data)
        self._next_epoch = 0.0
        self._err = [0.0, 0.0]           # zwervende fout oost/noord (m)
        self.epochs = 0
        self.last = {}                   # laatst verzonden waarden, voor de UI
        self.rx_messages = {}

    # ---------------------------------------------------------------- ontvangen (config, RTCM)
    def _on_data(self, data: bytes):
        for cls, mid, payload in self._splitter.feed(data):
            key = f"{cls:02X}-{mid:02X}"
            self.rx_messages[key] = self.rx_messages.get(key, 0) + 1
            if (cls, mid) == ubx.CFG_VALSET:
                self.port.send(ubx.ack(cls, mid, True))

    # ---------------------------------------------------------------- zenden
    def due(self, now: float) -> bool:
        return now >= self._next_epoch

    def emit(self, truth: GnssTruth) -> None:
        """Eén epoch: alle berichten in één keer, zoals de ontvanger ze in een klontje over USB stuurt."""
        s = self.settings
        period = 1.0 / s.rate_hz
        self._next_epoch = max(self._next_epoch + period, truth.t) if self._next_epoch else truth.t + period
        if not s.stream_enabled:
            return
        self.epochs += 1
        sigma, tau = FIX_SIGMA.get(s.fix, (0.0, 1.0))
        sigma *= s.noise_scale
        a = math.exp(-period / tau)
        for i in range(2):
            self._err[i] = a * self._err[i] + math.sqrt(1 - a * a) * self.rng.gauss(0.0, sigma)
        white = 0.002 * s.noise_scale
        de = self._err[0] + self.rng.gauss(0.0, white)
        dn = self._err[1] + self.rng.gauss(0.0, white)
        lat = truth.ant1_lat + dn / 111_250.0
        lon = truth.ant1_lon + de / (111_320.0 * math.cos(math.radians(truth.ant1_lat)))

        quality = FIX_QUALITY.get(s.fix, 0)
        corr_age = s.correction_age_s if quality in (2, 4, 5) else None
        hdop = 0.55 if quality >= 2 else 1.1

        # Koers van de antennelijn, met de montagefout erin (de software trekt heading_offset_deg er weer af)
        hz = math.hypot(truth.rel_n, truth.rel_e)
        true_heading = math.degrees(math.atan2(truth.rel_e, truth.rel_n)) % 360.0
        mount_h = self.heading_offset_deg if s.mount_errors else 0.0
        mount_p = self.pitch_offset_deg if s.mount_errors else 0.0
        heading = (true_heading - mount_h + self.rng.gauss(0.0, s.heading_sigma_deg * s.noise_scale)) % 360.0
        heading_ok = s.heading_valid and quality > 0
        # Baseline-helling: robot-pitch plus de scheefstand van de montage
        pitch = math.degrees(math.atan2(-truth.rel_d, hz)) + mount_p
        base = math.sqrt(hz * hz + truth.rel_d ** 2)
        rel_d = -base * math.sin(math.radians(pitch))
        rel_h = base * math.cos(math.radians(pitch))
        rel_n = rel_h * math.cos(math.radians(heading))
        rel_e = rel_h * math.sin(math.radians(heading))

        speed_kmh = truth.speed_mps * 3.6
        t = truth.t
        out = [
            nmea.gga(t, lat, lon, quality, s.sats if quality else 0, hdop, truth.ant1_alt, corr_age),
            nmea.rmc(t, lat, lon, speed_kmh, truth.cog_deg, valid=quality > 0),
            nmea.vtg(truth.cog_deg, speed_kmh),
            nmea.ths(heading if heading_ok else None, "A" if heading_ok else "V"),
        ]
        if quality > 0:
            soln = "fixed" if heading_ok and s.fix == "rtk_fixed" else ("float" if heading_ok else "none")
            out.append(ubx.daheading(int((t % 604800) * 1000), rel_n, rel_e, rel_d, heading if heading_ok else None,
                                     max(0.05, s.heading_sigma_deg), soln))
        self.port.send(b"".join(out))
        self.last = {"lat": lat, "lon": lon, "fix": s.fix, "quality": quality, "heading": heading if heading_ok
                     else None, "pitch_deg": pitch - mount_p, "speed_kmh": speed_kmh, "error_m": math.hypot(de, dn)}

    def close(self):
        self.port.close()
