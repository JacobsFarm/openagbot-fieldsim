"""TCP-poorten waarop de robotsoftware zijn 'apparaten' vindt.

OpenAgbot opent `socket://127.0.0.1:<poort>` met pyserial (`serial_for_url`); voor de software is dat een gewone
seriële poort. Elke emulator luistert op één poort en praat met één client tegelijk: komt er een nieuwe client
(backend herstart), dan vervangt die de oude, net als bij het opnieuw openen van een echte poort.
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable

log = logging.getLogger(__name__)


class TcpPort:
    def __init__(self, name: str, host: str = "127.0.0.1", port: int = 0,
                 on_data: Callable[[bytes], None] | None = None,
                 on_connect: Callable[[], None] | None = None):
        self.name = name
        self.on_data = on_data or (lambda data: None)
        self.on_connect = on_connect or (lambda: None)
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._srv.bind((host, port))
        except OSError:
            log.warning("%s: poort %s bezet, kies een vrije", name, port)
            self._srv.bind((host, 0))
        self._srv.listen(2)
        self.host, self.port = self._srv.getsockname()
        self._client: socket.socket | None = None
        self._tx_lock = threading.Lock()
        self._running = True
        self.bytes_in = 0
        self.bytes_out = 0
        self.connections = 0
        threading.Thread(target=self._accept_loop, name=f"{name}-accept", daemon=True).start()

    @property
    def url(self) -> str:
        return f"socket://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _accept_loop(self):
        while self._running:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            old, self._client = self._client, conn
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            self.connections += 1
            log.info("%s: client verbonden (%d)", self.name, self.connections)
            try:
                self.on_connect()
            except Exception:
                log.exception("%s: on_connect", self.name)
            threading.Thread(target=self._rx_loop, args=(conn,), name=f"{self.name}-rx", daemon=True).start()

    def _rx_loop(self, conn: socket.socket):
        while self._running:
            try:
                data = conn.recv(4096)
            except OSError:
                data = b""
            if not data:
                break
            self.bytes_in += len(data)
            try:
                self.on_data(data)
            except Exception:
                log.exception("%s: on_data", self.name)
        if self._client is conn:
            self._client = None
        try:
            conn.close()
        except OSError:
            pass

    def send(self, data: bytes) -> bool:
        conn = self._client
        if conn is None:
            return False
        try:
            with self._tx_lock:
                conn.sendall(data)
            self.bytes_out += len(data)
            return True
        except OSError:
            if self._client is conn:
                self._client = None
            return False

    def close(self):
        self._running = False
        for s in (self._client, self._srv):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
