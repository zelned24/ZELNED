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
#include <malloc.h>
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

int main() {
    // 1. Inicializar UI e interfaces gráficas PRIMERO
    ui_init();

    printf("\x1b[1;36m=== Zel.NeD 3DS Receiver v1.0 ===\x1b[0m\n\n");
    printf("[*] Iniciando servicios del sistema...\n");

    // Inicializar estado del receptor y cerrojos antes de cualquier hilo
    receiver_init();

    // 2. Servicios del sistema
    osSetSpeedupEnable(true);
    cfguInit();
    ptmuInit();
    fs_init();
    amInit();

    // 3. Inicializar red (soc:u) asignando memoria alineada en el HEAP
    printf("[*] Asignando memoria de red (1 MB)...\n");
    uint32_t* soc_buf = (uint32_t*)memalign(0x1000, SOC_BUFSIZE);
    bool soc_ok = false;
    if (soc_buf) {
        Result soc_res = socInit(soc_buf, SOC_BUFSIZE);
        if (R_SUCCEEDED(soc_res)) {
            soc_ok = true;
            printf("\x1b[1;32m[OK] Red SOC inicializada con exito.\x1b[0m\n");
        } else {
            printf("\x1b[1;31m[ERROR] socInit fallo con codigo: 0x%08lX\x1b[0m\n", (unsigned long)soc_res);
        }
    } else {
        printf("\x1b[1;31m[ERROR] memalign fallo para buffer de red!\x1b[0m\n");
    }

    // 4. Nombre de la consola segun hardware e IP
    u8 sys_model = 0;
    if (R_SUCCEEDED(CFGU_GetSystemModel(&sys_model))) {
        static const char* const k_models[] = {
            "3DS", "3DS XL", "New 3DS", "2DS", "New 3DS XL", "New 2DS XL"
        };
        const char* mname = (sys_model <= 5) ? k_models[sys_model] : "3DS";
        snprintf(s_console_name, sizeof(s_console_name), "ZelNeD [%s]", mname);
    }

    char ip_buf[24] = "Desconocida";
    if (soc_ok) {
        uint32_t ip_host = ntohl((uint32_t)gethostid());
        if (ip_host != 0) {
            snprintf(ip_buf, sizeof(ip_buf), "%u.%u.%u.%u",
                     (unsigned int)((ip_host >> 24) & 0xFF), (unsigned int)((ip_host >> 16) & 0xFF),
                     (unsigned int)((ip_host >> 8)  & 0xFF), (unsigned int)(ip_host & 0xFF));
        }
    }

    // Actualizar informacion en la pantalla superior (Citro2D)
    ui_set_console_info(s_console_name, ip_buf);

    // Pantalla inferior estilizada (Consola 320x240 -> 40 cols x 30 rows)
    printf("\x1b[2J\x1b[H"); // Limpiar pantalla y mover cursor al inicio
    printf("\x1b[1;36m+------------------------------------+\x1b[0m\n");
    printf("\x1b[1;36m| \x1b[1;37mZel.NeD 3DS Receiver v1.1          \x1b[1;36m|\x1b[0m\n");
    printf("\x1b[1;36m| \x1b[90mWireLess High-Speed Transfer       \x1b[1;36m|\x1b[0m\n");
    printf("\x1b[1;36m+------------------------------------+\x1b[0m\n");
    printf("\x1b[1;36m| \x1b[32mConsola: \x1b[37m%-25.25s \x1b[1;36m|\x1b[0m\n", s_console_name);
    printf("\x1b[1;36m| \x1b[33mIP:      \x1b[37m%-25.25s \x1b[1;36m|\x1b[0m\n", ip_buf);
    printf("\x1b[1;36m| \x1b[35mTCP: \x1b[37m%-5d     \x1b[35mBeacon UDP: \x1b[37m%-5d \x1b[1;36m|\x1b[0m\n", TCP_PORT, UDP_BEACON_PORT);
    printf("\x1b[1;36m+------------------------------------+\x1b[0m\n");
    printf("\x1b[1;32m[*] Sistema listo para recibir...\x1b[0m\n");
    printf("\x1b[90m--------------------------------------\x1b[0m\n");

    // 5. Iniciar beacon y receptor solo si la red inicio correctamente
    Thread recv_thread = nullptr;
    if (soc_ok) {
        beacon_start(s_console_name);
        recv_thread = threadCreate(receiver_thread_fn, nullptr,
                                   64 * 1024,  // 64 KB stack
                                   0x31,
                                   -2,         // any core
                                   false);
    } else {
        printf("\x1b[1;31m[!] No se pudo iniciar el receptor de red.\x1b[0m\n");
        printf("Pulsa [START] para salir al Homebrew Launcher.\n");
    }

    // ── Bucle principal (UI + input) ───────────────────────────────────────────
    while (aptMainLoop()) {
        hidScanInput();
        u32 keys_down = hidKeysDown();

        // Salir limpiamente con START o con B
        if (keys_down & (KEY_START | KEY_B)) {
            break;
        }

        // Pausar/Reanudar en SELECT
        if (keys_down & KEY_SELECT) {
            bool next_paused = !receiver_is_paused();
            receiver_set_paused(next_paused);
            printf(next_paused ? "\x1b[1;33m[*] Transferencia en PAUSA\x1b[0m\n" : "\x1b[1;32m[*] Transferencia REANUDADA\x1b[0m\n");
        }

        // Limpiar log en Y
        if (keys_down & KEY_Y) {
            ui_log_clear();
            printf("\x1b[1;36m+------------------------------------+\x1b[0m\n");
            printf("\x1b[1;36m| \x1b[1;37mZel.NeD 3DS Receiver v1.1          \x1b[1;36m|\x1b[0m\n");
            printf("\x1b[1;36m+------------------------------------+\x1b[0m\n");
            printf("\x1b[1;32m[*] Log limpiado.\x1b[0m\n");
        }

        // Dibujar fotograma en pantalla superior
        ReceiverStats stats = receiver_get_stats();
        ui_draw(stats);

        // Ceder CPU
        svcSleepThread(16666667LL);  // ~60 fps
    }

    // ── Limpieza y cierre ─────────────────────────────────────────────────────
    printf("\n\x1b[1;33m[*] Cerrando Zel.NeD... ¡Hasta pronto!\x1b[0m\n");
    if (soc_ok) {
        receiver_stop();
        if (recv_thread) {
            threadJoin(recv_thread, 1000000000ULL);  // timeout 1 seg
            threadFree(recv_thread);
            recv_thread = nullptr;
        }
        beacon_stop();
    }

    fs_exit();
    ui_exit();

    if (soc_ok) {
        socExit();
    }
    if (soc_buf) {
        free(soc_buf);
    }

    amExit();
    ptmuExit();
    cfguExit();

    return 0;
}
