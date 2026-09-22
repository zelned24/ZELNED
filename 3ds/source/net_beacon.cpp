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
static Thread        s_beacon_thread   = nullptr;
static int           s_beacon_sock     = -1;

static void beacon_thread_fn(void* arg) {
    const char* name = static_cast<const char*>(arg);

    // Build beacon packet
    ZelNedBeacon beacon = {};
    memcpy(beacon.magic, ZELNED_MAGIC, 6);
    beacon.version  = ZELNED_VERSION;
    beacon.tcp_port = TCP_PORT;
    strncpy(beacon.name, name, sizeof(beacon.name) - 1);

    // Create UDP broadcast socket
    s_beacon_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s_beacon_sock < 0) return;

    int bcast = 1;
    setsockopt(s_beacon_sock, SOL_SOCKET, SO_BROADCAST, &bcast, sizeof(bcast));

    // Global broadcast 255.255.255.255
    struct sockaddr_in dest_global = {};
    dest_global.sin_family      = AF_INET;
    dest_global.sin_port        = htons(UDP_BEACON_PORT);
    dest_global.sin_addr.s_addr = htonl(INADDR_BROADCAST);

    // Subnet broadcast (e.g. 192.168.18.255) to bypass router limited broadcast filters
    uint32_t ip_host = ntohl((uint32_t)gethostid());
    uint32_t subnet_bcast = (ip_host != 0) ? (ip_host | 0x000000FF) : 0xFFFFFFFF;
    struct sockaddr_in dest_subnet = {};
    dest_subnet.sin_family      = AF_INET;
    dest_subnet.sin_port        = htons(UDP_BEACON_PORT);
    dest_subnet.sin_addr.s_addr = htonl(subnet_bcast);

    while (s_beacon_running) {
        sendto(s_beacon_sock, &beacon, sizeof(beacon), 0,
               (struct sockaddr*)&dest_global, sizeof(dest_global));
        if (subnet_bcast != 0xFFFFFFFF) {
            sendto(s_beacon_sock, &beacon, sizeof(beacon), 0,
                   (struct sockaddr*)&dest_subnet, sizeof(dest_subnet));
        }

        // Sleep 2 seconds in 100ms slices to allow quick shutdown
        for (int i = 0; i < 20 && s_beacon_running; i++) {
            svcSleepThread(100000000LL);  // 100 ms
        }
    }

    if (s_beacon_sock >= 0) {
        close(s_beacon_sock);
        s_beacon_sock = -1;
    }
}

void beacon_start(const char* console_name) {
    s_beacon_running = true;
    s_beacon_thread = threadCreate(beacon_thread_fn,
                                   (void*)console_name,
                                   16384,
                                   0x30,   // priority (lower = higher prio)
                                   -2,     // CPU core (-2 = any)
                                   false);
}

void beacon_stop() {
    s_beacon_running = false;
    if (s_beacon_sock >= 0) {
        close(s_beacon_sock);
        s_beacon_sock = -1;
    }
    if (s_beacon_thread) {
        threadJoin(s_beacon_thread, 500000000ULL);  // 500 ms timeout
        threadFree(s_beacon_thread);
        s_beacon_thread = nullptr;
    }
}
