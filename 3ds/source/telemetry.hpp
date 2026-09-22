/**
 * Zel.NeD — Per-Chunk Transfer Telemetry (3DS)
 * =============================================
 * Measures T_net (socket recv), T_cpu (CRC32 + LZ4), and T_sd (SD card write)
 * for each chunk using high-resolution ARM11 system ticks.
 *
 * Usage pattern in net_receiver.cpp:
 *   uint64_t t0 = telemetry_tick();
 *   // ... recv bytes from socket ...
 *   uint64_t t1 = telemetry_tick();
 *   // ... CRC + decompress ...
 *   uint64_t t2 = telemetry_tick();
 *   // ... wait for previous SD write done ...
 *   uint64_t t3 = telemetry_tick();
 *
 *   telemetry_record(t1-t0, t2-t1, t3-t2);   // net, cpu, sd
 *   telemetry_fill(&ack);                      // fill the ACK struct
 *
 * All public functions are thread-safe (called only from the receiver thread).
 */
#pragma once
#include <3ds.h>
#include "protocol.hpp"
#include <stdint.h>

// Returns the current ARM11 tick counter (268 MHz on Old 3DS).
static inline uint64_t telemetry_tick() {
    return svcGetSystemTick();
}

// Cap a µs value to fit in uint16_t (max ~65 ms per chunk — anything above is capped)
static inline uint16_t cap16(uint32_t us) {
    return (us > 65535u) ? 65535u : (uint16_t)us;
}

// ── Running accumulator for the last N chunks ─────────────────────────────────
struct TelemetrySample {
    uint32_t t_net_us;
    uint32_t t_cpu_us;
    uint32_t t_sd_us;
    uint8_t  wifi_bars;
};

// Reset the accumulator (call at start of each file)
void telemetry_reset();

// Record measurements for one completed chunk.
//   net_ticks  — ticks spent in recv_exact() for header + payload
//   cpu_ticks  — ticks spent in CRC32 + LZ4_decompress_safe()
//   sd_ticks   — ticks spent waiting for the PREVIOUS chunk's SD write to finish
void telemetry_record(uint64_t net_ticks, uint64_t cpu_ticks, uint64_t sd_ticks);

// Fill a ZelNedChunkAckTelemetry struct with the latest measurements.
// wifi_bars is sampled fresh from osGetWifiStrength() every call.
void telemetry_fill(ZelNedChunkAckTelemetry* out, uint32_t chunk_index);
