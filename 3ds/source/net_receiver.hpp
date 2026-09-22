/**
 * Zel.NeD — TCP Receiver (3DS)
 * ==============================
 * Listens on TCP port 9503.
 * Receives ZELNED sessions: handshake → files → chunks → ACK/NACK.
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
};

// Initialize receiver internal state and locks (must call from main thread before loop)
void receiver_init();

// Start the receiver (blocking — call from a dedicated thread)
void receiver_run();

// Get a snapshot of current stats (thread-safe)
ReceiverStats receiver_get_stats();

// Pause or resume receiving chunks
void receiver_set_paused(bool paused);
bool receiver_is_paused();

// Request the receiver to stop after the current transfer
void receiver_stop();
