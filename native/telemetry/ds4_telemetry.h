/* DwarfStar runtime telemetry and calibration capture.
 *
 * Optional observation layer for the Qwen3.8/Qwen4Exp Metal graph. It never
 * changes model arithmetic: it reads values the graph already computed and
 * sends counters over loopback UDP.
 *
 * Telemetry is configured by environment at process start:
 *   DS4_TELEMETRY_UDP=<port>      destination for JSON datagrams (127.0.0.1)
 *   DS4_TELEMETRY_CONTROL=<port>  UDP port this process listens on for
 *                                 {"level":N,"ttl_ms":M} heartbeats
 *   DS4_TELEMETRY_MODEL=<id>      tag copied into every datagram
 * With no heartbeat the level is 0 and every hook is a single integer test.
 *   level 1: per-cycle counters (tokens, MTP drafts, n-gram disk reads)
 *   level 2: level 1 plus the router's selected experts per layer
 *
 * Calibration capture is a separate, offline mode:
 *   DS4_QWEN4_CALIB_DIR=<dir>     accumulate per-layer second moments of the
 *                                 expert inputs and write them at exit
 */
#ifndef DS4_TELEMETRY_H
#define DS4_TELEMETRY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define DS4_TM_ROWS 4u        /* rows of router output kept per layer */
#define DS4_TM_MAX_USED 16u   /* upper bound on experts selected per token */
#define DS4_TM_MAX_LAYER 128u

bool ds4_tm_configured(void);
int ds4_tm_level(void);
void ds4_tm_log_line(const char *type, const char *line);
void ds4_tm_ple_read(uint32_t bytes);
void ds4_tm_cycle_begin(void);
void ds4_tm_cycle_drafts(int proposed, int accepted);
/* selected/weights: [n_layer][DS4_TM_ROWS][DS4_TM_MAX_USED]; mask bit i set when layer i was captured. */
void ds4_tm_cycle_end(uint32_t pos, int committed, const int32_t *selected, const float *weights,
                      uint32_t n_layer, uint32_t n_used, const uint64_t *layer_mask);

bool ds4_calib_enabled(void);
void ds4_calib_moe(uint32_t layer, uint32_t n_layer, const float *x, uint32_t n_tokens, uint32_t n_embd,
                   const float *mid, uint32_t mid_slots, uint32_t n_used, uint32_t n_ff,
                   const int32_t *selected, uint32_t n_expert);

#endif
