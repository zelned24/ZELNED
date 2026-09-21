/**
 * Zel.NeD — TCP Receiver Implementation (3DS)
 * =============================================
 * Full ZELNED v1 protocol receiver:
 *   handshake → per-file header → chunks → CRC32 → ACK/NACK → write SD
 */
#include "net_receiver.hpp"
#include "protocol.hpp"
#include "integrity.hpp"
#include "fs_writer.hpp"
#include "resume.hpp"
#include "lz4/lz4.h"
#include <3ds.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// ── Internal state ─────────────────────────────────────────────────────────────
static volatile bool    s_stop        = false;
static volatile bool    s_paused      = false;
static ReceiverStats    s_stats       = {};
static LightLock        s_stats_lock;

// Decompression buffer (allocated in LINEAR memory for DMA performance)
static uint8_t*  s_decomp_buf  = nullptr;
static uint8_t*  s_chunk_buf   = nullptr;
static const int DECOMP_BUF_SZ = LZ4_COMPRESSBOUND(CHUNK_SIZE_RAW) + 64;

// ── Helpers ────────────────────────────────────────────────────────────────────

static bool recv_exact(int sock, void* buf, size_t len) {
    uint8_t* ptr = static_cast<uint8_t*>(buf);
    size_t   remaining = len;
    while (remaining > 0) {
        int got = recv(sock, ptr, remaining, 0);
        if (got <= 0) return false;
        ptr       += got;
        remaining -= got;
    }
    return true;
}

static bool send_exact(int sock, const void* buf, size_t len) {
    const uint8_t* ptr = static_cast<const uint8_t*>(buf);
    size_t remaining = len;
    while (remaining > 0) {
        int sent = send(sock, ptr, remaining, 0);
        if (sent <= 0) return false;
        ptr       += sent;
        remaining -= sent;
    }
    return true;
}

static void send_byte(int sock, uint8_t b) {
    send(sock, &b, 1, 0);
}

// ── Per-file receive logic ─────────────────────────────────────────────────────

static bool receive_file(int sock, const ZelNedFileHeader& fhdr,
                         const std::string& remote_path, bool use_crc32,
                         bool use_resume) {
    // Update stats
    LightLock_Lock(&s_stats_lock);
    strncpy(s_stats.current_file, remote_path.c_str(), 255);
    s_stats.total_chunks   = fhdr.total_chunks;
    s_stats.current_chunk  = 0;
    LightLock_Unlock(&s_stats_lock);

    // Check space
    if (fhdr.uncompressed_size > 0) {
        uint64_t free_space = fs_free_bytes();
        if (free_space < fhdr.uncompressed_size) {
            send_byte(sock, HS_SD_FULL);
            return false;
        }
    }

    // Handle resume — only apply if the PC explicitly requested it
    uint64_t resume_offset = (use_resume) ? fhdr.resume_offset : 0;
    uint32_t start_chunk   = (resume_offset > 0) ? (resume_offset / CHUNK_SIZE_RAW) : 0;

    FsWriter writer;
    if (!writer.open(remote_path, resume_offset)) {
        send_byte(sock, HS_ERROR);
        return false;
    }
    send_byte(sock, HS_OK);  // Ready

    uint64_t bytes_written   = resume_offset;
    uint32_t chunks_received = start_chunk;
    uint64_t t_start         = osGetTime();

    // FIX #1: while loop instead of for+ci-- to avoid uint32_t wraparound at chunk 0
    // FIX #7: max NACK retries on 3DS side (5 per chunk) to avoid infinite loop
    uint32_t ci           = start_chunk;
    uint32_t nack_retries = 0;
    static const uint32_t MAX_NACK = 5;

    while (ci < fhdr.total_chunks && !s_stop) {
        while (s_paused && !s_stop) {
            svcSleepThread(50000000LL);  // 50ms pause sleep
        }
        if (s_stop) break;

        ZelNedChunkHeader chdr;
        if (!recv_exact(sock, &chdr, sizeof(chdr))) {
            writer.abort();
            return false;
        }

        // Receive compressed data — reject if size exceeds receive buffer
        if (chdr.compressed_size == 0 || chdr.compressed_size > (uint32_t)DECOMP_BUF_SZ) {
            ZelNedChunkAck ack = { chdr.chunk_index, ACK_ERROR_FS };
            send_exact(sock, &ack, sizeof(ack));
            writer.abort();
            return false;
        }

        // FIX #2: validate uncompressed_size to prevent buffer overflow in s_decomp_buf
        if (chdr.uncompressed_size == 0 || chdr.uncompressed_size > CHUNK_SIZE_RAW) {
            ZelNedChunkAck ack = { chdr.chunk_index, ACK_ERROR_FS };
            send_exact(sock, &ack, sizeof(ack));
            writer.abort();
            return false;
        }

        if (!recv_exact(sock, s_chunk_buf, chdr.compressed_size)) {
            writer.abort();
            return false;
        }

        // CRC32 integrity check
        if (use_crc32) {
            uint32_t computed = zelned_crc32(s_chunk_buf, chdr.compressed_size);
            if (computed != chdr.crc32) {
                ZelNedChunkAck ack = { chdr.chunk_index, ACK_NACK };
                send_exact(sock, &ack, sizeof(ack));
                LightLock_Lock(&s_stats_lock);
                s_stats.chunks_nack++;
                LightLock_Unlock(&s_stats_lock);
                // FIX #1: do NOT decrement ci — just retry (bounded by MAX_NACK)
                if (++nack_retries >= MAX_NACK) {
                    writer.abort();
                    return false;
                }
                continue;  // retry same ci without incrementing
            }
        }
        nack_retries = 0;  // reset on successful CRC

        // Decompress LZ4 or write raw if not compressed (adaptive compression)
        const uint8_t* write_ptr = s_decomp_buf;
        size_t write_len = chdr.uncompressed_size;

        if (chdr.compressed_size < chdr.uncompressed_size) {
            int decompressed = LZ4_decompress_safe(
                (const char*)s_chunk_buf,
                (char*)s_decomp_buf,
                (int)chdr.compressed_size,
                (int)chdr.uncompressed_size
            );
            if (decompressed != (int)chdr.uncompressed_size) {
                // Decompression mismatch — NACK for retransmit
                ZelNedChunkAck ack = { chdr.chunk_index, ACK_NACK };
                send_exact(sock, &ack, sizeof(ack));
                if (++nack_retries >= MAX_NACK) {
                    writer.abort();
                    return false;
                }
                continue;
            }
            write_len = (size_t)decompressed;
        } else {
            // Raw chunk: bypasses LZ4 decompression on 3DS ARM11 for files already compressed
            write_ptr = s_chunk_buf;
            write_len = chdr.compressed_size;
        }

        // Write block to SD
        if (!writer.write(write_ptr, write_len)) {
            ZelNedChunkAck ack = { chdr.chunk_index, ACK_ERROR_FS };
            send_exact(sock, &ack, sizeof(ack));
            writer.abort();
            return false;
        }

        bytes_written += (uint64_t)write_len;
        chunks_received++;

        // Persist resume state every 8 chunks in case of disconnect
        if (chunks_received % 8 == 0) {
            resume_save(remote_path, bytes_written, ci);
        }

        // Acknowledge successful chunk
        ZelNedChunkAck ack = { chdr.chunk_index, ACK_OK };
        send_exact(sock, &ack, sizeof(ack));

        // Update shared stats for UI thread
        uint64_t elapsed_ms = osGetTime() - t_start;
        float elapsed_s = elapsed_ms / 1000.0f;
        LightLock_Lock(&s_stats_lock);
        s_stats.bytes_received += chdr.compressed_size;
        s_stats.bytes_written  += (uint64_t)decompressed;
        s_stats.chunks_ok       = chunks_received;
        s_stats.current_chunk   = ci + 1;
        s_stats.net_kbps  = elapsed_s > 0.0f ? (s_stats.bytes_received / 1024.0f / elapsed_s) : 0.0f;
        s_stats.eff_mbps  = elapsed_s > 0.0f ? (s_stats.bytes_written / 1048576.0f / elapsed_s) : 0.0f;
        LightLock_Unlock(&s_stats_lock);

        ci++;  // only advance after a successful write
    }

    if (!writer.commit()) {
        writer.abort();
        return false;
    }

    resume_clear(remote_path);
    return true;
}

// ── Main receiver loop ─────────────────────────────────────────────────────────

void receiver_run() {
    LightLock_Init(&s_stats_lock);

    // Allocate buffers in linear memory for best DMA performance
    s_decomp_buf = static_cast<uint8_t*>(linearAlloc(CHUNK_SIZE_RAW + 64));
    s_chunk_buf  = static_cast<uint8_t*>(linearAlloc(DECOMP_BUF_SZ));

    // Create TCP server socket
    int server_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server_sock < 0) {
        linearFree(s_decomp_buf);
        linearFree(s_chunk_buf);
        return;
    }
    int opt = 1;
    setsockopt(server_sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(TCP_PORT);
    addr.sin_addr.s_addr = INADDR_ANY;
    bind(server_sock, (struct sockaddr*)&addr, sizeof(addr));
    listen(server_sock, 1);

    while (!s_stop) {
        // Wait for PC to connect
        struct sockaddr_in client_addr = {};
        socklen_t client_len = sizeof(client_addr);
        int client_sock = accept(server_sock, (struct sockaddr*)&client_addr, &client_len);
        if (client_sock < 0) continue;

        // FIX #7 improvement B: 30-second recv timeout so 3DS doesn't block forever if PC dies
        struct timeval recv_tv = {30, 0};
        setsockopt(client_sock, SOL_SOCKET, SO_RCVTIMEO, &recv_tv, sizeof(recv_tv));

        // IMPROVEMENT C: larger recv buffer — reduces ACK round-trips on congested Wi-Fi
        int rcvbuf = 131072;   // 128 KB
        setsockopt(client_sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

        LightLock_Lock(&s_stats_lock);
        s_stats.connected      = true;
        s_stats.bytes_received = s_stats.bytes_written = 0;
        s_stats.chunks_ok      = s_stats.chunks_nack   = 0;
        LightLock_Unlock(&s_stats_lock);

        // ── Session handshake ─────────────────────────────────────────────────
        ZelNedSessionHeader session = {};
        if (!recv_exact(client_sock, &session, sizeof(session))) {
            close(client_sock);
            continue;
        }

        if (memcmp(session.magic, ZELNED_MAGIC, 6) != 0 || session.version != ZELNED_VERSION) {
            send_byte(client_sock, HS_ERROR);
            close(client_sock);
            continue;
        }

        bool use_crc32  = (session.flags & FLAG_CRC32)  != 0;
        bool use_resume = (session.flags & FLAG_RESUME) != 0;  // FIX #4: now actually passed to receive_file()

        LightLock_Lock(&s_stats_lock);
        s_stats.queue_total = session.queue_count;
        s_stats.queue_pos   = 0;
        LightLock_Unlock(&s_stats_lock);

        send_byte(client_sock, HS_OK);

        // ── Receive files ─────────────────────────────────────────────────────
        for (uint32_t fi = 0; fi < session.queue_count && !s_stop; fi++) {
            ZelNedFileHeader fhdr = {};
            if (!recv_exact(client_sock, &fhdr, sizeof(fhdr))) break;

            if (fhdr.path_len == 0 || fhdr.path_len > 255) break;

            char path_buf[256] = {};
            if (!recv_exact(client_sock, path_buf, fhdr.path_len)) break;
            std::string remote_path(path_buf, fhdr.path_len);

            LightLock_Lock(&s_stats_lock);
            s_stats.queue_pos = fi + 1;
            LightLock_Unlock(&s_stats_lock);

            if (fhdr.type == TYPE_DIRECTORY) {
                fs_mkdir_recursive(remote_path);
                send_byte(client_sock, ACK_DIR_OK);
                continue;
            }

            receive_file(client_sock, fhdr, remote_path, use_crc32, use_resume);  // FIX #4
        }

        close(client_sock);
        LightLock_Lock(&s_stats_lock);
        s_stats.connected = false;
        s_stats.current_file[0] = '\0';
        LightLock_Unlock(&s_stats_lock);
    }

    close(server_sock);
    linearFree(s_decomp_buf);
    linearFree(s_chunk_buf);
}

ReceiverStats receiver_get_stats() {
    LightLock_Lock(&s_stats_lock);
    ReceiverStats copy = s_stats;
    LightLock_Unlock(&s_stats_lock);
    return copy;
}

void receiver_set_paused(bool paused) {
    s_paused = paused;
    LightLock_Lock(&s_stats_lock);
    s_stats.paused = paused;
    LightLock_Unlock(&s_stats_lock);
}

bool receiver_is_paused() {
    return s_paused;
}

void receiver_stop() {
    s_stop = true;
}
