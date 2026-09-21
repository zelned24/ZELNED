/**
 * Zel.NeD — Resume State Implementation
 */
#include "resume.hpp"
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

static const char* RESUME_DIR = "sdmc:/ZelNeD/.resume";

static std::string _resume_path(const std::string& remote_path) {
    // Simple hash: use last 16 chars as filename (good enough for resume IDs)
    std::string safe_name = remote_path;
    for (char& c : safe_name) {
        if (c == '/' || c == '\\' || c == ':') c = '_';
    }
    if (safe_name.length() > 48) safe_name = safe_name.substr(safe_name.length() - 48);
    return std::string(RESUME_DIR) + "/" + safe_name + ".bin";
}

bool resume_save(const std::string& remote_path, uint64_t bytes_received, uint32_t chunk_index) {
    mkdir("sdmc:/ZelNeD", 0777);
    mkdir(RESUME_DIR, 0777);

    ResumeRecord rec = {};
    strncpy(rec.remote_path, remote_path.c_str(), sizeof(rec.remote_path) - 1);
    rec.bytes_received   = bytes_received;
    rec.last_chunk_index = chunk_index;

    std::string path = _resume_path(remote_path);
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) return false;
    bool ok = (fwrite(&rec, sizeof(rec), 1, f) == 1);
    fclose(f);
    return ok;
}

uint64_t resume_load(const std::string& remote_path, uint32_t* out_chunk_index) {
    std::string path = _resume_path(remote_path);
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return 0;

    ResumeRecord rec = {};
    if (fread(&rec, sizeof(rec), 1, f) != 1) {
        fclose(f);
        return 0;
    }
    fclose(f);

    // Validate the record belongs to the same file
    if (strncmp(rec.remote_path, remote_path.c_str(), sizeof(rec.remote_path)) != 0) return 0;

    if (out_chunk_index) *out_chunk_index = rec.last_chunk_index;
    return rec.bytes_received;
}

void resume_clear(const std::string& remote_path) {
    std::string path = _resume_path(remote_path);
    remove(path.c_str());
}
