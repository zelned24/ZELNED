/**
 * Zel.NeD — Filesystem Writer Implementation
 */
#include "fs_writer.hpp"
#include <3ds.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>

static bool s_fs_ready = false;

bool fs_init() {
    s_fs_ready = true;
    return true;
}

void fs_exit() {
    s_fs_ready = false;
}

uint64_t fs_free_bytes() {
    FS_ArchiveResource resource = {};
    if (R_SUCCEEDED(FSUSER_GetArchiveResource(&resource, SYSTEM_MEDIATYPE_SD))) {
        return (uint64_t)resource.freeClusters * resource.clusterSize;
    }
    return 0;
}

bool fs_path_safe(const std::string& rel_path) {
    if (rel_path.empty()) return false;
    // Reject absolute paths
    if (rel_path[0] == '/' || rel_path[0] == '\\') return false;
    // Reject traversal attempts
    if (rel_path.find("..") != std::string::npos) return false;
    // Reject dangerous prefixes
    static const char* BANNED[] = {"nand:", "twln:", "bis:", "ctr-nand:", nullptr};
    for (int i = 0; BANNED[i]; ++i) {
        if (rel_path.find(BANNED[i]) != std::string::npos) return false;
    }
    return true;
}

bool fs_mkdir_recursive(const std::string& rel_path) {
    if (!fs_path_safe(rel_path)) return false;  // FIX #5: sanitize directory path

    std::string full = "sdmc:/" + rel_path;
    std::string current;
    for (char c : full) {
        current += c;
        if (c == '/') {
            mkdir(current.c_str(), 0777);  // ignore errors for existing dirs
        }
    }
    mkdir(full.c_str(), 0777);
    return true;
}

// ── FsWriter ──────────────────────────────────────────────────────────────────

bool FsWriter::open(const std::string& rel_path, uint64_t resume_offset) {
    if (!fs_path_safe(rel_path)) return false;

    _final_path = "sdmc:/" + rel_path;
    _tmp_path   = _final_path + ".zelned_tmp";

    // Ensure parent directories exist
    size_t last_slash = rel_path.rfind('/');
    if (last_slash != std::string::npos) {
        if (!fs_mkdir_recursive(rel_path.substr(0, last_slash))) return false;
    }

    const char* mode = "wb";
    if (resume_offset > 0) {
        // FIX #13: verify existing .tmp file size exactly matches resume_offset
        struct stat st;
        if (stat(_tmp_path.c_str(), &st) == 0 && (uint64_t)st.st_size == resume_offset) {
            mode = "ab";
        } else {
            // Temp file missing or size mismatch; reject to avoid corrupting file
            return false;
        }
    }

    _handle = fopen(_tmp_path.c_str(), mode);
    return (_handle != nullptr);
}

bool FsWriter::write(const void* data, size_t len) {
    if (!_handle) return false;
    // Write in 128 KB chunks for optimal SD card throughput
    const uint8_t* ptr = static_cast<const uint8_t*>(data);
    size_t remaining = len;
    while (remaining > 0) {
        size_t to_write = (remaining > 131072) ? 131072 : remaining;
        size_t written = fwrite(ptr, 1, to_write, static_cast<FILE*>(_handle));
        if (written == 0) return false;
        ptr       += written;
        remaining -= written;
    }
    return true;
}

bool FsWriter::commit() {
    if (!_handle) return false;
    fclose(static_cast<FILE*>(_handle));
    _handle = nullptr;
    // Atomic rename: .zelned_tmp → final name
    remove(_final_path.c_str());   // remove existing file if present (resume)
    return (rename(_tmp_path.c_str(), _final_path.c_str()) == 0);
}

void FsWriter::abort() {
    if (_handle) {
        fclose(static_cast<FILE*>(_handle));
        _handle = nullptr;
    }
    remove(_tmp_path.c_str());
}
