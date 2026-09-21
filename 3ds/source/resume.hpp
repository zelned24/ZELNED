/**
 * Zel.NeD — Resume State (3DS)
 * ==============================
 * Saves and loads the byte offset of an in-progress transfer to
 * sdmc:/ZelNeD/.resume so the PC can continue if Wi-Fi drops.
 */
#pragma once
#include <string>
#include <cstdint>

struct ResumeRecord {
    char     remote_path[256];
    uint64_t bytes_received;
    uint32_t last_chunk_index;
};

// Save current offset for a transfer
bool resume_save(const std::string& remote_path, uint64_t bytes_received, uint32_t chunk_index);

// Load saved offset (returns 0 if none / not found)
uint64_t resume_load(const std::string& remote_path, uint32_t* out_chunk_index = nullptr);

// Clear resume state after successful transfer
void resume_clear(const std::string& remote_path);
