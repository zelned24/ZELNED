/**
 * Zel.NeD — CRC32 (3DS ARM11 implementation)
 * ============================================
 * Uses a precomputed lookup table (512 bytes) for fast per-byte CRC.
 * ~1 cycle per byte on ARM11 @ 268 MHz.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

uint32_t zelned_crc32(const void* data, size_t length, uint32_t crc_init = 0xFFFFFFFF);
