/**
 * Zel.NeD — UI Implementation (citro2d dual-screen)
 * ===================================================
 * Top screen (400x240):  Progress bar, speed, file info, ETA
 * Bottom screen (320x240): IP address, Wi-Fi signal, log
 */
#include "ui.hpp"
#include "net_receiver.hpp"
#include <citro2d.h>
#include <3ds.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

// Colors (ABGR format for citro2d)
#define C_BG        C2D_Color32(0x1a, 0x1a, 0x2e, 0xFF)
#define C_PANEL     C2D_Color32(0x16, 0x21, 0x3e, 0xFF)
#define C_BLUE      C2D_Color32(0x4f, 0xc3, 0xf7, 0xFF)
#define C_GREEN     C2D_Color32(0x66, 0xbb, 0x6a, 0xFF)
#define C_YELLOW    C2D_Color32(0xff, 0xca, 0x28, 0xFF)
#define C_RED       C2D_Color32(0xef, 0x53, 0x50, 0xFF)
#define C_DIM       C2D_Color32(0x77, 0x77, 0x88, 0xFF)
#define C_WHITE     C2D_Color32(0xe0, 0xe0, 0xe0, 0xFF)
#define C_BAR_FILL  C2D_Color32(0x4f, 0xc3, 0xf7, 0xFF)
#define C_BAR_EMPTY C2D_Color32(0x0f, 0x34, 0x60, 0xFF)

static C2D_TextBuf s_tbuf;
static C3D_RenderTarget* s_top;
static C3D_RenderTarget* s_bot;

// Simple log ring buffer
#define LOG_LINES 8
static char s_log[LOG_LINES][64];
static int  s_log_head = 0;

void ui_log(const char* msg) {
    strncpy(s_log[s_log_head % LOG_LINES], msg, 63);
    s_log_head++;
}

bool ui_init() {
    gfxInitDefault();
    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    C2D_Init(C2D_DEFAULT_MAX_OBJECTS);
    C2D_Prepare();

    s_top = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    s_bot = C2D_CreateScreenTarget(GFX_BOTTOM, GFX_LEFT);
    s_tbuf = C2D_TextBufNew(4096);

    return true;
}

void ui_exit() {
    C2D_TextBufDelete(s_tbuf);
    C2D_Fini();
    C3D_Fini();
    gfxExit();
}

static void draw_text(float x, float y, float sz, uint32_t color, const char* fmt, ...) {
    char buf[128];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    C2D_Text text;
    C2D_TextBufClear(s_tbuf);
    C2D_TextParse(&text, s_tbuf, buf);
    C2D_TextOptimize(&text);
    C2D_DrawText(&text, C2D_WithColor, x, y, 0.5f, sz, sz, color);
}

static void draw_rect(float x, float y, float w, float h, uint32_t color) {
    C2D_DrawRectSolid(x, y, 0.5f, w, h, color);
}

static void draw_progress_bar(float x, float y, float w, float h, float progress) {
    draw_rect(x, y, w, h, C_BAR_EMPTY);
    float fill = w * progress;
    if (fill > 0) draw_rect(x, y, fill, h, C_BAR_FILL);
}

// Animated "pulse" dot for live indicator
static uint32_t pulse_color(float t) {
    float alpha = 0.5f + 0.5f * sinf(t * 3.0f);
    uint8_t a = (uint8_t)(alpha * 255);
    return C2D_Color32(0x4f, 0xc3, 0xf7, a);
}

void ui_draw(const ReceiverStats& stats) {
    static float anim_t = 0.0f;
    anim_t += 0.05f;

    C3D_FrameBegin(C3D_FRAME_SYNCDRAW);

    // ── TOP SCREEN (400×240) ─────────────────────────────────────────────────
    C2D_TargetClear(s_top, C_BG);
    C2D_SceneBegin(s_top);

    // Header bar
    draw_rect(0, 0, 400, 26, C_PANEL);
    draw_text(8, 4, 0.55f, C_BLUE, "\u2B21 Zel.NeD  v1.0");
    // Live indicator
    if (stats.connected) {
        draw_text(350, 4, 0.45f, pulse_color(anim_t), "\u25CF LIVE");
    } else {
        draw_text(350, 4, 0.45f, C_DIM, "\u25CB Idle");
    }

    int y = 34;

    if (stats.connected && stats.current_file[0]) {
        // File name (truncate if needed)
        char fname[40];
        strncpy(fname, stats.current_file, 39);
        draw_text(8, (float)y, 0.48f, C_WHITE,   "File:  %s", fname); y += 18;
        draw_text(8, (float)y, 0.44f, C_DIM,     "Queue: %d / %d",
                  stats.queue_pos, stats.queue_total);                  y += 22;

        // Progress bar
        float progress = (stats.total_chunks > 0)
            ? (float)stats.current_chunk / stats.total_chunks
            : 0.0f;
        draw_progress_bar(8, (float)y, 384, 14, progress);
        y += 20;
        draw_text(8, (float)y, 0.48f, C_BLUE, "%.1f%%  (%u / %u chunks)",
                  progress * 100.0f, stats.current_chunk, stats.total_chunks); y += 20;

        // Speed stats
        draw_text(8,   (float)y, 0.48f, C_WHITE,  "Net:       %.0f KB/s",  stats.net_kbps);  y += 18;
        draw_text(8,   (float)y, 0.48f, C_GREEN,  "Effective: %.2f MB/s",  stats.eff_mbps);  y += 18;

        // Compression ratio
        float ratio = (stats.bytes_received > 0)
            ? (1.0f - (float)stats.bytes_received / (float)stats.bytes_written) * 100.0f
            : 0.0f;
        draw_text(8, (float)y, 0.48f, C_YELLOW, "LZ4 saving: %.0f%%", ratio > 0 ? ratio : 0.0f); y += 18;

        // CRC stats
        draw_text(8, (float)y, 0.44f, C_DIM, "CRC OK: %u  NACK: %u",
                  stats.chunks_ok, stats.chunks_nack);
    } else {
        draw_text(8, 80, 0.52f, C_DIM, "Waiting for connection...");
        draw_text(8, 105, 0.44f, C_DIM, "Open Zel.NeD on your PC and");
        draw_text(8, 120, 0.44f, C_DIM, "select this console from the list.");
    }

    // Bottom hint
    draw_rect(0, 228, 400, 12, C_PANEL);
    draw_text(8, 229, 0.38f, C_DIM, "[START] Exit   [SELECT] Pause   [Y] Clear log");

    // ── BOTTOM SCREEN (320×240) ──────────────────────────────────────────────
    C2D_TargetClear(s_bot, C_BG);
    C2D_SceneBegin(s_bot);

    draw_rect(0, 0, 320, 26, C_PANEL);
    draw_text(8, 4, 0.52f, C_BLUE, "\u2B21 Zel.NeD \u2014 Receiver");

    // Get IP
    char ip_buf[24] = "Not connected";
    if (stats.connected) {
        // Read local IP from gethostname (simplified)
        uint32_t ip = gethostid();
        snprintf(ip_buf, sizeof(ip_buf), "%lu.%lu.%lu.%lu",
                 (ip >> 24) & 0xFF, (ip >> 16) & 0xFF,
                 (ip >> 8) & 0xFF, ip & 0xFF);
    } else {
        struct in_addr addr;
        addr.s_addr = gethostid();
        snprintf(ip_buf, sizeof(ip_buf), "%s", inet_ntoa(addr));
    }

    int by = 34;
    draw_text(8, (float)by, 0.50f, C_WHITE,  "IP:     %s", ip_buf);    by += 18;
    draw_text(8, (float)by, 0.50f, C_DIM,    "Port:   %d", TCP_PORT);  by += 18;

    // Wi-Fi signal bars (from acuGetWifiStatus)
    uint8_t wifi = 0;
    ACU_GetWifiStatus(&wifi);
    const char* wifi_bars[] = {"   No signal", "\u2582   Weak", "\u2582\u2584  Fair", "\u2582\u2584\u2586 Good", "\u2582\u2584\u2586\u2588 Excellent"};
    uint32_t wifi_colors[]  = {C_RED, C_RED, C_YELLOW, C_GREEN, C_GREEN};
    int ws = (int)(wifi > 4 ? 4 : wifi);
    draw_text(8, (float)by, 0.50f, wifi_colors[ws], "Wi-Fi:  %s", wifi_bars[ws]); by += 24;

    draw_rect(0, by, 320, 1, C_DIM); by += 6;
    draw_text(8, (float)by, 0.46f, C_BLUE, "Log:"); by += 16;

    // Log lines (newest last)
    for (int i = 0; i < LOG_LINES; i++) {
        int idx = (s_log_head - LOG_LINES + i + LOG_LINES * 2) % LOG_LINES;
        if (s_log[idx][0]) {
            draw_text(8, (float)by, 0.40f, C_DIM, "> %s", s_log[idx]);
            by += 13;
        }
    }

    C3D_FrameEnd(0);
}
