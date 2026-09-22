/**
 * Zel.NeD — ZELNED Protocol Definitions (3DS side)
 * ==================================================
 * Must stay in sync with pc/protocol.py
 */
#pragma once
#include <stdint.h>

// ── Magic & Version ───────────────────────────────────────────────────────────
static const char ZELNED_MAGIC[6] = {'Z','E','L','N','E','D'};
#define ZELNED_VERSION    1
#define TCP_PORT          9503
#define UDP_BEACON_PORT   9504
#define CHUNK_SIZE_RAW    262144  // 256 KB — fits ARM9 FS cache cleanly; halves memcpy per chunk

// ── Session flags ─────────────────────────────────────────────────────────────
#define FLAG_LZ4            0x01
#define FLAG_CRC32          0x02
#define FLAG_RESUME         0x04
#define FLAG_TELEMETRY      0x08
#define FLAG_BENCHMARK_NET  0x10
#define FLAG_INSTALL_CIA    0x20

// ── Item types ────────────────────────────────────────────────────────────────
#define TYPE_FILE        0
#define TYPE_DIRECTORY   1
#define TYPE_CIA_INSTALL 2

// ── ACK status codes ──────────────────────────────────────────────────────────
#define ACK_OK             0
#define ACK_NACK           1   // CRC mismatch → PC will resend
#define ACK_ERROR_FS       2   // Filesystem / SD write error
#define ACK_DIR_OK         3   // Directory created (or already exists)
#define ACK_WITH_TELEMETRY 4

// ── 3DS handshake response codes ─────────────────────────────────────────────
#define HS_OK         0
#define HS_SD_FULL    1
#define HS_ERROR      2

// ── Network buffer size for socInit ──────────────────────────────────────────
#define SOC_BUFSIZE   (4 * 1024 * 1024)   // 4 MB — needed for large SO_RCVBUF with 512 KB chunks

// ── ARM11 system clock for tick→µs conversion ────────────────────────────────
// Old 3DS: 268 111 856 Hz   |   New 3DS (boosted): 804 333 568 Hz
// Use svcGetSystemTick() for high-resolution timing; divide by this constant.
#define SYSCLOCK_ARM11  268111856ULL

// Convert ARM11 ticks to microseconds (safe for values up to ~79 seconds)
static inline uint32_t ticks_to_us(uint64_t ticks) {
    return (uint32_t)((ticks * 1000000ULL) / SYSCLOCK_ARM11);
}

// ── Packed structs (must match pc/protocol.py FMT_* exactly) ─────────────────
#pragma pack(push, 1)

typedef struct {
    uint8_t  magic[6];
    uint8_t  version;
    uint8_t  flags;
    uint32_t throttle_kbps;
    uint32_t queue_count;
} ZelNedSessionHeader;   // 16 bytes

typedef struct {
    uint8_t  type;
    uint64_t uncompressed_size;
    uint64_t resume_offset;
    uint32_t total_chunks;
    uint16_t path_len;
} ZelNedFileHeader;      // 23 bytes

typedef struct {
    uint32_t chunk_index;
    uint32_t compressed_size;
    uint32_t uncompressed_size;
    uint32_t crc32;
} ZelNedChunkHeader;     // 16 bytes

typedef struct {
    uint32_t chunk_index;
    uint8_t  status;
} ZelNedChunkAck;        // 5 bytes  (status == ACK_OK | ACK_NACK | ACK_ERROR_FS | ACK_DIR_OK)

// Extended ACK with per-chunk telemetry (status == ACK_WITH_TELEMETRY).
// Sent only when FLAG_TELEMETRY is active in the session header.
typedef struct {
    uint32_t chunk_index;
    uint8_t  status;        // Always ACK_WITH_TELEMETRY (4)
    uint16_t t_net_us;      // Time to recv chunk bytes from socket   (µs, capped 65535)
    uint16_t t_cpu_us;      // Time for CRC32 check + LZ4 decompress  (µs, capped 65535)
    uint16_t t_sd_us;       // Time for SD write of PREVIOUS chunk    (µs, capped 65535)
    uint8_t  wifi_bars;     // Wi-Fi signal strength 0-3 (osGetWifiStrength)
    uint8_t  cpu_load_pct;  // Estimated CPU load 0-100%
    uint16_t reserved;      // Must be 0
} ZelNedChunkAckTelemetry;  // 15 bytes

typedef struct {
    uint8_t  magic[6];
    uint8_t  version;
    uint16_t tcp_port;
    char     name[32];
} ZelNedBeacon;          // 41 bytes

#pragma pack(pop)
