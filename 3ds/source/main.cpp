/**
 * Zel.NeD — Main Entry Point (Nintendo 3DS)
 * ============================================
 * Initializes all subsystems and runs three concurrent threads:
 *   1. UDP Beacon broadcaster (auto-discovery)
 *   2. TCP Receiver (ZELNED protocol handler)
 *   3. Main loop (UI rendering + button input)
 */
#include <3ds.h>
#include <stdio.h>
#include <unistd.h>
#include <arpa/inet.h>
#include "ui.hpp"
#include "net_beacon.hpp"
#include "net_receiver.hpp"
#include "fs_writer.hpp"
#include "protocol.hpp"

// Fallback console name
static char s_console_name[32] = "Nintendo 3DS (ZelNeD)";

// ── Receiver thread ────────────────────────────────────────────────────────────
static void receiver_thread_fn(void*) {
    receiver_run();
}

// ── socInit buffer ─────────────────────────────────────────────────────────────
// FIX #11: 4 KB page alignment required for DMA/SOC buffer in libctru
static uint32_t __attribute__((aligned(4096))) s_soc_buf[SOC_BUFSIZE / 4];

int main() {
    // ── System service initialization ─────────────────────────────────────────
    // Enable 804 MHz clock mode and extra L2 cache on New 3DS (no-op on Old 3DS)
    osSetSpeedupEnable(true);

    romfsInit();
    cfguInit();
    acuInit();
    ptmuInit();

    // Initialize network (allocate 1 MB socket buffer)
    socInit(s_soc_buf, SOC_BUFSIZE);

    // Initialize filesystem
    fs_init();

    // Initialize UI
    ui_init();

    // ── Start beacon broadcaster ───────────────────────────────────────────────
    // IMPROVEMENT D: generate unique console name from hardware model
    u8 sys_model = 0;
    if (R_SUCCEEDED(CFGU_GetSystemModel(&sys_model))) {
        static const char* const k_models[] = {
            "3DS", "3DS XL", "New 3DS", "2DS", "New 3DS XL", "New 2DS XL"
        };
        const char* mname = (sys_model <= 5) ? k_models[sys_model] : "3DS";
        snprintf(s_console_name, sizeof(s_console_name), "ZelNeD [%s]", mname);
    }
    beacon_start(s_console_name);

    // ── Start receiver thread ─────────────────────────────────────────────────
    // Priority 0x31 (slightly lower than main), runs on APP CPU core
    Thread recv_thread = threadCreate(receiver_thread_fn, nullptr,
                                      64 * 1024,  // 64 KB stack
                                      0x31,
                                      -2,         // any core
                                      false);

    // ── Main loop (UI + input) ─────────────────────────────────────────────────
    while (aptMainLoop()) {
        hidScanInput();
        u32 keys_down = hidKeysDown();

        if (keys_down & KEY_START) {
            break;   // Exit
        }

        // FIX #6: Toggle pause on SELECT
        if (keys_down & KEY_SELECT) {
            bool next_paused = !receiver_is_paused();
            receiver_set_paused(next_paused);
        }

        // FIX #6: Clear UI log on Y
        if (keys_down & KEY_Y) {
            ui_log_clear();
        }

        // Draw current frame
        ReceiverStats stats = receiver_get_stats();
        ui_draw(stats);

        // Yield CPU to receiver thread
        svcSleepThread(16666667LL);  // ~60 fps cap (16.7 ms)
    }

    // ── Teardown ──────────────────────────────────────────────────────────────
    receiver_stop();

    // Wait for receiver thread to finish
    threadJoin(recv_thread, 3000000000ULL);  // 3 second timeout
    threadFree(recv_thread);

    beacon_stop();
    fs_exit();
    ui_exit();

    socExit();
    ptmuExit();
    acuExit();
    cfguExit();
    romfsExit();

    return 0;
}
