/**
 * Zel.NeD — Filesystem Writer (3DS)
 * ====================================
 * Safe, atomic writes to sdmc:/ with path traversal guard.
 * Files are written as .zelned_tmp and renamed on completion.
 */
#pragma once
#include <string>
#include <cstdint>

// Initialize the filesystem (call once at startup)
bool fs_init();
void fs_exit();

// Check available free bytes on sdmc:/
uint64_t fs_free_bytes();

// Validate a relative path — returns false if it tries to escape sdmc:/
bool fs_path_safe(const std::string& rel_path);

// Create directories recursively under sdmc:/
bool fs_mkdir_recursive(const std::string& rel_path);

// File write session (atomic: write to .tmp, then rename)
struct FsWriter {
    bool open(const std::string& rel_path, uint64_t total_size = 0, uint64_t resume_offset = 0);
    bool write(const void* data, size_t len);
    bool commit();       // Rename .tmp → final name
    void abort();        // Delete .tmp
    bool is_open() const { return _handle != nullptr; }

private:
    void*       _handle = nullptr;
    std::string _tmp_path;
    std::string _final_path;
};
