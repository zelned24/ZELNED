"""
Zel.NeD - Auto-Tuner
=====================
Dynamically adjusts transfer parameters (chunk_size, window_size, use_lz4,
use_crc32) based on real-time telemetry from the 3DS.

Design goals:
  - Conservative: never disables CRC32 if there have been NACK errors.
  - Stable: cooldown of 20 chunks between adjustments to avoid oscillation.
  - Safe: parameters are applied at file boundaries, not mid-chunk.
  - Predictable: prints a log line whenever a parameter changes.

Usage in protocol.py / zelned_gui.py:
    tuner = AutoTuner()
    tuner.feed_telemetry(t_net_us, t_cpu_us, t_sd_us, wifi_bars, cpu_load_pct, nack_count)
    params = tuner.get_params()   # TransferParams(chunk_size=262144, window=6, ...)
"""

from __future__ import annotations
import threading
from dataclasses import dataclass
from analyzer import BottleneckAnalyzer, Bottleneck, AnalysisResult


@dataclass
class TransferParams:
    """Current active transfer parameters."""
    chunk_size:  int   = 262144   # bytes; 128 KB, 256 KB, or 512 KB
    window_size: int   = 6        # chunks in flight before blocking for ACK
    use_lz4:     bool  = True
    use_crc32:   bool  = True

    def session_flags(self) -> int:
        """Build the flags byte for ZelNedSessionHeader."""
        flags = 0x00
        if self.use_lz4:   flags |= 0x01   # FLAG_LZ4
        if self.use_crc32: flags |= 0x02   # FLAG_CRC32
        return flags

    def describe(self) -> str:
        lz = "ON" if self.use_lz4   else "OFF"
        cr = "ON" if self.use_crc32 else "OFF"
        return (f"chunk={self.chunk_size//1024}KB  "
                f"window={self.window_size}  "
                f"LZ4={lz}  CRC32={cr}")


# Candidate values
_CHUNK_SIZES  = [131072, 262144, 524288]   # 128 KB, 256 KB, 512 KB
_WINDOW_SIZES = [4, 6, 8, 10]


class AutoTuner:
    """
    Feeds from BottleneckAnalyzer and adjusts TransferParams accordingly.

    Thread-safe: feed_telemetry() may be called from the transfer thread;
    get_params() may be called from the GUI thread.
    """

    # Minimum chunks to collect before first auto-tune
    WARMUP_CHUNKS = 20

    # Minimum chunks between consecutive adjustments (cooldown)
    COOLDOWN_CHUNKS = 20

    def __init__(self, initial_params: TransferParams | None = None):
        self._lock   = threading.Lock()
        self._params = initial_params or TransferParams()
        self._analyzer = BottleneckAnalyzer(window=50)
        self._total_chunks     = 0   # cumulative chunk count across all files
        self._last_tune_chunk  = 0   # chunk index of last auto-tune action
        self._nack_since_last  = 0   # NACKs since last CRC32 evaluation
        self._history: list    = []  # log of tuning decisions

    # ── External API ──────────────────────────────────────────────────────────

    def reset_file(self):
        """Call at the start of each new file to reset per-file NACK count."""
        with self._lock:
            self._nack_since_last = 0

    def feed_telemetry(self, t_net_us: int, t_cpu_us: int, t_sd_us: int,
                       wifi_bars: int, cpu_load_pct: int, nack_count: int = 0):
        """
        Record one chunk's telemetry and optionally trigger an auto-tune.

        Args:
            t_net_us:     µs spent receiving chunk bytes from socket
            t_cpu_us:     µs spent on CRC32 + LZ4 decompress
            t_sd_us:      µs spent waiting for SD card write of previous chunk
            wifi_bars:    Wi-Fi signal strength 0-3
            cpu_load_pct: estimated CPU load 0-100
            nack_count:   number of NACKs seen since last call (usually 0 or 1)
        """
        with self._lock:
            self._analyzer.feed(t_net_us, t_cpu_us, t_sd_us, wifi_bars, cpu_load_pct)
            self._nack_since_last += nack_count
            self._total_chunks += 1
            self._maybe_tune()

    def get_params(self) -> TransferParams:
        with self._lock:
            # Return a copy so callers don't race against tuning
            p = self._params
            return TransferParams(
                chunk_size  = p.chunk_size,
                window_size = p.window_size,
                use_lz4     = p.use_lz4,
                use_crc32   = p.use_crc32,
            )

    def get_history(self) -> list:
        with self._lock:
            return list(self._history)

    # ── Internal tuning logic ─────────────────────────────────────────────────

    def _maybe_tune(self):
        """Called under self._lock. Evaluates whether to adjust parameters."""
        chunks_since = self._total_chunks - self._last_tune_chunk
        if chunks_since < max(self.WARMUP_CHUNKS, self.COOLDOWN_CHUNKS):
            return

        result = self._analyzer.classify()
        if result.bottleneck == Bottleneck.UNKNOWN:
            return   # not enough data yet

        changed = False
        p = self._params   # mutate in place (we hold the lock)

        # --- Rule 1: CPU bottleneck → disable LZ4 (and CRC32 if link is clean) ---
        if result.bottleneck == Bottleneck.CPU and result.cpu_pct > 0.55:
            if p.use_lz4:
                p = TransferParams(p.chunk_size, p.window_size, use_lz4=False, use_crc32=p.use_crc32)
                self._log("CPU bottleneck detected (%.0f%%). Disabled LZ4." % (result.cpu_pct * 100))
                changed = True
            # Disable CRC32 too only if there are zero NACKs (clean link)
            if p.use_crc32 and self._nack_since_last == 0 and result.cpu_pct > 0.65:
                p = TransferParams(p.chunk_size, p.window_size, p.use_lz4, use_crc32=False)
                self._log("Clean link, high CPU load. Disabled CRC32.")
                changed = True

        # --- Rule 2: SD bottleneck → reduce chunk size ---
        elif result.bottleneck == Bottleneck.SD_CARD and result.sd_pct > 0.55:
            idx = _CHUNK_SIZES.index(p.chunk_size) if p.chunk_size in _CHUNK_SIZES else 1
            if idx > 0:
                new_size = _CHUNK_SIZES[idx - 1]
                p = TransferParams(new_size, p.window_size, p.use_lz4, p.use_crc32)
                self._log("SD bottleneck (%.0f%%). Reduced chunk: %dKB -> %dKB." %
                          (result.sd_pct * 100, _CHUNK_SIZES[idx] // 1024, new_size // 1024))
                changed = True

        # --- Rule 3: Network bottleneck → increase window size ---
        elif result.bottleneck == Bottleneck.NETWORK and result.net_pct > 0.55:
            idx = _WINDOW_SIZES.index(p.window_size) if p.window_size in _WINDOW_SIZES else 1
            if idx < len(_WINDOW_SIZES) - 1:
                new_window = _WINDOW_SIZES[idx + 1]
                p = TransferParams(p.chunk_size, new_window, p.use_lz4, p.use_crc32)
                self._log("Network bottleneck (%.0f%%). Increased window: %d -> %d." %
                          (result.net_pct * 100, _WINDOW_SIZES[idx], new_window))
                changed = True

        # --- Rule 4: Balanced → try increasing window ---
        elif result.bottleneck == Bottleneck.BALANCED:
            idx = _WINDOW_SIZES.index(p.window_size) if p.window_size in _WINDOW_SIZES else 1
            if idx < len(_WINDOW_SIZES) - 1:
                new_window = _WINDOW_SIZES[idx + 1]
                p = TransferParams(p.chunk_size, new_window, p.use_lz4, p.use_crc32)
                self._log("Balanced profile. Increased window: %d -> %d." %
                          (_WINDOW_SIZES[idx], new_window))
                changed = True

        # --- Rule 5: Re-enable CRC32 if we had NACKs and it's off ---
        if self._nack_since_last > 0 and not p.use_crc32:
            p = TransferParams(p.chunk_size, p.window_size, p.use_lz4, use_crc32=True)
            self._log("NACK detected with CRC32 off. Re-enabled CRC32.")
            changed = True

        if changed:
            self._params = p
            self._last_tune_chunk = self._total_chunks
            self._nack_since_last = 0

    def _log(self, msg: str):
        entry = f"[Chunk {self._total_chunks:5d}] {msg}"
        self._history.append(entry)
        # Keep last 100 entries
        if len(self._history) > 100:
            self._history.pop(0)
        print(f"[AutoTuner] {entry}")
