"""NMEA-0183 zinnen zoals de u-blox ZED-X20D ze geeft (talker GN, CFG-NMEA-HIGHPREC: 7 decimalen in de minuten)."""
from __future__ import annotations

import time


def checksum(body: str) -> str:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"{c:02X}"


def sentence(body: str) -> bytes:
    return f"${body}*{checksum(body)}\r\n".encode("ascii")


def _utc(t: float) -> str:
    g = time.gmtime(t)
    return f"{g.tm_hour:02d}{g.tm_min:02d}{g.tm_sec:02d}.{int((t % 1) * 100):02d}"


def _lat(lat: float) -> tuple[str, str]:
    a = abs(lat)
    d = int(a)
    return f"{d:02d}{(a - d) * 60.0:011.8f}", "N" if lat >= 0 else "S"


def _lon(lon: float) -> tuple[str, str]:
    a = abs(lon)
    d = int(a)
    return f"{d:03d}{(a - d) * 60.0:011.8f}", "E" if lon >= 0 else "W"


def gga(t: float, lat: float, lon: float, quality: int, sats: int, hdop: float, alt_m: float,
        correction_age_s: float | None, station_id: str = "0000") -> bytes:
    if quality <= 0:
        return sentence(f"GNGGA,{_utc(t)},,,,,0,{sats:02d},99.99,,,,,,")
    la, ns = _lat(lat)
    lo, ew = _lon(lon)
    age = f"{correction_age_s:.1f}" if correction_age_s is not None else ""
    sid = station_id if correction_age_s is not None else ""
    return sentence(f"GNGGA,{_utc(t)},{la},{ns},{lo},{ew},{quality},{sats:02d},{hdop:.2f},{alt_m:.4f},M,"
                    f"46.000,M,{age},{sid}")


def ths(heading_deg: float | None, status: str = "A") -> bytes:
    """$GNTHS,<koers>,<status>; status A = gemeten, E = geschat, V = ongeldig (dan leeg koersveld)."""
    if heading_deg is None or status == "V":
        return sentence("GNTHS,,V")
    return sentence(f"GNTHS,{heading_deg % 360.0:.2f},{status}")


def vtg(cog_deg: float, speed_kmh: float, mode: str = "D") -> bytes:
    knots = speed_kmh / 1.852
    cog = f"{cog_deg % 360.0:.2f}" if speed_kmh > 0.1 else ""
    return sentence(f"GNVTG,{cog},T,,M,{knots:.3f},N,{speed_kmh:.3f},K,{mode}")


def rmc(t: float, lat: float, lon: float, speed_kmh: float, cog_deg: float, valid: bool = True,
        mode: str = "D") -> bytes:
    g = time.gmtime(t)
    date = f"{g.tm_mday:02d}{g.tm_mon:02d}{g.tm_year % 100:02d}"
    if not valid:
        return sentence(f"GNRMC,{_utc(t)},V,,,,,,,{date},,,N,V")
    la, ns = _lat(lat)
    lo, ew = _lon(lon)
    return sentence(f"GNRMC,{_utc(t)},A,{la},{ns},{lo},{ew},{speed_kmh / 1.852:.3f},{cog_deg % 360.0:.2f},"
                    f"{date},,,{mode},V")
