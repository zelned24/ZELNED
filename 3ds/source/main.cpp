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
#include "ui.hpp"
#include "net_beacon.hpp"
#include "net_receiver.hpp"
#include "fs_writer.hpp"

// Console name shown in the PC discovery list
#define CONSOLE_NAME "3DS XL - ZelNeD"

// ── Receiver thread ────────────────────────────────────────────────────────────
static void receiver_thread_fn(void*) {
    receiver_run();
}

// ── socInit buffer ─────────────────────────────────────────────────────────────
static uint32_t s_soc_buf[SOC_BUFSIZE / 4];

int main() {
    // ── System service initialization ─────────────────────────────────────────
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
    beacon_start(CONSOLE_NAME);

    // ── Start receiver thread ─────────────────────────────────────────────────
    // Priority 0x31 (slightly lower than main), runs on APP CPU core
    Thread recv_thread = threadCreate(receiver_thread_fn, nullptr,
                                      64 * 1024,  // 64 KB stack
                                      0x31,
                                      -2,         // any core
                                      false);

    // ── Main loop (UI + input) ─────────────────────────────────────────────────
    bool paused = false;

    while (aptMainLoop()) {
        hidScanInput();
        u32 keys_down = hidKeysDown();

        if (keys_down & KEY_START) {
            break;   // Exit
        }

        if (keys_down & KEY_SELECT) {
            paused = !paused;
            // TODO: notify receiver of pause state
        }

        if (keys_down & KEY_Y) {
            // Clear log (future: call ui_log_clear())
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
