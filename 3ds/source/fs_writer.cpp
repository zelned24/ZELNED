/**
 * Zel.NeD -- Filesystem Writer Implementation
 *
 * Key optimization: uses FSFILE_Write with flag=0 (NO FS_WRITE_FLUSH).
 * The standard devoptab stdio path calls FSFILE_Write with FS_WRITE_FLUSH on
 * every fwrite(), which forces a full SD-card sync per chunk (~120-160 ms each).
 * By using FSFILE_Write directly with flag=0, the ARM9 caches the write and
 * returns immediately; the SD-card DMA runs asynchronously while the ARM11
 * is already receiving the next chunk over Wi-Fi.
 * A single FSFILE_Flush() at commit time is the only synchronous SD wait.
 */
#include "fs_writer.hpp"
#include <3ds.h>
#include <3ds/services/fs.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>
#include <unistd.h>

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
    if (rel_path[0] == '/' || rel_path[0] == '\\') return false;
    if (rel_path.find("..") != std::string::npos) return false;
    static const char* BANNED[] = {"nand:", "twln:", "bis:", "ctr-nand:", nullptr};
    for (int i = 0; BANNED[i]; ++i) {
        if (rel_path.find(BANNED[i]) != std::string::npos) return false;
    }
    return true;
}

bool fs_mkdir_recursive(const std::string& rel_path) {
    if (!fs_path_safe(rel_path)) return false;
    std::string full = "sdmc:/" + rel_path;
    std::string current;
    for (char c : full) {
        current += c;
        if (c == '/') {
            mkdir(current.c_str(), 0777);
        }
    }
    mkdir(full.c_str(), 0777);
    return true;
}

// -- FsWriter ------------------------------------------------------------------

bool FsWriter::open(const std::string& rel_path, uint64_t total_size, uint64_t resume_offset) {
    if (!fs_path_safe(rel_path)) return false;

    _final_path   = "sdmc:/" + rel_path;
    _tmp_path     = _final_path + ".zelned_tmp";
    _write_offset = resume_offset;
    _file_open    = false;
    _archive_open = false;
    _stdio_handle = nullptr;

    // Ensure parent dirs exist
    size_t last_slash = rel_path.rfind('/');
    if (last_slash != std::string::npos) {
        if (!fs_mkdir_recursive(rel_path.substr(0, last_slash))) return false;
    }

    // Handle resume: validate existing tmp file
    if (resume_offset > 0) {
        struct stat st;
        if (stat(_tmp_path.c_str(), &st) != 0 || (uint64_t)st.st_size != resume_offset) {
            return false;
        }
    } else {
        remove(_tmp_path.c_str());  // clean up any stale tmp
    }

    // ---- Open SDMC archive for direct FSFILE access -------------------------
    if (R_FAILED(FSUSER_OpenArchive(&_archive, ARCHIVE_SDMC, fsMakePath(PATH_EMPTY, "")))) {
        goto fallback_stdio;
    }
    _archive_open = true;

    {
        // FSFILE path = "/" + rel_path + ".zelned_tmp" (no "sdmc:" prefix)
        std::string fspath = "/" + rel_path + ".zelned_tmp";

        u32 open_flags = FS_OPEN_WRITE | FS_OPEN_CREATE;
        if (resume_offset > 0) open_flags = FS_OPEN_WRITE;  // don't truncate on resume

        if (R_FAILED(FSUSER_OpenFile(&_file_handle, _archive,
                                     fsMakePath(PATH_ASCII, fspath.c_str()),
                                     open_flags, 0))) {
            FSUSER_CloseArchive(_archive);
            _archive_open = false;
            goto fallback_stdio;
        }
    }
    _file_open = true;

    // Pre-allocate contiguous clusters to minimise FAT fragmentation
    if (total_size > 0 && resume_offset == 0) {
        FSFILE_SetSize(_file_handle, total_size);
    }
    return true;

fallback_stdio:
    // If FSFILE open fails, fall back to buffered stdio (slower but safe)
    {
        const char* mode = (resume_offset > 0) ? "ab" : "wb";
        _stdio_handle = fopen(_tmp_path.c_str(), mode);
        if (!_stdio_handle) return false;
        // Large stdio buffer to batch the partial last chunk
        static uint8_t s_buf[524288];
        setvbuf(static_cast<FILE*>(_stdio_handle), (char*)s_buf, _IOFBF, sizeof(s_buf));
        _file_open = true;
        return true;
    }
}

bool FsWriter::write(const void* data, size_t len) {
    if (!_file_open) return false;

    if (_stdio_handle) {
        // Fallback: stdio write (with FS_WRITE_FLUSH per batch -- slow but safe)
        const uint8_t* ptr = static_cast<const uint8_t*>(data);
        size_t remaining = len;
        while (remaining > 0) {
            size_t to_write = (remaining > 0x100000) ? 0x100000 : remaining;
            size_t written  = fwrite(ptr, 1, to_write, static_cast<FILE*>(_stdio_handle));
            if (written == 0) return false;
            ptr       += written;
            remaining -= written;
        }
        return true;
    }

    // Direct FSFILE_Write with flag=0 (NO FS_WRITE_FLUSH per chunk).
    // ARM9 queues the write to its cache and returns immediately.
    // SD-card DMA continues in background while ARM11 gets next chunk.
    const uint8_t* ptr = static_cast<const uint8_t*>(data);
    size_t remaining = len;
    while (remaining > 0) {
        u32 to_write = (u32)((remaining > 0x100000) ? 0x100000 : remaining);
        u32 written  = 0;
        Result r = FSFILE_Write(_file_handle, &written, _write_offset, ptr, to_write, 0);
        if (R_FAILED(r) || written == 0) return false;
        ptr           += written;
        _write_offset += written;
        remaining     -= written;
    }
    return true;
}

bool FsWriter::commit() {
    if (!_file_open) return false;

    if (_stdio_handle) {
        fclose(static_cast<FILE*>(_stdio_handle));
        _stdio_handle = nullptr;
        _file_open = false;
        remove(_final_path.c_str());
        return (rename(_tmp_path.c_str(), _final_path.c_str()) == 0);
    }

    // Flush all cached data to SD card (single wait at end of transfer)
    FSFILE_Flush(_file_handle);
    FSFILE_Close(_file_handle);
    _file_open = false;

    if (_archive_open) {
        FSUSER_CloseArchive(_archive);
        _archive_open = false;
    }

    // Atomic rename via stdio devoptab
    remove(_final_path.c_str());
    return (rename(_tmp_path.c_str(), _final_path.c_str()) == 0);
}

void FsWriter::abort() {
    if (_file_open) {
        if (_stdio_handle) {
            fclose(static_cast<FILE*>(_stdio_handle));
            _stdio_handle = nullptr;
        } else {
            FSFILE_Close(_file_handle);
        }
        _file_open = false;
    }
    if (_archive_open) {
        FSUSER_CloseArchive(_archive);
        _archive_open = false;
    }
    remove(_tmp_path.c_str());
}
