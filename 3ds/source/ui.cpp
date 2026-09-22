/**
 * Zel.NeD — UI Implementation (citro2d Top Screen + Console Bottom Screen)
 * =========================================================================
 * Top screen (400x240):  Progress bar, speed, file info, ETA, status cards (citro2d)
 * Bottom screen (320x240): Text console with real-time logs and error codes (libctru console)
 */
#include "ui.hpp"
#include "net_receiver.hpp"
#include "protocol.hpp"
#include <citro2d.h>
#include <3ds.h>
#include <arpa/inet.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

// Color Palette (citro2d RGBA/ABGR format)
#define C_BG           C2D_Color32(0x0e, 0x13, 0x1f, 0xFF) // Deep dark slate
#define C_HEADER       C2D_Color32(0x15, 0x1d, 0x2e, 0xFF) // Header/footer bar
#define C_PANEL        C2D_Color32(0x18, 0x22, 0x36, 0xFF) // Card panel background
#define C_PANEL_BORDER C2D_Color32(0x27, 0x36, 0x4f, 0xFF) // Card subtle border
#define C_INNER        C2D_Color32(0x0f, 0x17, 0x26, 0xFF) // Inner dark box
#define C_CYAN         C2D_Color32(0x38, 0xbd, 0xf8, 0xFF) // Electric cyan/sky
#define C_GREEN        C2D_Color32(0x4a, 0xde, 0x80, 0xFF) // Mint green
#define C_YELLOW       C2D_Color32(0xfb, 0xbf, 0x24, 0xFF) // Amber
#define C_RED          C2D_Color32(0xf8, 0x71, 0x71, 0xFF) // Coral red
#define C_WHITE        C2D_Color32(0xf8, 0xfa, 0xfc, 0xFF) // Bright white
#define C_MUTED        C2D_Color32(0x94, 0xa3, 0xb8, 0xFF) // Slate 400
#define C_DIM          C2D_Color32(0x64, 0x74, 0x8b, 0xFF) // Slate 500
#define C_BAR_BG       C2D_Color32(0x0c, 0x14, 0x22, 0xFF) // Progress track
#define C_BAR_FILL     C2D_Color32(0x02, 0x84, 0xc7, 0xFF) // Vibrant blue fill
#define C_BAR_HL       C2D_Color32(0x38, 0xbd, 0xf8, 0xFF) // Progress highlight edge

static C2D_TextBuf s_tbuf = nullptr;
static C3D_RenderTarget* s_top = nullptr;
static char s_ui_name[32] = "Nintendo 3DS";
static char s_ui_ip[24]   = "Desconocida";

void ui_set_console_info(const char* name, const char* ip) {
    if (name && name[0]) {
        strncpy(s_ui_name, name, sizeof(s_ui_name) - 1);
        s_ui_name[sizeof(s_ui_name) - 1] = '\0';
    }
    if (ip && ip[0]) {
        strncpy(s_ui_ip, ip, sizeof(s_ui_ip) - 1);
        s_ui_ip[sizeof(s_ui_ip) - 1] = '\0';
    }
}

void ui_log(const char* msg) {
    printf("%s\n", msg);
}

void ui_log_clear() {
    printf("\x1b[2J\x1b[H");
}

bool ui_init() {
    gfxInitDefault();

    // Consola nativa en pantalla inferior
    consoleInit(GFX_BOTTOM, NULL);

    // Inicializar Citro3D y Citro2D para pantalla superior
    if (!C3D_Init(C3D_DEFAULT_CMDBUF_SIZE)) {
        printf("\x1b[1;31m[!] Error: C3D_Init fallo.\x1b[0m\n");
        return false;
    }
    if (!C2D_Init(C2D_DEFAULT_MAX_OBJECTS)) {
        printf("\x1b[1;31m[!] Error: C2D_Init fallo.\x1b[0m\n");
        return false;
    }
    C2D_Prepare();

    s_top  = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    s_tbuf = C2D_TextBufNew(4096);

    return true;
}

void ui_exit() {
    if (s_tbuf) {
        C2D_TextBufDelete(s_tbuf);
        s_tbuf = nullptr;
    }
    C2D_Fini();
    C3D_Fini();
    gfxExit();
}

static void draw_text(float x, float y, float sz, uint32_t color, const char* fmt, ...) {
    if (!s_tbuf) return;
    char buf[128];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    C2D_Text text;
    C2D_TextParse(&text, s_tbuf, buf);
    C2D_TextOptimize(&text);
    C2D_DrawText(&text, C2D_WithColor, x, y, 0.5f, sz, sz, color);
}

static void draw_rect(float x, float y, float w, float h, uint32_t color) {
    C2D_DrawRectSolid(x, y, 0.5f, w, h, color);
}

static void draw_bordered_card(float x, float y, float w, float h, uint32_t bg_col, uint32_t border_col) {
    draw_rect(x, y, w, h, bg_col);
    draw_rect(x, y, w, 1.0f, border_col);
    draw_rect(x, y + h - 1.0f, w, 1.0f, border_col);
    draw_rect(x, y, 1.0f, h, border_col);
    draw_rect(x + w - 1.0f, y, 1.0f, h, border_col);
}

void ui_draw(const ReceiverStats& stats) {
    static float anim_t = 0.0f;
    anim_t += 0.06f;

    C3D_FrameBegin(C3D_FRAME_SYNCDRAW);

    if (s_tbuf) C2D_TextBufClear(s_tbuf);

    // ── TOP SCREEN (400×240) ─────────────────────────────────────────────────
    if (s_top) {
        C2D_TargetClear(s_top, C_BG);
        C2D_SceneBegin(s_top);

        // Header bar (0 .. 26)
        draw_rect(0, 0, 400, 26, C_HEADER);
        draw_rect(0, 25, 400, 1, C_PANEL_BORDER);

        draw_text(10, 4, 0.54f, C_CYAN, "Zel.NeD");
        draw_text(85, 6, 0.42f, C_MUTED, "v1.1  High-Speed WireLess");

        // Status Badge (top right)
        if (stats.paused) {
            draw_bordered_card(275, 4, 115, 18, C2D_Color32(0x3a, 0x2e, 0x0a, 0xFF), C_YELLOW);
            draw_text(285, 5, 0.42f, C_YELLOW, "||  EN PAUSA");
        } else if (stats.connected && stats.current_file[0]) {
            float pulse = 0.6f + 0.4f * sinf(anim_t * 3.0f);
            uint32_t live_col = C2D_Color32(0x4a, 0xde, 0x80, (uint8_t)(pulse * 255));
            draw_bordered_card(275, 4, 115, 18, C2D_Color32(0x09, 0x30, 0x1e, 0xFF), live_col);
            draw_text(288, 5, 0.42f, live_col, "*  EN VIVO");
        } else if (stats.connected) {
            draw_bordered_card(275, 4, 115, 18, C2D_Color32(0x0a, 0x26, 0x3d, 0xFF), C_CYAN);
            draw_text(282, 5, 0.42f, C_CYAN, "●  CONECTADO");
        } else {
            draw_bordered_card(275, 4, 115, 18, C2D_Color32(0x14, 0x1d, 0x2c, 0xFF), C_PANEL_BORDER);
            draw_text(285, 5, 0.42f, C_MUTED, "o  EN ESPERA");
        }

        // Main body content (32 .. 216)
        if (stats.connected && stats.current_file[0]) {
            // ── Transferring Active Card ──────────────────────────────────────
            draw_bordered_card(12, 32, 376, 182, C_PANEL, C_PANEL_BORDER);

            // File Name & Queue
            char fname[36];
            strncpy(fname, stats.current_file, 35);
            fname[35] = '\0';
            draw_text(24, 40, 0.48f, C_WHITE, "Archivo: %s", fname);
            draw_text(24, 58, 0.42f, C_MUTED, "Cola: Archivo %d de %d",
                      stats.queue_pos, stats.queue_total);

            // Progress Bar
            float progress = (stats.total_chunks > 0)
                ? (float)stats.current_chunk / stats.total_chunks
                : 0.0f;
            if (progress > 1.0f) progress = 1.0f;

            draw_bordered_card(24, 78, 352, 16, C_BAR_BG, C_PANEL_BORDER);
            float fill_w = 348.0f * progress;
            if (fill_w > 0) {
                draw_rect(26, 80, fill_w, 12, C_BAR_FILL);
                // Highlight line at edge
                draw_rect(26 + fill_w - 2.0f, 80, 2.0f, 12, C_BAR_HL);
            }

            // Progress text & ETA
            draw_text(24, 98, 0.54f, C_CYAN, "%.1f%%", progress * 100.0f);
            draw_text(100, 100, 0.40f, C_MUTED, "(%u / %u)",
                      stats.current_chunk, stats.total_chunks);

            // Estimated Time Remaining (ETA)
            char eta_str[32] = "--:--";
            if (stats.eff_mbps > 0.01f && stats.total_chunks > stats.current_chunk) {
                uint32_t rem_chunks = stats.total_chunks - stats.current_chunk;
                float rem_bytes = (float)rem_chunks * (float)CHUNK_SIZE_RAW;
                float bytes_per_sec = stats.eff_mbps * 1048576.0f;
                uint32_t rem_sec = (uint32_t)(rem_bytes / bytes_per_sec);
                if (rem_sec >= 3600) {
                    snprintf(eta_str, sizeof(eta_str), "%uh %02um", (unsigned int)(rem_sec / 3600), (unsigned int)((rem_sec % 3600) / 60));
                } else {
                    snprintf(eta_str, sizeof(eta_str), "%02um %02us", (unsigned int)(rem_sec / 60), (unsigned int)(rem_sec % 60));
                }
            }
            draw_text(260, 100, 0.44f, C_YELLOW, "ETA: %s", eta_str);

            // Metrics 2-column box
            draw_bordered_card(24, 122, 352, 64, C_INNER, C_PANEL_BORDER);

            // Col 1: Net speed + CRC
            draw_text(34, 128, 0.42f, C_MUTED, "Velocidad Red:");
            draw_text(34, 144, 0.50f, C_CYAN, "%.0f KB/s", stats.net_kbps);
            draw_text(34, 166, 0.40f, C_DIM, "CRC32 OK: %u  NACK: %u",
                      stats.chunks_ok, stats.chunks_nack);

            // Col 2: Effective speed + LZ4 savings
            float ratio = (stats.bytes_received > 0 && stats.bytes_written > stats.bytes_received)
                ? (1.0f - (float)stats.bytes_received / (float)stats.bytes_written) * 100.0f
                : 0.0f;

            draw_text(200, 128, 0.42f, C_MUTED, "Velocidad Efectiva:");
            draw_text(200, 144, 0.50f, C_GREEN, "%.2f MB/s", stats.eff_mbps);
            draw_text(200, 166, 0.40f, C_YELLOW, "Ahorro LZ4: %.0f%%", ratio);

        } else {
            // ── Idle Card ────────────────────────────────────────────────────
            draw_bordered_card(12, 32, 376, 182, C_PANEL, C_PANEL_BORDER);

            draw_text(24, 42, 0.50f, C_WHITE, "CONSOLA LISTA PARA TRANSFERIR");

            // Hardware & IP card
            draw_bordered_card(24, 66, 352, 42, C_INNER, C_PANEL_BORDER);
            draw_text(34, 71, 0.42f, C_MUTED, "Consola: %s", s_ui_name);
            draw_text(34, 88, 0.48f, C_CYAN, "IP: %s  :  %d", s_ui_ip, TCP_PORT);

            // Steps
            draw_text(24, 118, 0.44f, C_WHITE, "1. Abre Zel.NeD en tu PC");
            draw_text(24, 136, 0.44f, C_WHITE, "2. Pulsa 'Discover 3DS' o introduce la IP");
            draw_text(24, 154, 0.44f, C_WHITE, "3. Arrastra archivos (.cia / .3ds) y pulsa Send");
            draw_text(24, 178, 0.40f, C_DIM, "Aceleracion LZ4 en tiempo real activa");
        }

        // Footer bar (220 .. 240)
        draw_rect(0, 220, 400, 20, C_HEADER);
        draw_rect(0, 219, 400, 1, C_PANEL_BORDER);
        draw_text(12, 223, 0.38f, C_DIM, "[START] Salir   [SELECT] Pausar   [Y] Limpiar consola");
    }

    C3D_FrameEnd(0);
}
