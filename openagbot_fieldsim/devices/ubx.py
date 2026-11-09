"""UBX-binaire berichten die OpenAgbot leest of verstuurt (NAV-DAHEADING, CFG-VALSET, ACK)."""
from __future__ import annotations

import struct

SYNC = b"\xb5\x62"
NAV_DAHEADING = (0x01, 0x45)
CFG_VALSET = (0x06, 0x8A)
ACK_ACK = (0x05, 0x01)
ACK_NAK = (0x05, 0x00)


def frame(cls: int, mid: int, payload: bytes = b"") -> bytes:
    head = bytes((cls, mid, len(payload) & 0xFF, len(payload) >> 8))
    a = b = 0
    for x in head + payload:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return SYNC + head + payload + bytes((a, b))


def ack(cls: int, mid: int, ok: bool = True) -> bytes:
    return frame(*(ACK_ACK if ok else ACK_NAK), bytes((cls, mid)))


def daheading(itow_ms: int, rel_n_m: float, rel_e_m: float, rel_d_m: float, heading_deg: float | None,
              acc_heading_deg: float, solution: str = "fixed") -> bytes:
    """NAV-DAHEADING versie 2 (HDG 2.01), 64 bytes payload.

    Velden zoals gps_logic._parse_daheading ze leest: relPosN/E/D/Length (mm) en relPosHeading (1e-5 graad) vanaf
    byte 8, accHeading (1e-5 graad) op byte 48, flags op byte 56: bit 2 relPosValid, bit 3-4 carrSoln
    (1 = float, 2 = fixed), bit 6 relPosHeadingValid.
    """
    length = (rel_n_m ** 2 + rel_e_m ** 2 + rel_d_m ** 2) ** 0.5
    soln = {"none": 0, "float": 1, "fixed": 2}.get(solution, 0)
    flags = 0x01 | 0x04 | (soln << 3)   # gnssFixOK, relPosValid, carrSoln
    if heading_deg is not None:
        flags |= 0x40
    p = bytearray(64)
    p[0] = 0x02
    struct.pack_into("<I", p, 4, itow_ms & 0xFFFFFFFF)
    struct.pack_into("<iiiii", p, 8, round(rel_n_m * 1000), round(rel_e_m * 1000), round(rel_d_m * 1000),
                     round(length * 1000), round((heading_deg or 0.0) % 360.0 * 1e5))
    struct.pack_into("<I", p, 48, round(acc_heading_deg * 1e5))
    struct.pack_into("<I", p, 56, flags)
    return frame(*NAV_DAHEADING, bytes(p))


class UbxNmeaSplitter:
    """Knipt een ontvangen bytestroom in UBX-frames (met geldige checksum) en NMEA/RTCM-rommel (genegeerd)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                del self.buf[:-1 if self.buf.endswith(b"\xb5") else len(self.buf)]
                return out
            if len(self.buf) < i + 6:
                del self.buf[:i]
                return out
            n = self.buf[i + 4] | (self.buf[i + 5] << 8)
            end = i + 8 + n
            if n > 4096:
                del self.buf[:i + 2]
                continue
            if len(self.buf) < end:
                del self.buf[:i]
                return out
            raw = bytes(self.buf[i:end])
            del self.buf[:end]
            if raw == frame(raw[2], raw[3], raw[6:-2]):
                out.append((raw[2], raw[3], raw[6:-2]))
