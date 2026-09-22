/**
 * Zel.NeD - TCP Receiver (3DS)
 * ==============================
 * Listens on TCP port 9503.
 * Receives ZELNED sessions: handshake -> files -> chunks -> ACK/NACK.
 */
#pragma once
#include <stdint.h>

struct ReceiverStats {
    uint64_t bytes_received;
    uint64_t bytes_written;
    uint32_t chunks_ok;
    uint32_t chunks_nack;
    float    net_kbps;
    float    eff_mbps;
    uint32_t current_chunk;
    uint32_t total_chunks;
    char     current_file[256];
    int      queue_pos;
    int      queue_total;
    bool     connected;
    bool     paused;
    // Telemetry fields (populated when FLAG_TELEMETRY is set in the session)
    bool     telemetry_active;
    uint16_t telem_net_us;    // EMA of socket recv time per chunk (us)
    uint16_t telem_cpu_us;    // EMA of CRC32 + LZ4 decompress time (us)
    uint16_t telem_sd_us;     // EMA of SD write time per chunk (us)
    uint8_t  wifi_bars;       // Wi-Fi signal strength 0-3
    uint8_t  cpu_load_pct;    // Estimated CPU load 0-100%
};

// Initialize receiver internal state and locks (must call from main thread before loop)
void receiver_init();

// Start the receiver (blocking - call from a dedicated thread)
void receiver_run();

// Get a snapshot of current stats (thread-safe)
ReceiverStats receiver_get_stats();

// Pause or resume receiving chunks
void receiver_set_paused(bool paused);
bool receiver_is_paused();

// Request the receiver to stop after the current transfer
void receiver_stop();
