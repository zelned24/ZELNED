/**
 * Zel.NeD - TCP Receiver Implementation (3DS)
 * =============================================
 * Full ZELNED v1 protocol receiver with telemetry instrumentation.
 *
 * Per-chunk timing: T_net (socket recv), T_cpu (CRC32+LZ4), T_sd (SD write).
 * Benchmark mode (FLAG_BENCHMARK_NET): receives chunks and discards to RAM.
 */
#include "net_receiver.hpp"
#include "protocol.hpp"
#include "telemetry.hpp"
#include "integrity.hpp"
#include "fs_writer.hpp"
#include "resume.hpp"
#include "lz4/lz4.h"
#include <3ds.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

static volatile bool    s_stop        = false;
static volatile bool    s_paused      = false;
static ReceiverStats    s_stats       = {};
static LightLock        s_stats_lock;
static int              s_server_sock = -1;

static uint8_t*  s_buf_a      = nullptr;
static uint8_t*  s_buf_b      = nullptr;
static uint8_t*  s_chunk_buf  = nullptr;
static const int DECOMP_BUF_SZ = LZ4_COMPRESSBOUND(CHUNK_SIZE_RAW) + 64;

struct WriteJob {
    FsWriter*      writer;
    const uint8_t* data;
    size_t         len;
    volatile bool  ok;
    volatile bool  exit_flag;
};

static WriteJob   s_wjob;
static LightEvent s_wjob_ready;
static LightEvent s_wjob_done;

static void write_worker_fn(void*) {
    while (!s_wjob.exit_flag) {
        LightEvent_Wait(&s_wjob_ready);
        if (s_wjob.exit_flag) break;
        s_wjob.ok = s_wjob.writer->write(s_wjob.data, s_wjob.len);
        LightEvent_Signal(&s_wjob_done);
    }
}

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

static void do_abort(FsWriter& writer, Thread wthread, bool pending) {
    if (pending) LightEvent_Wait(&s_wjob_done);
    writer.abort();
    if (wthread) {
        s_wjob.exit_flag = true;
        LightEvent_Signal(&s_wjob_ready);
        threadJoin(wthread, 2000000000ULL);
        threadFree(wthread);
    }
}

// Send ACK: normal (5 bytes) or with telemetry (15 bytes)
static bool send_ack(int sock, uint32_t chunk_index, uint8_t ack_status, bool use_telemetry) {
    if (use_telemetry && ack_status == ACK_OK) {
        ZelNedChunkAckTelemetry tack;
        telemetry_fill(&tack, chunk_index);
        return send_exact(sock, &tack, sizeof(tack));
    }
    ZelNedChunkAck ack = { chunk_index, ack_status };
    return send_exact(sock, &ack, sizeof(ack));
}

// -- Normal file receive with telemetry instrumentation -----------------------
static bool receive_file(int sock, const ZelNedFileHeader& fhdr,
                         const std::string& remote_path, bool use_crc32,
                         bool use_resume, bool use_telemetry) {
    LightLock_Lock(&s_stats_lock);
    strncpy(s_stats.current_file, remote_path.c_str(), 255);
    s_stats.total_chunks     = fhdr.total_chunks;
    s_stats.current_chunk    = 0;
    s_stats.telemetry_active = use_telemetry;
    LightLock_Unlock(&s_stats_lock);

    telemetry_reset();

    if (fhdr.uncompressed_size > 0) {
        uint64_t free_space = fs_free_bytes();
        if (free_space < fhdr.uncompressed_size) {
            send_byte(sock, HS_SD_FULL);
            return false;
        }
    }

    uint64_t resume_offset = use_resume ? fhdr.resume_offset : 0;
    uint32_t start_chunk   = (resume_offset > 0) ? (uint32_t)(resume_offset / CHUNK_SIZE_RAW) : 0;

    FsWriter writer;
    if (!writer.open(remote_path, fhdr.uncompressed_size, resume_offset)) {
        send_byte(sock, HS_ERROR);
        return false;
    }
    send_byte(sock, HS_OK);

    uint64_t bytes_written   = resume_offset;
    uint32_t chunks_received = start_chunk;
    uint64_t t_start         = osGetTime();
    uint32_t ci              = start_chunk;
    uint32_t nack_retries    = 0;
    const uint32_t MAX_NACK  = 5;

    s_wjob.writer    = &writer;
    s_wjob.exit_flag = false;
    s_wjob.ok        = true;
    LightEvent_Init(&s_wjob_ready, RESET_ONESHOT);
    LightEvent_Init(&s_wjob_done,  RESET_ONESHOT);
    Thread wthread = threadCreate(write_worker_fn, nullptr, 16 * 1024, 0x39, -2, false);

    uint8_t* work_buf      = s_buf_a;
    uint8_t* write_buf     = s_buf_b;
    bool     pending_write = false;

    uint64_t t_net_start = 0, t_net_end = 0;
    uint64_t t_cpu_start = 0, t_cpu_end = 0;
    uint64_t t_sd_start  = 0, t_sd_end  = 0;

    while (ci < fhdr.total_chunks && !s_stop) {
        while (s_paused && !s_stop) svcSleepThread(50000000LL);
        if (s_stop) break;

        // STEP 1: Recv header + payload (overlaps with previous SD write)
        t_net_start = telemetry_tick();
        ZelNedChunkHeader chdr;
        if (!recv_exact(sock, &chdr, sizeof(chdr))) {
            do_abort(writer, wthread, pending_write);
            return false;
        }
        if (chdr.compressed_size == 0 || chdr.compressed_size > (uint32_t)DECOMP_BUF_SZ ||
            chdr.uncompressed_size == 0 || chdr.uncompressed_size > CHUNK_SIZE_RAW) {
            send_ack(sock, chdr.chunk_index, ACK_ERROR_FS, false);
            do_abort(writer, wthread, pending_write);
            return false;
        }
        if (!recv_exact(sock, s_chunk_buf, chdr.compressed_size)) {
            do_abort(writer, wthread, pending_write);
            return false;
        }
        t_net_end = telemetry_tick();

        // STEP 2: Wait for previous SD write (measure T_sd)
        t_sd_start = telemetry_tick();
        if (pending_write) {
            LightEvent_Wait(&s_wjob_done);
            if (!s_wjob.ok) {
                send_ack(sock, chdr.chunk_index, ACK_ERROR_FS, false);
                do_abort(writer, wthread, false);
                return false;
            }
            pending_write = false;
        }
        t_sd_end = telemetry_tick();

        // STEP 3: CRC32 + LZ4 decompress (measure T_cpu)
        t_cpu_start = telemetry_tick();
        if (use_crc32) {
            uint32_t computed = zelned_crc32(s_chunk_buf, chdr.compressed_size);
            if (computed != chdr.crc32) {
                send_ack(sock, chdr.chunk_index, ACK_NACK, false);
                LightLock_Lock(&s_stats_lock);
                s_stats.chunks_nack++;
                LightLock_Unlock(&s_stats_lock);
                if (++nack_retries >= MAX_NACK) { do_abort(writer, wthread, false); return false; }
                continue;
            }
        }
        nack_retries = 0;

        size_t write_len = chdr.uncompressed_size;
        if (chdr.compressed_size < chdr.uncompressed_size) {
            int dec = LZ4_decompress_safe((const char*)s_chunk_buf, (char*)work_buf,
                                          (int)chdr.compressed_size, (int)chdr.uncompressed_size);
            if (dec != (int)chdr.uncompressed_size) {
                send_ack(sock, chdr.chunk_index, ACK_NACK, false);
                if (++nack_retries >= MAX_NACK) { do_abort(writer, wthread, false); return false; }
                continue;
            }
            write_len = (size_t)dec;
        } else {
            memcpy(work_buf, s_chunk_buf, chdr.compressed_size);
            write_len = chdr.compressed_size;
        }
        t_cpu_end = telemetry_tick();

        // Record timings (T_sd = wait for PREVIOUS write)
        telemetry_record(t_net_end - t_net_start, t_cpu_end - t_cpu_start, t_sd_end - t_sd_start);

        // STEP 5: ACK immediately so PC sends the next chunk
        bytes_written += (uint64_t)write_len;
        chunks_received++;
        if (chunks_received % 8 == 0) resume_save(remote_path, bytes_written, ci);
        send_ack(sock, chdr.chunk_index, ACK_OK, use_telemetry);

        // STEP 6: Async SD write (non-blocking)
        if (wthread) {
            s_wjob.data = work_buf;
            s_wjob.len  = write_len;
            LightEvent_Signal(&s_wjob_ready);
            pending_write = true;
        } else {
            if (!writer.write(work_buf, write_len)) { writer.abort(); return false; }
        }

        // STEP 7: Update stats
        uint64_t elapsed_ms = osGetTime() - t_start;
        float elapsed_s = elapsed_ms / 1000.0f;
        LightLock_Lock(&s_stats_lock);
        s_stats.bytes_received += chdr.compressed_size;
        s_stats.bytes_written  += (uint64_t)write_len;
        s_stats.chunks_ok       = chunks_received;
        s_stats.current_chunk   = ci + 1;
        s_stats.net_kbps  = elapsed_s > 0.0f ? (s_stats.bytes_received / 1024.0f / elapsed_s) : 0.0f;
        s_stats.eff_mbps  = elapsed_s > 0.0f ? (s_stats.bytes_written / 1048576.0f / elapsed_s) : 0.0f;
        if (use_telemetry) {
            ZelNedChunkAckTelemetry snap;
            telemetry_fill(&snap, ci);
            s_stats.telem_net_us = snap.t_net_us;
            s_stats.telem_cpu_us = snap.t_cpu_us;
            s_stats.telem_sd_us  = snap.t_sd_us;
            s_stats.wifi_bars    = snap.wifi_bars;
            s_stats.cpu_load_pct = snap.cpu_load_pct;
        }
        LightLock_Unlock(&s_stats_lock);

        // STEP 8: Swap buffers
        uint8_t* tmp = work_buf; work_buf = write_buf; write_buf = tmp;
        ci++;
    }

    if (pending_write) {
        LightEvent_Wait(&s_wjob_done);
        if (!s_wjob.ok) { do_abort(writer, wthread, false); return false; }
    }
    if (wthread) {
        s_wjob.exit_flag = true;
        LightEvent_Signal(&s_wjob_ready);
        threadJoin(wthread, 2000000000ULL);
        threadFree(wthread);
    }
    if (!writer.commit()) { writer.abort(); return false; }
    resume_clear(remote_path);
    return true;
}

// -- RAM-sink benchmark (no SD write) -----------------------------------------
static bool receive_benchmark(int sock, const ZelNedFileHeader& fhdr,
                               bool use_crc32, bool use_telemetry) {
    LightLock_Lock(&s_stats_lock);
    snprintf(s_stats.current_file, sizeof(s_stats.current_file), "%s", "[NET BENCHMARK]");
    s_stats.total_chunks  = fhdr.total_chunks;
    s_stats.current_chunk = 0;
    LightLock_Unlock(&s_stats_lock);

    telemetry_reset();
    send_byte(sock, HS_OK);

    uint64_t t_start = osGetTime();
    uint32_t ci = 0;
    uint32_t nack_retries = 0;

    while (ci < fhdr.total_chunks && !s_stop) {
        uint64_t t0 = telemetry_tick();
        ZelNedChunkHeader chdr;
        if (!recv_exact(sock, &chdr, sizeof(chdr))) break;
        if (chdr.compressed_size == 0 || chdr.compressed_size > (uint32_t)DECOMP_BUF_SZ) break;
        if (!recv_exact(sock, s_chunk_buf, chdr.compressed_size)) break;
        uint64_t t1 = telemetry_tick();

        if (use_crc32) {
            uint32_t computed = zelned_crc32(s_chunk_buf, chdr.compressed_size);
            if (computed != chdr.crc32) {
                send_ack(sock, chdr.chunk_index, ACK_NACK, false);
                if (++nack_retries >= 5) break;
                continue;
            }
        }
        uint64_t t2 = telemetry_tick();
        nack_retries = 0;
        telemetry_record(t1 - t0, t2 - t1, 0ULL);

        send_ack(sock, chdr.chunk_index, ACK_OK, use_telemetry);

        uint64_t elapsed_ms = osGetTime() - t_start;
        float elapsed_s = elapsed_ms / 1000.0f;
        LightLock_Lock(&s_stats_lock);
        s_stats.bytes_received += chdr.compressed_size;
        s_stats.chunks_ok       = ++ci;
        s_stats.current_chunk   = ci;
        s_stats.net_kbps = elapsed_s > 0.0f ? (s_stats.bytes_received / 1024.0f / elapsed_s) : 0.0f;
        s_stats.eff_mbps = s_stats.net_kbps / 1024.0f;
        if (use_telemetry) {
            ZelNedChunkAckTelemetry snap;
            telemetry_fill(&snap, ci);
            s_stats.telem_net_us = snap.t_net_us;
            s_stats.telem_cpu_us = snap.t_cpu_us;
            s_stats.telem_sd_us  = 0;
            s_stats.wifi_bars    = snap.wifi_bars;
        }
        LightLock_Unlock(&s_stats_lock);
    }
    return true;
}

// -- Public API ---------------------------------------------------------------

void receiver_init() {
    LightLock_Init(&s_stats_lock);
    memset((void*)&s_stats, 0, sizeof(s_stats));
    s_stop        = false;
    s_paused      = false;
    s_server_sock = -1;
}

void receiver_run() {
    s_buf_a     = static_cast<uint8_t*>(linearAlloc(CHUNK_SIZE_RAW + 64));
    s_buf_b     = static_cast<uint8_t*>(linearAlloc(CHUNK_SIZE_RAW + 64));
    s_chunk_buf = static_cast<uint8_t*>(linearAlloc(DECOMP_BUF_SZ));

    s_server_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s_server_sock < 0) {
        linearFree(s_buf_a); linearFree(s_buf_b); linearFree(s_chunk_buf);
        return;
    }
    int opt = 1;
    setsockopt(s_server_sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(TCP_PORT);
    addr.sin_addr.s_addr = INADDR_ANY;
    bind(s_server_sock, (struct sockaddr*)&addr, sizeof(addr));
    listen(s_server_sock, 1);

    while (!s_stop) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(s_server_sock, &fds);
        struct timeval tv = { 0, 250000 };
        if (select(s_server_sock + 1, &fds, NULL, NULL, &tv) <= 0) continue;

        struct sockaddr_in client_addr = {};
        socklen_t client_len = sizeof(client_addr);
        int client_sock = accept(s_server_sock, (struct sockaddr*)&client_addr, &client_len);
        if (client_sock < 0) continue;

        int rcvbuf = 2 * 1024 * 1024;
        setsockopt(client_sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
        int nodelay = 1;
        setsockopt(client_sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));

        ZelNedSessionHeader session = {};
        if (!recv_exact(client_sock, &session, sizeof(session)) ||
            memcmp(session.magic, ZELNED_MAGIC, 6) != 0 ||
            session.version != ZELNED_VERSION) {
            send_byte(client_sock, HS_ERROR);
            close(client_sock);
            continue;
        }

        bool use_crc32     = (session.flags & FLAG_CRC32)         != 0;
        bool use_resume    = (session.flags & FLAG_RESUME)        != 0;
        bool use_telemetry = (session.flags & FLAG_TELEMETRY)     != 0;
        bool is_benchmark  = (session.flags & FLAG_BENCHMARK_NET) != 0;

        LightLock_Lock(&s_stats_lock);
        s_stats.connected        = true;
        s_stats.bytes_received   = 0;
        s_stats.bytes_written    = 0;
        s_stats.chunks_ok        = 0;
        s_stats.chunks_nack      = 0;
        s_stats.queue_total      = session.queue_count;
        s_stats.queue_pos        = 0;
        s_stats.telemetry_active = use_telemetry;
        s_stats.wifi_bars        = 0;
        s_stats.cpu_load_pct     = 0;
        LightLock_Unlock(&s_stats_lock);

        send_byte(client_sock, HS_OK);

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

            if (is_benchmark) {
                receive_benchmark(client_sock, fhdr, use_crc32, use_telemetry);
            } else {
                receive_file(client_sock, fhdr, remote_path, use_crc32, use_resume, use_telemetry);
            }
        }

        close(client_sock);
        LightLock_Lock(&s_stats_lock);
        s_stats.connected        = false;
        s_stats.current_file[0] = '\0';
        s_stats.telemetry_active = false;
        LightLock_Unlock(&s_stats_lock);
    }

    if (s_server_sock >= 0) { close(s_server_sock); s_server_sock = -1; }
    linearFree(s_buf_a); linearFree(s_buf_b); linearFree(s_chunk_buf);
    s_buf_a = s_buf_b = s_chunk_buf = nullptr;
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

bool receiver_is_paused() { return s_paused; }

void receiver_stop() {
    s_stop = true;
    if (s_server_sock >= 0) shutdown(s_server_sock, SHUT_RDWR);
}
