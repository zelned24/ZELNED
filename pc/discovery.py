"""
Zel.NeD — UDP Auto-Discovery & Active Subnet Scanner
===================================================
1. Listens for ZELNED beacon broadcasts (UDP port 9504) from 3DS consoles.
2. Performs ultra-fast active subnet TCP probes (port 9503) when requested,
   ensuring 100% reliable detection even if routers/firewalls drop UDP broadcasts.
"""

import socket
import struct
import threading
import time
import concurrent.futures
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional

UDP_PORT     = 9504
TCP_PORT     = 9503
BEACON_MAGIC = b"ZELNED"
BEACON_SIZE  = 6 + 1 + 2 + 32   # magic + version + tcp_port + name = 41 bytes
FMT_BEACON   = "<6sBH32s"

# A device is considered "gone" if no beacon or probe responded for this many seconds
EXPIRY_SECS  = 15.0


@dataclass
class DiscoveredDevice:
    ip: str
    name: str
    tcp_port: int
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def display_name(self) -> str:
        return f"{self.name}  [{self.ip}:{self.tcp_port}]"


def get_local_ip() -> str:
    """Detect the local LAN IP of this PC."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def probe_host(ip: str, port: int = TCP_PORT, timeout: float = 0.35) -> Optional[DiscoveredDevice]:
    """Check if a host is listening on the ZelNeD TCP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        s.close()
        return DiscoveredDevice(ip=ip, name="Nintendo 3DS (ZelNeD)", tcp_port=port)
    except (socket.timeout, OSError):
        return None


class DiscoveryListener:
    """
    Background UDP listener + Active subnet scanner.

    Usage:
        listener = DiscoveryListener(on_change=my_callback)
        listener.start()
        ...
        listener.scan_active()
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

    def scan_active(self, port: int = TCP_PORT, timeout: float = 0.35) -> List[DiscoveredDevice]:
        """
        Scan the local /24 subnet concurrently using a thread pool.
        Finds any 3DS running ZelNeD in ~1 second even with UDP broadcasts blocked.
        """
        local_ip = get_local_ip()
        if local_ip == "127.0.0.1":
            return self.devices()

        parts = local_ip.split(".")
        base = f"{parts[0]}.{parts[1]}.{parts[2]}."
        ips = [f"{base}{i}" for i in range(1, 255)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
            futures = [executor.submit(probe_host, ip, port, timeout) for ip in ips]
            for fut in concurrent.futures.as_completed(futures):
                dev = fut.result()
                if dev:
                    with self._lock:
                        existing = self._devices.get(dev.ip)
                        if existing is None:
                            self._devices[dev.ip] = dev
                        else:
                            existing.last_seen = time.monotonic()

        return self.devices()

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
