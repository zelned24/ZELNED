/**
 * Zel.NeD — Network Beacon (3DS UDP Broadcaster)
 * =================================================
 * Sends a ZELNED beacon UDP broadcast every 2 seconds so the PC
 * client can auto-discover the 3DS without manual IP entry.
 */
#pragma once

// Start broadcasting the beacon (runs in a background thread)
void beacon_start(const char* console_name);

// Stop the beacon thread
void beacon_stop();
