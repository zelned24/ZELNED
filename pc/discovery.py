"""
Zel.NeD — UDP Auto-Discovery
==============================
Listens for ZELNED beacon broadcasts from 3DS consoles on the LAN.
The 3DS sends a UDP broadcast every 2 seconds; this module collects them.
"""

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional

UDP_PORT     = 9504
BEACON_MAGIC = b"ZELNED"
BEACON_SIZE  = 6 + 1 + 2 + 32   # magic + version + tcp_port + name = 41 bytes
FMT_BEACON   = "<6sBH32s"

# A device is considered "gone" if no beacon received for this many seconds
EXPIRY_SECS  = 8.0


@dataclass
class DiscoveredDevice:
    ip: str
    name: str
    tcp_port: int
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def display_name(self) -> str:
        return f"{self.name}  [{self.ip}:{self.tcp_port}]"


class DiscoveryListener:
    """
    Background UDP listener that maintains a live list of 3DS consoles.

    Usage:
        listener = DiscoveryListener(on_change=my_callback)
        listener.start()
        ...
        devices = listener.devices()
        listener.stop()
    """

    def __init__(self, on_change: Optional[Callable[[], None]] = None):
        self._devices: Dict[str, DiscoveredDevice] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_change = on_change

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def devices(self) -> List[DiscoveredDevice]:
        """Return a snapshot of currently visible devices, evicting expired ones."""
        now = time.monotonic()
        with self._lock:
            expired = [ip for ip, d in self._devices.items()
                       if now - d.last_seen > EXPIRY_SECS]
            for ip in expired:
                del self._devices[ip]
            return list(self._devices.values())

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind(("", UDP_PORT))
        except OSError as e:
            print(f"[Discovery] Cannot bind UDP port {UDP_PORT}: {e}")
            return

        while self._running:
            try:
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                # Evict expired entries periodically
                self.devices()
                continue
            except OSError:
                break

            if len(data) < BEACON_SIZE:
                continue

            try:
                magic, version, tcp_port, raw_name = struct.unpack_from(FMT_BEACON, data)
            except struct.error:
                continue

            if magic != BEACON_MAGIC or version != 1:
                continue

            name = raw_name.rstrip(b"\x00").decode("utf-8", errors="replace")
            ip = addr[0]

            changed = False
            with self._lock:
                existing = self._devices.get(ip)
                if existing is None:
                    self._devices[ip] = DiscoveredDevice(ip=ip, name=name, tcp_port=tcp_port)
                    changed = True
                else:
                    existing.last_seen = time.monotonic()
                    if existing.name != name or existing.tcp_port != tcp_port:
                        existing.name = name
                        existing.tcp_port = tcp_port
                        changed = True

            if changed and self._on_change:
                self._on_change()

        sock.close()
