/**
 * Zel.NeD — Per-Chunk Transfer Telemetry Implementation (3DS)
 * ============================================================
 * Uses a lightweight exponential moving average (alpha=0.25) so that
 * transient spikes don't corrupt the classification but genuine trends
 * are captured within ~8 chunks.
 */
#include "telemetry.hpp"
#include <3ds.h>
#include <string.h>

// ── EMA state ────────────────────────────────────────────────────────────────
// alpha = 0.25 → new value contributes 25%, history 75%.
// Integer arithmetic: ema = (ema * 3 + new_value) / 4
static uint32_t s_ema_net = 0;
static uint32_t s_ema_cpu = 0;
static uint32_t s_ema_sd  = 0;
static uint32_t s_count   = 0;   // number of chunks recorded

// ── Public API ───────────────────────────────────────────────────────────────

void telemetry_reset() {
    s_ema_net = 0;
    s_ema_cpu = 0;
    s_ema_sd  = 0;
    s_count   = 0;
}

void telemetry_record(uint64_t net_ticks, uint64_t cpu_ticks, uint64_t sd_ticks) {
    uint32_t n = ticks_to_us(net_ticks);
    uint32_t c = ticks_to_us(cpu_ticks);
    uint32_t s = ticks_to_us(sd_ticks);

    if (s_count == 0) {
        // Seed with first sample to avoid long warmup
        s_ema_net = n;
        s_ema_cpu = c;
        s_ema_sd  = s;
    } else {
        // EMA α = 0.25 using integer math (multiply-then-shift-right equivalent)
        s_ema_net = (s_ema_net * 3 + n) >> 2;
        s_ema_cpu = (s_ema_cpu * 3 + c) >> 2;
        s_ema_sd  = (s_ema_sd  * 3 + s) >> 2;
    }
    s_count++;
}

void telemetry_fill(ZelNedChunkAckTelemetry* out, uint32_t chunk_index) {
    memset(out, 0, sizeof(*out));
    out->chunk_index = chunk_index;
    out->status      = ACK_WITH_TELEMETRY;
    out->t_net_us    = cap16(s_ema_net);
    out->t_cpu_us    = cap16(s_ema_cpu);
    out->t_sd_us     = cap16(s_ema_sd);
    out->wifi_bars   = (uint8_t)osGetWifiStrength();
    out->reserved    = 0;

    // cpu_load_pct = T_cpu / (T_net + T_cpu + T_sd) * 100
    uint32_t total = s_ema_net + s_ema_cpu + s_ema_sd;
    out->cpu_load_pct = (total > 0)
        ? (uint8_t)((s_ema_cpu * 100u) / total)
        : 0u;
}
