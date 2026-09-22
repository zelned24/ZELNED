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
#define FLAG_LZ4    0x01
#define FLAG_CRC32  0x02
#define FLAG_RESUME 0x04

// ── Item types ────────────────────────────────────────────────────────────────
#define TYPE_FILE      0
#define TYPE_DIRECTORY 1

// ── ACK status codes ──────────────────────────────────────────────────────────
#define ACK_OK       0
#define ACK_NACK     1   // CRC mismatch → PC will resend
#define ACK_ERROR_FS 2   // Filesystem / SD write error
#define ACK_DIR_OK   3   // Directory created (or already exists)

// ── 3DS handshake response codes ─────────────────────────────────────────────
#define HS_OK         0
#define HS_SD_FULL    1
#define HS_ERROR      2

// ── Network buffer size for socInit ──────────────────────────────────────────
#define SOC_BUFSIZE   (4 * 1024 * 1024)   // 4 MB — needed for large SO_RCVBUF with 512 KB chunks

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
} ZelNedChunkAck;        // 5 bytes

typedef struct {
    uint8_t  magic[6];
    uint8_t  version;
    uint16_t tcp_port;
    char     name[32];
} ZelNedBeacon;          // 41 bytes

#pragma pack(pop)
