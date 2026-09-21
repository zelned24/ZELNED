/**
 * Zel.NeD — UDP Beacon Broadcaster Implementation
 */
#include "net_beacon.hpp"
#include "protocol.hpp"
#include <3ds.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <string.h>
#include <unistd.h>

static volatile bool s_beacon_running = false;
static Thread        s_beacon_thread;

static void beacon_thread_fn(void* arg) {
    const char* name = static_cast<const char*>(arg);

    // Build beacon packet
    ZelNedBeacon beacon = {};
    memcpy(beacon.magic, ZELNED_MAGIC, 6);
    beacon.version  = ZELNED_VERSION;
    beacon.tcp_port = TCP_PORT;
    strncpy(beacon.name, name, sizeof(beacon.name) - 1);

    // Create UDP broadcast socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) return;

    int bcast = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &bcast, sizeof(bcast));

    struct sockaddr_in dest = {};
    dest.sin_family      = AF_INET;
    dest.sin_port        = htons(UDP_BEACON_PORT);
    dest.sin_addr.s_addr = htonl(INADDR_BROADCAST);

    while (s_beacon_running) {
        sendto(sock, &beacon, sizeof(beacon), 0,
               (struct sockaddr*)&dest, sizeof(dest));
        // Sleep 2 seconds in 100ms slices to allow quick shutdown
        for (int i = 0; i < 20 && s_beacon_running; i++) {
            svcSleepThread(100000000LL);  // 100 ms
        }
    }

    close(sock);
}

void beacon_start(const char* console_name) {
    s_beacon_running = true;
    // FIX #10: Stack size 16 KB for socket operations in ctrulib
    s_beacon_thread = threadCreate(beacon_thread_fn,
                                   (void*)console_name,
                                   16384,
                                   0x30,   // priority (lower = higher prio)
                                   -2,     // CPU core (-2 = any)
                                   false);
}

void beacon_stop() {
    s_beacon_running = false;
    if (s_beacon_thread) {
        threadJoin(s_beacon_thread, U64_MAX);
        threadFree(s_beacon_thread);
        s_beacon_thread = nullptr;
    }
}
