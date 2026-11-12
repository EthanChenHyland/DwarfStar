#!/usr/bin/env python3
"""Insert the telemetry and calibration hooks into a pinned ds4 source tree.

    apply_hooks.py SOURCE_DIR

The hooks are a handful of guarded calls; every edit asserts that its anchor
text occurs exactly as often as expected in the pinned source, so the script
fails loudly on any other revision. The result is also kept as
``patches/telemetry-hooks.patch`` for review.
"""
import shutil
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
HERE = Path(__file__).resolve().parent


def edit(name, replacements):
    path = SRC / name
    text = path.read_text()
    for old, new, count, limit in replacements:
        found = text.count(old)
        if found != count:
            raise SystemExit(f"{name}: expected {count} of {old[:70]!r}, found {found}")
        text = text.replace(old, new, limit)
    path.write_text(text)


SPEC_SIGNATURE = """static int ds4_session_qwen4_spec_cycle(ds4_session *s, int first_token, float temperature, int top_k,
                                        float top_p, float min_p, uint64_t *rng, bool exact_sampling,
                                        int *accepted, int accepted_cap,
                                        char *err, size_t errlen) {
"""
SPEC_WRAPPER = """static int ds4_session_qwen4_spec_cycle_run(ds4_session *s, int first_token, float temperature, int top_k,
                                            float top_p, float min_p, uint64_t *rng, bool exact_sampling,
                                            int *accepted, int accepted_cap,
                                            char *err, size_t errlen);

/* Telemetry wrapper: one event per verify cycle. It only reads what the cycle computed. */
static int ds4_session_qwen4_spec_cycle(ds4_session *s, int first_token, float temperature, int top_k,
                                        float top_p, float min_p, uint64_t *rng, bool exact_sampling,
                                        int *accepted, int accepted_cap,
                                        char *err, size_t errlen) {
    if (ds4_tm_level() < 1) {
        return ds4_session_qwen4_spec_cycle_run(s, first_token, temperature, top_k, top_p, min_p, rng,
                                                exact_sampling, accepted, accepted_cap, err, errlen);
    }
    const uint32_t tm_pos = s->qwen4_graph.pos;
    memset(g_qwen4_tm.mask, 0, sizeof(g_qwen4_tm.mask));
    ds4_tm_cycle_begin();
    const int rc = ds4_session_qwen4_spec_cycle_run(s, first_token, temperature, top_k, top_p, min_p, rng,
                                                    exact_sampling, accepted, accepted_cap, err, errlen);
    ds4_tm_cycle_end(tm_pos, rc,
                     g_qwen4_tm.sel ? ds4_gpu_tensor_contents(g_qwen4_tm.sel) : NULL,
                     g_qwen4_tm.wts ? ds4_gpu_tensor_contents(g_qwen4_tm.wts) : NULL,
                     DS4_N_LAYER, DS4_N_EXPERT_USED, g_qwen4_tm.mask);
    return rc;
}

static int ds4_session_qwen4_spec_cycle_run(ds4_session *s, int first_token, float temperature, int top_k,
                                            float top_p, float min_p, uint64_t *rng, bool exact_sampling,
                                            int *accepted, int accepted_cap,
                                            char *err, size_t errlen) {
"""

TM_STATE = """/* Telemetry: per-layer views that receive the router's top-k output for the
 * first rows of a decode step.  The kernels and their inputs are unchanged;
 * only the buffer the router writes and the expert kernels read differs. */
static struct {
    ds4_gpu_tensor *sel, *wts;
    ds4_gpu_tensor *sel_view[DS4_TM_MAX_LAYER], *wts_view[DS4_TM_MAX_LAYER];
    uint64_t mask[DS4_TM_MAX_LAYER / 64u];
    int ready;            /* 0 untried, 1 usable, -1 unavailable */
    uint32_t mid_slots;   /* layout of g->mid after the last per-token expert pass; 0 = not per token */
} g_qwen4_tm;

static bool qwen4_tm_ready(void) {
    if (g_qwen4_tm.ready) return g_qwen4_tm.ready > 0;
    g_qwen4_tm.ready = -1;
    if (DS4_N_LAYER > DS4_TM_MAX_LAYER || DS4_N_EXPERT_USED > DS4_TM_MAX_USED) return false;
    const uint64_t per_layer = (uint64_t)DS4_TM_ROWS * DS4_TM_MAX_USED;
    g_qwen4_tm.sel = qwen4_graph_alloc_f32((uint64_t)DS4_N_LAYER * per_layer);
    g_qwen4_tm.wts = qwen4_graph_alloc_f32((uint64_t)DS4_N_LAYER * per_layer);
    if (!g_qwen4_tm.sel || !g_qwen4_tm.wts) return false;
    for (uint32_t il = 0; il < DS4_N_LAYER; il++) {
        g_qwen4_tm.sel_view[il] = ds4_gpu_tensor_view(g_qwen4_tm.sel, il * per_layer * sizeof(float), per_layer * sizeof(float));
        g_qwen4_tm.wts_view[il] = ds4_gpu_tensor_view(g_qwen4_tm.wts, il * per_layer * sizeof(float), per_layer * sizeof(float));
        if (!g_qwen4_tm.sel_view[il] || !g_qwen4_tm.wts_view[il]) return false;
    }
    g_qwen4_tm.ready = 1;
    return true;
}

"""

CALIB = """/* Offline calibration capture: wait for this layer's kernels, then accumulate
 * second moments of the expert inputs on the host.  Slow by design. */
static bool qwen4_calib_capture(ds4_qwen4_gpu_graph *g, uint32_t il, uint32_t T) {
    if (!ds4_gpu_end_commands()) return false;
    ds4_calib_moe(il, DS4_N_LAYER, ds4_gpu_tensor_contents(g->mixed), T, DS4_N_EMBD,
                  g_qwen4_tm.mid_slots ? ds4_gpu_tensor_contents(g->mid) : NULL, g_qwen4_tm.mid_slots,
                  DS4_N_EXPERT_USED, DS4_N_FF_EXP, ds4_gpu_tensor_contents(g->selected), DS4_N_EXPERT);
    return glm_graph_begin_commands_if_needed();
}

"""

LAYER_OLD = """    qwen4_ane_set_moe_layer((int)il);
    bool ok = qwen4_graph_moe(g, m, l, T);   /* the reduce folds the combine in */
    qwen4_ane_set_moe_layer(-1);
"""
LAYER_NEW = """    qwen4_ane_set_moe_layer((int)il);
    ds4_gpu_tensor *const tm_sel = g->selected, *const tm_wts = g->weights;
    const bool tm_capture = T <= DS4_TM_ROWS && ds4_tm_level() >= 2 && qwen4_tm_ready() &&
        !((g_qwen4_tm.mask[il / 64u] >> (il % 64u)) & 1u);
    if (tm_capture) {
        g->selected = g_qwen4_tm.sel_view[il];
        g->weights = g_qwen4_tm.wts_view[il];
    }
    bool ok = qwen4_graph_moe(g, m, l, T);   /* the reduce folds the combine in */
    if (tm_capture) {
        g->selected = tm_sel;
        g->weights = tm_wts;
        if (ok) g_qwen4_tm.mask[il / 64u] |= 1ull << (il % 64u);
    }
    if (ok && ds4_calib_enabled()) ok = qwen4_calib_capture(g, il, T);
    qwen4_ane_set_moe_layer(-1);
"""

edit("ds4.c", [
    ('#include "ds4.h"\n#include "ds4_tool_text.h"\n', '#include "ds4.h"\n#include "ds4_telemetry.h"\n#include "ds4_tool_text.h"\n', 1, 1),
    ("        done += (uint32_t)n;\n    }\n    if (iq4_nl) {\n        qwen4_ngram_iq4_nl_row(raw, (uint32_t)t->dim[0], out);",
     "        done += (uint32_t)n;\n    }\n    ds4_tm_ple_read(bytes);\n    if (iq4_nl) {\n        qwen4_ngram_iq4_nl_row(raw, (uint32_t)t->dim[0], out);", 1, 1),
    ("/* router GEMV, top-k (+ shared gate logit), experts with the shared expert as\n", TM_STATE + "/* router GEMV, top-k (+ shared gate logit), experts with the shared expert as\n", 1, 1),
    ("    const bool profile = T > 8u && getenv(\"DS4_QWEN4_MOE_PROFILE\") != NULL;\n", "    const bool profile = T > 8u && getenv(\"DS4_QWEN4_MOE_PROFILE\") != NULL;\n    g_qwen4_tm.mid_slots = 0u;\n", 1, 1),
    ("        ok = ds4_gpu_qwen4_moe_mid_tensor(g->mid, g->mixed, g->selected, m->map, m->size, l->ffn_gate_exps->abs_offset,",
     "        g_qwen4_tm.mid_slots = DS4_N_EXPERT_USED + (shared_dense ? 0u : 1u);\n        ok = ds4_gpu_qwen4_moe_mid_tensor(g->mid, g->mixed, g->selected, m->map, m->size, l->ffn_gate_exps->abs_offset,", 1, 1),
    ("static bool qwen4_layer_moe(ds4_qwen4_gpu_graph *g, const ds4_model *m, const ds4_layer_weights *l,\n", CALIB + "static bool qwen4_layer_moe(ds4_qwen4_gpu_graph *g, const ds4_model *m, const ds4_layer_weights *l,\n", 1, 1),
    (LAYER_OLD, LAYER_NEW, 1, 1),
    (SPEC_SIGNATURE, SPEC_WRAPPER, 1, 1),
    ("    qwen4_spec_note_first_draft(s, accept);\n    if (deep) {\n        if (accept2) s->qwen4_reject2_streak = 0u;",
     "    qwen4_spec_note_first_draft(s, accept);\n    ds4_tm_cycle_drafts(deep ? 2 : 1, (accept ? 1 : 0) + ((deep && accept && accept2) ? 1 : 0));\n    if (deep) {\n        if (accept2) s->qwen4_reject2_streak = 0u;", 1, 1),
])
edit("ds4_server.c", [
    ('#include "ds4.h"\n', '#include "ds4.h"\n#include "ds4_telemetry.h"\n', 1, 1),
    ('        ds4_log(stderr, type, "%s", line);\n        free(line);\n',
     '        ds4_log(stderr, type, "%s", line);\n        ds4_tm_log_line(type == DS4_LOG_PREFILL ? "prefill" : type == DS4_LOG_GENERATION ? "generation" : "server", line);\n        free(line);\n', 1, 1),
])
edit("Makefile", [
    ("METAL_LDLIBS := $(LDLIBS) -framework Foundation -framework Metal -framework IOSurface\nCORE_OBJS = ds4.o ds4_image.o",
     "METAL_LDLIBS := $(LDLIBS) -framework Foundation -framework Metal -framework IOSurface -framework Accelerate\nCORE_OBJS = ds4_telemetry.o ds4.o ds4_image.o", 1, 1),
    ("ds4_server.o: ds4_server.c ds4.h", "ds4_telemetry.o: ds4_telemetry.c ds4_telemetry.h\n\t$(CC) $(CFLAGS) -c -o $@ ds4_telemetry.c\n\nds4_server.o: ds4_server.c ds4_telemetry.h ds4.h", 1, 1),
])
for name in ("ds4_telemetry.c", "ds4_telemetry.h"):
    shutil.copy2(HERE / "telemetry" / name, SRC / name)
print("hooks applied")
