"""
Zel.NeD - Network & SD Benchmark
==================================
Runs two isolated speed tests against the 3DS to identify the dominant
bottleneck before committing to a real file transfer.

Test 1 — Network (RAM sink):
    Sends 32 MB of pseudo-random data. The 3DS receives it and discards
    to RAM. No SD write happens. Measures pure Wi-Fi throughput.

Test 2 — Combined (SD write):
    Sends 64 MB of data. The 3DS writes to SD as in a real transfer.
    Compares against Test 1 to isolate SD overhead.

Both tests use FLAG_TELEMETRY so the BottleneckAnalyzer gets warm data.

Usage:
    from benchmark import BenchmarkRunner
    runner = BenchmarkRunner(ip="192.168.1.47")
    result = runner.run(progress_cb=lambda pct, msg: print(pct, msg))
    print(result.summary())
"""

from __future__ import annotations

import os
import socket
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Optional, Callable

import lz4.block as lz4b

from protocol import (
    MAGIC, VERSION, TCP_PORT, CHUNK_SIZE_RAW,
    FLAG_CRC32, FLAG_TELEMETRY, FLAG_BENCHMARK_NET,
    FMT_SESSION, FMT_FILE, FMT_CHUNK, FMT_ACK,
    ACK_SIZE, HS_RESP_SIZE, CONNECT_TIMEOUT,
    _sendall_exact, _recvall_exact, connect_to_3ds,
    ProtocolError,
)
from analyzer import BottleneckAnalyzer, AnalysisResult, Bottleneck

# Size of extended telemetry ACK (15 bytes)
TELEM_ACK_SIZE = 15   # ZelNedChunkAckTelemetry
FMT_TELEM_ACK  = "<IBHHHBB H"  # chunk_index(4) + status(1) + net(2) + cpu(2) + sd(2) + bars(1) + load(1) + reserved(2)

BENCHMARK_NET_MB  = 32   # MB of data for the pure-network test
BENCHMARK_FULL_MB = 64   # MB for the combined net+SD test
WINDOW_SIZE       = 6    # chunks in flight


def _read_telem_ack(sock: socket.socket, use_telemetry: bool):
    """
    Read either a normal ACK (5 bytes) or telemetry ACK (15 bytes).
    Returns (chunk_index, status, t_net_us, t_cpu_us, t_sd_us, wifi_bars, cpu_pct).
    For normal ACKs, timing fields are all 0.
    """
    if use_telemetry:
        raw = _recvall_exact(sock, TELEM_ACK_SIZE)
        # < I B H H H B B H
        fields = struct.unpack("<IBHHHBBH", raw)
        return fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6]
    else:
        raw = _recvall_exact(sock, ACK_SIZE)
        idx, status = struct.unpack("<IB", raw)
        return idx, status, 0, 0, 0, 0, 0


def _send_benchmark_session(sock: socket.socket, chunk_count: int,
                             is_net_only: bool, use_telemetry: bool):
    """Send session header for benchmark mode."""
    flags = FLAG_CRC32
    if is_net_only:   flags |= FLAG_BENCHMARK_NET
    if use_telemetry: flags |= FLAG_TELEMETRY
    hdr = struct.pack(FMT_SESSION, MAGIC, VERSION, flags, 0, 1)
    _sendall_exact(sock, hdr)
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    if resp[0] != 0:
        raise ProtocolError(f"3DS rejected benchmark session: code {resp[0]}")


def _send_benchmark_file_header(sock: socket.socket, total_bytes: int,
                                 chunk_count: int, path: str):
    """Send a fake file header for benchmark mode."""
    path_bytes = path.encode("utf-8")
    fhdr = struct.pack("<BQQIH",
                       0,            # type: file
                       total_bytes,  # uncompressed_size
                       0,            # resume_offset
                       chunk_count,  # total_chunks
                       len(path_bytes))
    _sendall_exact(sock, fhdr)
    _sendall_exact(sock, path_bytes)
    resp = _recvall_exact(sock, HS_RESP_SIZE)
    if resp[0] != 0:
        raise ProtocolError(f"3DS not ready for benchmark file: code {resp[0]}")


def _run_single_test(ip: str, total_mb: int, is_net_only: bool,
                     use_telemetry: bool,
                     progress_cb: Optional[Callable[[float, str], None]],
                     analyzer: BottleneckAnalyzer) -> float:
    """
    Run one benchmark phase.
    Returns effective throughput in MB/s (uncompressed bytes / elapsed time).
    """
    total_bytes = total_mb * 1024 * 1024
    chunk_count = (total_bytes + CHUNK_SIZE_RAW - 1) // CHUNK_SIZE_RAW

    sock = connect_to_3ds(ip)
    try:
        _send_benchmark_session(sock, chunk_count, is_net_only, use_telemetry)
        label = "NET-only" if is_net_only else "NET+SD"
        _send_benchmark_file_header(sock, total_bytes, chunk_count, f"__benchmark_{label}__")

        # Generate a fixed random block (reused for all chunks — CPU cost is negligible)
        raw_block = os.urandom(CHUNK_SIZE_RAW)
        crc = zlib.crc32(raw_block) & 0xFFFFFFFF

        bytes_sent = 0
        t_start = time.monotonic()
        chunk_index = 0
        in_flight = {}

        while chunk_index < chunk_count or in_flight:
            # Fill window
            while chunk_index < chunk_count and len(in_flight) < WINDOW_SIZE:
                chunk_hdr = struct.pack(FMT_CHUNK,
                                        chunk_index,
                                        CHUNK_SIZE_RAW,   # compressed_size == raw (no compression in bench)
                                        CHUNK_SIZE_RAW,
                                        crc)
                _sendall_exact(sock, chunk_hdr + raw_block)
                in_flight[chunk_index] = CHUNK_SIZE_RAW
                chunk_index += 1

            if not in_flight:
                break

            # Read one ACK
            sock.settimeout(30.0)
            idx, status, t_net, t_cpu, t_sd, bars, cpu_pct = _read_telem_ack(sock, use_telemetry)
            sock.settimeout(None)

            if status in (0, 4):   # ACK_OK or ACK_WITH_TELEMETRY
                in_flight.pop(idx, None)
                bytes_sent += CHUNK_SIZE_RAW
                if use_telemetry and t_net > 0:
                    analyzer.feed(t_net, t_cpu, t_sd, bars, cpu_pct)
            elif status == 1:      # NACK: resend not implemented in benchmark
                in_flight.pop(idx, None)

            elapsed = time.monotonic() - t_start
            pct = bytes_sent / total_bytes
            mbps = (bytes_sent / 1_048_576) / max(elapsed, 1e-9)
            if progress_cb:
                progress_cb(pct, f"{label}: {mbps:.2f} MB/s  ({bytes_sent//1024//1024}/{total_mb} MB)")

    finally:
        sock.close()

    elapsed = time.monotonic() - t_start
    return (bytes_sent / 1_048_576) / max(elapsed, 1e-9)


@dataclass
class BenchmarkResult:
    net_mbps:    float   # pure Wi-Fi speed (RAM sink)
    full_mbps:   float   # Wi-Fi + SD combined speed
    sd_mbps:     float   # estimated SD write speed (derived)
    analysis:    AnalysisResult

    def summary(self) -> str:
        sd_est = self.sd_mbps
        lines = [
            "=== Zel.NeD Network + SD Benchmark ===",
            f"  Pure Wi-Fi (RAM):   {self.net_mbps:.2f} MB/s",
            f"  Wi-Fi + SD write:   {self.full_mbps:.2f} MB/s",
            f"  SD write estimate:  {sd_est:.2f} MB/s",
            f"  Bottleneck:         {self.analysis.bottleneck.label()}",
            f"  Recommendation:     {self.analysis.recommendation()}",
        ]
        return "\n".join(lines)


class BenchmarkRunner:
    """
    Orchestrates both benchmark phases and returns a combined BenchmarkResult.
    """

    def __init__(self, ip: str, port: int = TCP_PORT):
        self.ip   = ip
        self.port = port

    def run(self, progress_cb: Optional[Callable[[float, str], None]] = None,
            use_telemetry: bool = True) -> BenchmarkResult:
        """
        Run the full benchmark (both phases).

        Args:
            progress_cb: called with (fraction 0-1, status string) periodically.
            use_telemetry: if True, collect per-chunk telemetry for analysis.

        Returns:
            BenchmarkResult with speed measurements and bottleneck analysis.
        """
        analyzer = BottleneckAnalyzer(window=100)

        if progress_cb:
            progress_cb(0.0, "Phase 1/2: Network-only test (RAM sink, no SD write)...")

        net_mbps = _run_single_test(
            self.ip, BENCHMARK_NET_MB,
            is_net_only=True, use_telemetry=use_telemetry,
            progress_cb=progress_cb, analyzer=analyzer,
        )

        if progress_cb:
            progress_cb(0.5, "Phase 2/2: Network + SD write test...")

        full_mbps = _run_single_test(
            self.ip, BENCHMARK_FULL_MB,
            is_net_only=False, use_telemetry=use_telemetry,
            progress_cb=progress_cb, analyzer=analyzer,
        )

        # Estimated SD speed:
        # 1/full = 1/net + 1/sd  =>  sd = net*full / (net - full)
        if net_mbps > full_mbps > 0:
            sd_mbps = (net_mbps * full_mbps) / (net_mbps - full_mbps)
        else:
            sd_mbps = full_mbps  # SD is not the bottleneck

        analysis = analyzer.classify()

        if progress_cb:
            progress_cb(1.0, "Benchmark complete.")

        return BenchmarkResult(
            net_mbps=net_mbps,
            full_mbps=full_mbps,
            sd_mbps=sd_mbps,
            analysis=analysis,
        )
