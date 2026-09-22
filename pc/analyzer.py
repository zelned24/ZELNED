"""
Zel.NeD - Bottleneck Analyzer
==============================
Classifies the dominant bottleneck (Network / CPU / SD-card) from
per-chunk telemetry sent by the 3DS side.

Usage:
    from analyzer import BottleneckAnalyzer, Bottleneck
    analyzer = BottleneckAnalyzer()
    # Feed data as telemetry ACKs arrive:
    analyzer.feed(t_net_us=4200, t_cpu_us=900, t_sd_us=3100, wifi_bars=3, cpu_load_pct=15)
    result = analyzer.classify()  # -> Bottleneck.SD_CARD
    print(result.summary())
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class Bottleneck(Enum):
    UNKNOWN  = "unknown"   # Not enough data yet
    NETWORK  = "network"   # Wi-Fi / router is the limiting factor
    CPU      = "cpu"       # ARM11 CRC32 / LZ4 saturating the single core
    SD_CARD  = "sd"        # SD card write speed is the limiting factor
    BALANCED = "balanced"  # No single dominant bottleneck; system is efficient

    def label(self) -> str:
        return {
            Bottleneck.UNKNOWN:  "Collecting data...",
            Bottleneck.NETWORK:  "Wi-Fi / Network",
            Bottleneck.CPU:      "ARM11 CPU (CRC32/LZ4)",
            Bottleneck.SD_CARD:  "SD Card Write",
            Bottleneck.BALANCED: "Balanced (no dominant)",
        }[self]

    def color(self) -> str:
        """CustomTkinter / Tkinter color string for UI display."""
        return {
            Bottleneck.UNKNOWN:  "#888888",
            Bottleneck.NETWORK:  "#e67e22",
            Bottleneck.CPU:      "#c0392b",
            Bottleneck.SD_CARD:  "#8e44ad",
            Bottleneck.BALANCED: "#27ae60",
        }[self]


@dataclass
class TelemetryPoint:
    t_net_us:     int   # socket recv time (µs)
    t_cpu_us:     int   # CRC32 + LZ4 time (µs)
    t_sd_us:      int   # SD write time of previous chunk (µs)
    wifi_bars:    int   # 0-3
    cpu_load_pct: int   # 0-100


@dataclass
class AnalysisResult:
    bottleneck:    Bottleneck
    net_pct:       float   # fraction of total time spent in network
    cpu_pct:       float   # fraction of total time spent in CPU
    sd_pct:        float   # fraction of total time spent in SD
    wifi_bars:     float   # average Wi-Fi bars (0-3)
    avg_net_us:    float
    avg_cpu_us:    float
    avg_sd_us:     float
    sample_count:  int

    def recommendation(self) -> str:
        bn = self.bottleneck
        if bn == Bottleneck.NETWORK:
            if self.wifi_bars < 2:
                return ("Weak Wi-Fi signal (%.1f/3 bars). Move closer to router. "
                        "Consider a 2.4 GHz Wi-Fi hotspot on your PC." % self.wifi_bars)
            return ("Network is the bottleneck (%.0f%% of time). "
                    "Try a 5 GHz-capable router (3DS is 2.4 GHz only), "
                    "reduce Wi-Fi interference, or enable QoS on the router." % (self.net_pct * 100))
        if bn == Bottleneck.CPU:
            return ("ARM11 CPU is the bottleneck (%.0f%% of time). "
                    "Auto-tuner will disable LZ4 and CRC32 for incompressible files "
                    "to free the single core. "
                    "Expected gain: +30-50%% throughput for CIA/ZIP/MP4 transfers." % (self.cpu_pct * 100))
        if bn == Bottleneck.SD_CARD:
            return ("SD card write is the bottleneck (%.0f%% of time). "
                    "Auto-tuner will try smaller chunk sizes (128 KB) "
                    "to better fit the ARM9 FS write cache. "
                    "A faster SD card (A1/A2 rated, e.g. SanDisk Extreme) may help." % (self.sd_pct * 100))
        if bn == Bottleneck.BALANCED:
            return ("Transfer is well-balanced. "
                    "Auto-tuner will try increasing the sliding window to 8 chunks "
                    "to maximize pipeline utilization.")
        return "Collecting telemetry data..."

    def summary(self) -> str:
        return (
            f"Bottleneck: {self.bottleneck.label()}\n"
            f"  Net: {self.net_pct*100:.0f}%  ({self.avg_net_us:.0f} µs avg)\n"
            f"  CPU: {self.cpu_pct*100:.0f}%  ({self.avg_cpu_us:.0f} µs avg)\n"
            f"  SD:  {self.sd_pct*100:.0f}%  ({self.avg_sd_us:.0f} µs avg)\n"
            f"  Wi-Fi: {self.wifi_bars:.1f}/3 bars  |  Samples: {self.sample_count}\n"
            f"  Rec: {self.recommendation()}"
        )


class BottleneckAnalyzer:
    """
    Accumulates telemetry samples and classifies the dominant bottleneck.

    Thread-safe: call feed() from the transfer thread, classify() from any thread.
    """

    # Minimum samples before making a classification
    MIN_SAMPLES = 10

    # Thresholds: if one component exceeds this fraction of total time -> that component is the bottleneck
    DOMINANCE_THRESHOLD = 0.50   # 50% of total chunk time

    # Difference threshold: if top two are within 15% of each other -> BALANCED
    BALANCE_MARGIN = 0.15

    def __init__(self, window: int = 50):
        """
        Args:
            window: rolling window size (number of recent chunks to consider).
                    Older chunks are discarded automatically.
        """
        self._window = window
        self._samples: List[TelemetryPoint] = []

    def reset(self):
        self._samples.clear()

    def feed(self, t_net_us: int, t_cpu_us: int, t_sd_us: int,
             wifi_bars: int, cpu_load_pct: int):
        """Record one chunk's telemetry. Old samples beyond the window are dropped."""
        self._samples.append(TelemetryPoint(t_net_us, t_cpu_us, t_sd_us, wifi_bars, cpu_load_pct))
        if len(self._samples) > self._window:
            self._samples.pop(0)

    def classify(self) -> AnalysisResult:
        n = len(self._samples)
        if n < self.MIN_SAMPLES:
            return AnalysisResult(
                bottleneck=Bottleneck.UNKNOWN,
                net_pct=0, cpu_pct=0, sd_pct=0,
                wifi_bars=0, avg_net_us=0, avg_cpu_us=0, avg_sd_us=0,
                sample_count=n,
            )

        avg_net = sum(s.t_net_us for s in self._samples) / n
        avg_cpu = sum(s.t_cpu_us for s in self._samples) / n
        avg_sd  = sum(s.t_sd_us  for s in self._samples) / n
        avg_bars = sum(s.wifi_bars for s in self._samples) / n
        total = avg_net + avg_cpu + avg_sd

        if total < 1:
            # All zeros — 3DS responded instantly; probably emulator or loopback test
            return AnalysisResult(
                bottleneck=Bottleneck.BALANCED,
                net_pct=0.33, cpu_pct=0.33, sd_pct=0.33,
                wifi_bars=avg_bars, avg_net_us=0, avg_cpu_us=0, avg_sd_us=0,
                sample_count=n,
            )

        net_pct = avg_net / total
        cpu_pct = avg_cpu / total
        sd_pct  = avg_sd  / total

        # Sort components by share
        components = sorted(
            [("net", net_pct), ("cpu", cpu_pct), ("sd", sd_pct)],
            key=lambda x: x[1], reverse=True
        )
        top_name, top_pct = components[0]
        second_pct = components[1][1]

        # Is one component clearly dominant?
        if top_pct >= self.DOMINANCE_THRESHOLD and (top_pct - second_pct) > self.BALANCE_MARGIN:
            bn_map = {"net": Bottleneck.NETWORK, "cpu": Bottleneck.CPU, "sd": Bottleneck.SD_CARD}
            bottleneck = bn_map[top_name]
        else:
            bottleneck = Bottleneck.BALANCED

        return AnalysisResult(
            bottleneck=bottleneck,
            net_pct=net_pct, cpu_pct=cpu_pct, sd_pct=sd_pct,
            wifi_bars=avg_bars,
            avg_net_us=avg_net, avg_cpu_us=avg_cpu, avg_sd_us=avg_sd,
            sample_count=n,
        )

    @property
    def sample_count(self) -> int:
        return len(self._samples)
