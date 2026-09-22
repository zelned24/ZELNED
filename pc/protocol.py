"""
Zel.NeD — ZELNED v1 Protocol (PC side)
========================================
Handles: LZ4 compression, CRC32 integrity, chunk packing,
         session handshake, throttle, ACK/NACK loop,
         and optional per-chunk telemetry for auto-tuning.
"""

import struct
import socket
import time
import zlib
import lz4.block as lz4b
from pathlib import Path
from typing import Optional, Callable

# Protocol Constants
MAGIC            = b"ZELNED"
VERSION          = 1
TCP_PORT         = 9503
UDP_BEACON_PORT  = 9504
CHUNK_SIZE_RAW   = 262144       # 256 KB — matches 3DS protocol.hpp
CONNECT_TIMEOUT  = 8.0
ACK_TIMEOUT      = 60.0
MAX_RETRIES      = 3

# Session flags (bitmask)
FLAG_LZ4           = 0x01
FLAG_CRC32         = 0x02
FLAG_RESUME        = 0x04
FLAG_TELEMETRY     = 0x08  # Request per-chunk telemetry in ACKs
FLAG_BENCHMARK_NET = 0x10  # Benchmark mode: 3DS discards data to RAM
FLAG_INSTALL_CIA    = 0x20  # Direct CIA install: 3DS streams to AM service

# Item types
TYPE_FILE        = 0
TYPE_DIRECTORY   = 1
TYPE_CIA_INSTALL = 2

# Chunk ACK status codes
ACK_OK             = 0
ACK_NACK           = 1   # CRC mismatch — resend
ACK_ERROR_FS       = 2   # SD write error
ACK_DIR_OK         = 3   # Directory created
ACK_WITH_TELEMETRY = 4   # Extended ACK with telemetry payload

# Struct Formats (little-endian, packed)
# Session header: magic(6) + version(1) + flags(1) + throttle_kbps(4) + queue_count(4) = 16 bytes
FMT_SESSION   = "<6sBBII"
SESSION_SIZE  = struct.calcsize(FMT_SESSION)

# File header: type(1) + uncompressed_size(8) + resume_offset(8) + total_chunks(4) + path_len(2) = 23 bytes
FMT_FILE      = "<BQQIH"
FILE_HDR_SIZE = struct.calcsize(FMT_FILE)

# Chunk header: chunk_index(4) + compressed_size(4) + uncompressed_size(4) + crc32(4) = 16 bytes
FMT_CHUNK      = "<IIII"
CHUNK_HDR_SIZE = struct.calcsize(FMT_CHUNK)

# Normal ACK: chunk_index(4) + status(1) = 5 bytes
FMT_ACK  = "<IB"
ACK_SIZE = struct.calcsize(FMT_ACK)

# Telemetry ACK: chunk_index(4)+status(1)+t_net(2)+t_cpu(2)+t_sd(2)+bars(1)+cpu%(1)+reserved(2) = 15 bytes
FMT_TELEM_ACK  = "<IBHHHBBH"
TELEM_ACK_SIZE = struct.calcsize(FMT_TELEM_ACK)   # must be 15

# Handshake response: status(1) = 1 byte
FMT_HS_RESP  = "<B"
HS_RESP_SIZE = struct.calcsize(FMT_HS_RESP)


class ProtocolError(Exception):
    """Raised when the 3DS responds with an unexpected or error status."""
    pass


class ThrottleController:
    """Token-bucket throttle to cap outbound bandwidth."""
    def __init__(self, kbps: int):
        self.kbps = kbps
        self.bytes_per_sec = kbps * 1024
        self._tokens = float(self.bytes_per_sec)
        self._last_refill = time.monotonic()

    def consume(self, n_bytes: int):
        if self.kbps == 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self.bytes_per_sec),
                           self._tokens + elapsed * self.bytes_per_sec)
        if self._tokens < n_bytes:
            sleep_time = (n_bytes - self._tokens) / self.bytes_per_sec
            time.sleep(sleep_time)
            self._tokens = 0.0
            self._last_refill = time.monotonic()
        else:
            self._tokens -= n_bytes
            self._last_refill = now


def _sendall_exact(sock: socket.socket, data: bytes):
    """Send all bytes, raising on failure."""
    total = 0
    mv = memoryview(data)
    while total < len(data):
        sent = sock.send(mv[total:])
        if sent == 0:
            raise ConnectionError("Socket closed during send.")
        total += sent


def _recvall_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes, raising on failure."""
    buf = bytearray(n)
    view = memoryview(buf)
    received = 0
    while received < n:
        chunk = sock.recv_into(view[received:], n - received)
        if chunk == 0:
            raise ConnectionError("Socket closed during recv.")
        received += chunk
    return bytes(buf)


def connect_to_3ds(ip: str, port: int = TCP_PORT) -> socket.socket:
    """Open a TCP connection to the 3DS."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # Large send buffer: lets the OS queue multiple 256 KB chunks without blocking.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
    sock.connect((ip, port))
    sock.settimeout(None)
    return sock


def send_session_header(sock: socket.socket, throttle_kbps: int, queue_count: int,
                        resume: bool = False, extra_flags: int = 0):
    """
    Send the ZELNED session handshake and wait for the 3DS acknowledgement.

    Args:
        extra_flags: additional flag bits to OR in (e.g. FLAG_TELEMETRY).
    """
    flags = FLAG_LZ4 | FLAG_CRC32 | extra_flags
    if resume:
        flags |= FLAG_RESUME
    header = struct.pack(FMT_SESSION, MAGIC, VERSION, flags, throttle_kbps, queue_count)
    _sendall_exact(sock, header)
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    status = struct.unpack(FMT_HS_RESP, resp)[0]
    if status == 1:
        raise ProtocolError("3DS rejected session: SD card full.")
    if status != 0:
        raise ProtocolError(f"3DS rejected session: unknown error code {status}.")


def _read_ack(sock: socket.socket, use_telemetry: bool):
    """
    Read either a normal ACK (5 bytes) or a telemetry ACK (15 bytes).

    Returns:
        (chunk_index, status, t_net_us, t_cpu_us, t_sd_us, wifi_bars, cpu_pct)
    """
    if use_telemetry:
        sock.settimeout(ACK_TIMEOUT)
        raw = _recvall_exact(sock, TELEM_ACK_SIZE)
        sock.settimeout(None)
        fields = struct.unpack(FMT_TELEM_ACK, raw)
        return fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6]
    else:
        sock.settimeout(ACK_TIMEOUT)
        raw = _recvall_exact(sock, ACK_SIZE)
        sock.settimeout(None)
        idx, status = struct.unpack(FMT_ACK, raw)
        return idx, status, 0, 0, 0, 0, 0


def send_file(
    sock: socket.socket,
    local_path: Path,
    remote_path: str,
    throttle: ThrottleController,
    resume_offset: int = 0,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    use_telemetry: bool = False,
    tuner=None,
    window_size: int = 6,
    is_cia_install: bool = False,
) -> bool:
    """
    Send a single file over an established ZELNED session.

    Args:
        sock:           Connected TCP socket to the 3DS.
        local_path:     Path to the file on the PC.
        remote_path:    Relative destination path on sdmc:/ (e.g. '3ds/game.cia').
        throttle:       ThrottleController instance (kbps=0 for unlimited).
        resume_offset:  Byte offset to resume from (0 = start from beginning).
        progress_cb:    Called with (bytes_sent, total_bytes, effective_mbps) per chunk.
        use_telemetry:  If True, reads extended 15-byte ACKs with timing data.
        tuner:          Optional AutoTuner instance to feed telemetry into.
        window_size:    Sliding-window size (chunks in flight). Tuner may override.
        is_cia_install: If True, marks chunk stream for direct CIA install.

    Returns:
        True on success, False on unrecoverable error.
    """
    file_size = local_path.stat().st_size
    path_bytes = remote_path.encode("utf-8")

    total_bytes_to_send = file_size - resume_offset
    total_chunks = (total_bytes_to_send + CHUNK_SIZE_RAW - 1) // CHUNK_SIZE_RAW

    # Send file header
    ftype = TYPE_CIA_INSTALL if is_cia_install else TYPE_FILE
    file_hdr = struct.pack(
        FMT_FILE,
        ftype,
        file_size,
        resume_offset,
        total_chunks,
        len(path_bytes),
    )
    _sendall_exact(sock, file_hdr)
    _sendall_exact(sock, path_bytes)

    # Wait for 3DS ready ACK
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    status = struct.unpack(FMT_HS_RESP, resp)[0]
    if status != 0:
        raise ProtocolError(f"3DS not ready for file '{remote_path}': code {status}")

    # Determine per-file window size from tuner (if available)
    WINDOW = window_size
    if tuner is not None:
        WINDOW = tuner.get_params().window_size

    # Incompressible file formats: skip LZ4 entirely (saves CPU on both ends)
    _INCOMPRESSIBLE_SUFFIX = (
        ".cia", ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz",
        ".mp4", ".mkv", ".avi", ".mov", ".aac", ".mp3", ".ogg",
        ".jpg", ".jpeg", ".png", ".webp",
    )
    _skip_lz4 = local_path.suffix.lower() in _INCOMPRESSIBLE_SUFFIX

    bytes_sent = 0
    chunk_index = resume_offset // CHUNK_SIZE_RAW
    t_start = time.monotonic()
    in_flight = {}   # chunk_index -> uncompressed_size

    with open(local_path, "rb") as f:
        if resume_offset > 0:
            f.seek(resume_offset)

        more_chunks = True

        while more_chunks or in_flight:
            # Fill the sliding window
            while more_chunks and len(in_flight) < WINDOW:
                raw_block = f.read(CHUNK_SIZE_RAW)
                if not raw_block:
                    more_chunks = False
                    break

                uncompressed_size = len(raw_block)

                if _skip_lz4:
                    compressed = raw_block
                else:
                    compressed = lz4b.compress(raw_block, store_size=False)
                    if len(compressed) >= uncompressed_size * 0.98:
                        compressed = raw_block

                crc = zlib.crc32(compressed) & 0xFFFFFFFF
                chunk_hdr = struct.pack(FMT_CHUNK, chunk_index,
                                        len(compressed), uncompressed_size, crc)

                # Combined header + payload in ONE send (avoids tiny TCP packet overhead)
                packet = chunk_hdr + compressed
                throttle.consume(len(packet))
                _sendall_exact(sock, packet)

                in_flight[chunk_index] = uncompressed_size
                chunk_index += 1

            if not in_flight:
                break

            # Read next ACK (normal or telemetry)
            ack_idx, ack_status, t_net, t_cpu, t_sd, bars, cpu_pct = _read_ack(sock, use_telemetry)

            if ack_status in (ACK_OK, ACK_WITH_TELEMETRY):
                uncomp_sz = in_flight.pop(ack_idx, CHUNK_SIZE_RAW)
                bytes_sent += uncomp_sz
                elapsed = time.monotonic() - t_start
                effective_mbps = (bytes_sent / 1_048_576) / max(elapsed, 1e-9)
                if progress_cb:
                    progress_cb(resume_offset + bytes_sent, file_size, effective_mbps)
                # Feed telemetry to auto-tuner
                if use_telemetry and tuner is not None and t_net > 0:
                    tuner.feed_telemetry(t_net, t_cpu, t_sd, bars, cpu_pct, nack_count=0)
            elif ack_status == ACK_NACK:
                raise ProtocolError(f"Chunk {ack_idx} failed CRC32 (NACK).")
            elif ack_status == ACK_ERROR_FS:
                raise ProtocolError(f"3DS filesystem error on chunk {ack_idx}.")

    return True


def send_directory_entry(sock: socket.socket, remote_path: str):
    """
    Notify the 3DS to create a directory.
    Sends a file header with type=1 and no data.
    """
    path_bytes = remote_path.encode("utf-8")
    dir_hdr = struct.pack(
        FMT_FILE,
        1,   # type: directory
        0,   # size
        0,   # resume_offset
        0,   # total_chunks
        len(path_bytes),
    )
    _sendall_exact(sock, dir_hdr)
    _sendall_exact(sock, path_bytes)
    # Wait for 3DS ready
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    status = struct.unpack(FMT_HS_RESP, resp)[0]
    if status not in (0, 3):   # 3 = dir already exists (OK)
        raise ProtocolError(f"3DS could not create directory '{remote_path}': code {status}")