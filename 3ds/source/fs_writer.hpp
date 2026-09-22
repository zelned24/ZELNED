/**
 * Zel.NeD — Filesystem Writer (3DS)
 * ====================================
 * Safe, atomic writes to sdmc:/ with path traversal guard.
 * Files are written as .zelned_tmp and renamed on completion.
 *
 * Uses FSFILE_Write directly (bypass stdio) with NO per-chunk flush.
 * The ARM9 writes to SD asynchronously while ARM11 receives the next chunk.
 * FSFILE_Flush() is called only once at commit time.
 */
#pragma once
#include <string>
#include <cstdint>
#include <3ds.h>

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
    bool commit();       // Flush + close + rename .tmp → final name
    void abort();        // Close + delete .tmp
    bool is_open() const { return _file_open; }

private:
    FS_Archive  _archive      = {};
    Handle      _file_handle  = 0;
    uint64_t    _write_offset = 0;
    bool        _archive_open = false;
    bool        _file_open    = false;
    std::string _tmp_path;
    std::string _final_path;
    // Fallback stdio handle (used only if FSFILE open fails)
    void*       _stdio_handle = nullptr;
};
