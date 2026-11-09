"""CAN over TCP tussen de sim en de robotsoftware.

`socketcan` bestaat niet op Windows en python-can's `virtual` werkt maar binnen één proces. Daarom levert de sim een
eigen python-can interface `simcan`: de robotsoftware zet in zijn config `can_interface: "simcan"` en
`can_channel: "127.0.0.1:5103"`, en `vesc_can.py` blijft ongewijzigd.

Frame op de lijn (14 bytes, big endian): arbitration_id u32, vlaggen u8 (bit 0 extended, bit 1 error, bit 2 remote),
dlc u8, data 8 bytes (aangevuld met nullen).

Aanmelden bij python-can: `register()` (de runner doet dat voor hij OpenAgbot importeert). Als pakket geïnstalleerd
kan het ook via een entry point; dat is niet nodig.
"""
from __future__ import annotations

import queue
import socket
import struct
import threading
import time
from typing import Callable

import can

from openagbot_fieldsim.devices.transport import TcpPort

_FMT = ">IBB8s"
FRAME_SIZE = struct.calcsize(_FMT)


def encode(arbitration_id: int, data: bytes, extended: bool = True, error: bool = False,
           remote: bool = False) -> bytes:
    flags = (1 if extended else 0) | (2 if error else 0) | (4 if remote else 0)
    data = bytes(data)[:8]
    return struct.pack(_FMT, arbitration_id, flags, len(data), data.ljust(8, b"\0"))


def decode(raw: bytes) -> tuple[int, bytes, bool, bool, bool]:
    arb, flags, dlc, data = struct.unpack(_FMT, raw)
    return arb, data[:dlc], bool(flags & 1), bool(flags & 2), bool(flags & 4)


class FrameReader:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while len(self.buf) >= FRAME_SIZE:
            out.append(decode(bytes(self.buf[:FRAME_SIZE])))
            del self.buf[:FRAME_SIZE]
        return out


class CanHub:
    """Kant van de sim: één TCP-poort, frames van de robot gaan naar `on_frame`, `send()` gaat naar de robot."""

    def __init__(self, port: int, on_frame: Callable[[int, bytes], None]):
        self._reader = FrameReader()
        self.on_frame = on_frame
        self.frames_in = 0
        self.frames_out = 0
        self.connected_since = None
        self.port = TcpPort("can", port=port, on_data=self._on_data, on_connect=self._on_connect)

    def _on_connect(self):
        self._reader = FrameReader()
        self.connected_since = time.monotonic()

    def _on_data(self, data: bytes):
        for arb, payload, ext, err, rtr in self._reader.feed(data):
            if ext and not err and not rtr:
                self.frames_in += 1
                self.on_frame(arb, payload)

    def send(self, arbitration_id: int, data: bytes) -> bool:
        ok = self.port.send(encode(arbitration_id, data))
        if ok:
            self.frames_out += 1
        return ok

    def close(self):
        self.port.close()


class SimCanBus(can.BusABC):
    """python-can interface 'simcan' (kant van de robotsoftware). channel = 'host:poort'."""

    def __init__(self, channel: str = "127.0.0.1:5103", **kwargs):
        host, _, port = str(channel).rpartition(":")
        self.channel_info = f"simcan {channel}"
        self._sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=2.0)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(None)
        self._rxq: queue.Queue = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()
        threading.Thread(target=self._rx_loop, daemon=True, name="simcan-rx").start()
        super().__init__(channel=channel, **kwargs)

    def _rx_loop(self):
        reader = FrameReader()
        while not self._closed:
            try:
                data = self._sock.recv(4096)
            except OSError:
                data = b""
            if not data:
                self._rxq.put(None)
                return
            for arb, payload, ext, err, rtr in reader.feed(data):
                self._rxq.put(can.Message(arbitration_id=arb, data=payload, is_extended_id=ext,
                                          is_error_frame=err, is_remote_frame=rtr, timestamp=time.time(),
                                          channel=self.channel_info))

    def _recv_internal(self, timeout):
        try:
            msg = self._rxq.get(timeout=timeout) if timeout is None or timeout > 0 else self._rxq.get_nowait()
        except queue.Empty:
            return None, False
        if msg is None:
            raise can.CanOperationError("simcan: verbinding met de sim verbroken")
        return msg, False

    def send(self, msg, timeout=None):
        if self._closed:
            raise can.CanOperationError("simcan: bus is dicht")
        try:
            with self._lock:
                self._sock.sendall(encode(msg.arbitration_id, msg.data, msg.is_extended_id, msg.is_error_frame,
                                          msg.is_remote_frame))
        except OSError as e:
            raise can.CanOperationError(f"simcan: verzenden mislukt: {e}") from e

    def shutdown(self):
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass
        super().shutdown()


def register() -> None:
    """Maak `can.Bus(interface="simcan", ...)` beschikbaar in dit proces."""
    import can.util
    from can import interfaces
    interfaces.BACKENDS["simcan"] = ("openagbot_fieldsim.devices.can_link", "SimCanBus")
    # python-can kopieert VALID_INTERFACES bij het importeren naar een paar modules; overal bijwerken.
    valid = frozenset(set(interfaces.VALID_INTERFACES) | {"simcan"})
    for mod in (can, interfaces, can.util):
        if hasattr(mod, "VALID_INTERFACES"):
            mod.VALID_INTERFACES = valid
