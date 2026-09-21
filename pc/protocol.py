"""
Zel.NeD — ZELNED v1 Protocol (PC side)
========================================
Handles: LZ4 compression, CRC32 integrity, chunk packing,
         session handshake, throttle, and ACK/NACK loop.
"""

import struct
import socket
import time
import zlib
import lz4.frame as lz4f
import lz4.block as lz4b
from pathlib import Path
from typing import Optional, Callable

# ── Protocol Constants ─────────────────────────────────────────────────────────
MAGIC            = b"ZELNED"
VERSION          = 1
TCP_PORT         = 9503
UDP_BEACON_PORT  = 9504
CHUNK_SIZE_RAW   = 65536        # 64 KB uncompressed chunks
CONNECT_TIMEOUT  = 8.0          # seconds
ACK_TIMEOUT      = 10.0         # seconds to wait for 3DS ACK per chunk
MAX_RETRIES      = 3            # max NACK retries before aborting

# Session flags (bitmask)
FLAG_LZ4         = 0x01
FLAG_CRC32       = 0x02
FLAG_RESUME      = 0x04

# Chunk ACK status codes (must match 3DS side)
ACK_OK           = 0
ACK_NACK         = 1   # CRC mismatch → resend
ACK_ERROR_FS     = 2   # SD write error

# ── Struct Formats (little-endian, packed) ─────────────────────────────────────
# Session header: magic(6) + version(1) + flags(1) + throttle_kbps(4) + queue_count(4) = 16 bytes
FMT_SESSION  = "<6sBBII"
SESSION_SIZE = struct.calcsize(FMT_SESSION)

# File header: type(1) + uncompressed_size(8) + resume_offset(8) + total_chunks(4) + path_len(2) = 23 bytes
FMT_FILE     = "<BQQIH"
FILE_HDR_SIZE = struct.calcsize(FMT_FILE)

# Chunk header: chunk_index(4) + compressed_size(4) + uncompressed_size(4) + crc32(4) = 16 bytes
FMT_CHUNK    = "<IIII"
CHUNK_HDR_SIZE = struct.calcsize(FMT_CHUNK)

# Chunk ACK: chunk_index(4) + status(1) = 5 bytes
FMT_ACK      = "<IB"
ACK_SIZE     = struct.calcsize(FMT_ACK)

# 3DS handshake response: status(1) = 1 byte
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
            return   # no limit
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self.bytes_per_sec, self._tokens + elapsed * self.bytes_per_sec)
        if self._tokens < n_bytes:
            sleep_time = (n_bytes - self._tokens) / self.bytes_per_sec
            time.sleep(sleep_time)
            self._tokens = 0
        else:
            self._tokens -= n_bytes


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


def send_session_header(sock: socket.socket, throttle_kbps: int, queue_count: int,
                        resume: bool = False):
    """
    Send the ZELNED session handshake and wait for the 3DS acknowledgement.
    Raises ProtocolError if the 3DS rejects the session.
    """
    flags = FLAG_LZ4 | FLAG_CRC32
    if resume:
        flags |= FLAG_RESUME
    header = struct.pack(FMT_SESSION, MAGIC, VERSION, flags, throttle_kbps, queue_count)
    _sendall_exact(sock, header)
    # Wait for 3DS to respond with 1-byte status
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    status = struct.unpack(FMT_HS_RESP, resp)[0]
    if status == 1:
        raise ProtocolError("3DS rejected session: SD card full.")
    if status != 0:
        raise ProtocolError(f"3DS rejected session: unknown error code {status}.")


def send_file(
    sock: socket.socket,
    local_path: Path,
    remote_path: str,
    throttle: ThrottleController,
    resume_offset: int = 0,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
) -> bool:
    """
    Send a single file over an established ZELNED session.

    Args:
        sock:          Connected TCP socket to the 3DS.
        local_path:    Path to the file on the PC.
        remote_path:   Relative destination path on sdmc:/ (e.g. '3ds/game.cia').
        throttle:      ThrottleController instance (kbps=0 for unlimited).
        resume_offset: Byte offset to resume from (0 = start from beginning).
        progress_cb:   Called with (bytes_sent, total_bytes, effective_mbps) per chunk.

    Returns:
        True on success, False on unrecoverable error.
    """
    file_size = local_path.stat().st_size
    path_bytes = remote_path.encode("utf-8")

    # ── Calculate total chunks (accounting for resume) ───────────────────────
    total_bytes_to_send = file_size - resume_offset
    total_chunks = (total_bytes_to_send + CHUNK_SIZE_RAW - 1) // CHUNK_SIZE_RAW

    # ── Send file header ─────────────────────────────────────────────────────
    file_hdr = struct.pack(
        FMT_FILE,
        0,                 # type: regular file
        file_size,
        resume_offset,
        total_chunks,
        len(path_bytes),
    )
    _sendall_exact(sock, file_hdr)
    _sendall_exact(sock, path_bytes)

    # ── Wait for 3DS ready acknowledgement ───────────────────────────────────
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    status = struct.unpack(FMT_HS_RESP, resp)[0]
    if status != 0:
        raise ProtocolError(f"3DS not ready for file '{remote_path}': code {status}")

    # ── Stream chunks ─────────────────────────────────────────────────────────
    bytes_sent = 0
    chunk_index = resume_offset // CHUNK_SIZE_RAW

    with open(local_path, "rb") as f:
        if resume_offset > 0:
            f.seek(resume_offset)

        while True:
            raw_block = f.read(CHUNK_SIZE_RAW)
            if not raw_block:
                break

            uncompressed_size = len(raw_block)

            # Compress with LZ4 block (fastest mode, no frame overhead)
            compressed = lz4b.compress(raw_block, store_size=False)
            crc = zlib.crc32(compressed) & 0xFFFFFFFF

            chunk_hdr = struct.pack(
                FMT_CHUNK,
                chunk_index,
                len(compressed),
                uncompressed_size,
                crc,
            )

            # Retry loop for NACK
            for attempt in range(MAX_RETRIES):
                throttle.consume(len(chunk_hdr) + len(compressed))
                t0 = time.monotonic()
                _sendall_exact(sock, chunk_hdr)
                _sendall_exact(sock, compressed)

                # Wait for ACK/NACK from 3DS
                sock.settimeout(ACK_TIMEOUT)
                ack_raw = _recvall_exact(sock, ACK_SIZE)
                sock.settimeout(None)
                ack_idx, ack_status = struct.unpack(FMT_ACK, ack_raw)

                if ack_status == ACK_OK:
                    elapsed = time.monotonic() - t0
                    effective_mbps = (uncompressed_size / 1_048_576) / max(elapsed, 1e-9)
                    bytes_sent += uncompressed_size
                    if progress_cb:
                        progress_cb(resume_offset + bytes_sent, file_size, effective_mbps)
                    break
                elif ack_status == ACK_NACK:
                    # CRC mismatch: resend this chunk
                    continue
                elif ack_status == ACK_ERROR_FS:
                    raise ProtocolError(f"3DS filesystem error on chunk {chunk_index}.")
            else:
                # Exhausted retries
                raise ProtocolError(
                    f"Chunk {chunk_index} failed after {MAX_RETRIES} retries (NACK)."
                )

            chunk_index += 1

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


def connect_to_3ds(ip: str, port: int = TCP_PORT) -> socket.socket:
    """
    Open a TCP connection to the 3DS receiver.
    Returns the connected socket.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((ip, port))
    sock.settimeout(None)
    return sock
