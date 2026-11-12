/* DwarfStar runtime telemetry and calibration capture. See ds4_telemetry.h. */
#define _DARWIN_C_SOURCE
#define ACCELERATE_NEW_LAPACK
#include "ds4_telemetry.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#ifdef __APPLE__
#include <Accelerate/Accelerate.h>
#endif

/* ------------------------------------------------------------------ transport */

static struct {
    atomic_int state;            /* 0 unknown, 1 configured, -1 disabled */
    atomic_int level;
    int fd;
    struct sockaddr_in dst;
    char model[40];
    atomic_ullong ple_reads, ple_bytes;
    unsigned long long last_ple_reads, last_ple_bytes;
    unsigned long long seq;
    double cycle_t0;
    int cycle_proposed, cycle_accepted;
    pthread_mutex_t mu;
} tm = { .fd = -1, .mu = PTHREAD_MUTEX_INITIALIZER };

static double tm_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec * 1e-6;
}

static double tm_wall(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (double)tv.tv_sec + (double)tv.tv_usec * 1e-6;
}

/* Heartbeats raise the detail level for ttl_ms; silence lowers it to zero. */
static void *tm_control_thread(void *arg) {
    const int fd = (int)(intptr_t)arg;
    double until = 0.0;
    for (;;) {
        char buf[256];
        const ssize_t n = recv(fd, buf, sizeof(buf) - 1, 0);   /* 500 ms receive timeout */
        if (n > 0) {
            buf[n] = 0;
            const char *lv = strstr(buf, "\"level\"");
            const char *ttl = strstr(buf, "\"ttl_ms\"");
            int level = 0;
            double ttl_ms = 3000.0;
            if (lv && (lv = strchr(lv, ':')) != NULL) level = atoi(lv + 1);
            if (ttl && (ttl = strchr(ttl, ':')) != NULL) ttl_ms = atof(ttl + 1);
            if (level < 0) level = 0;
            if (level > 2) level = 2;
            if (ttl_ms < 100.0) ttl_ms = 100.0;
            if (ttl_ms > 60000.0) ttl_ms = 60000.0;
            until = tm_now_ms() + ttl_ms;
            atomic_store(&tm.level, level);
        } else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
            break;
        }
        if (atomic_load(&tm.level) != 0 && tm_now_ms() > until) atomic_store(&tm.level, 0);
    }
    atomic_store(&tm.level, 0);
    return NULL;
}

static void tm_init(void) {
    pthread_mutex_lock(&tm.mu);
    if (atomic_load(&tm.state) != 0) {
        pthread_mutex_unlock(&tm.mu);
        return;
    }
    const char *port = getenv("DS4_TELEMETRY_UDP");
    const char *control = getenv("DS4_TELEMETRY_CONTROL");
    int state = -1;
    if (port && atoi(port) > 0 && control && atoi(control) > 0) {
        tm.fd = socket(AF_INET, SOCK_DGRAM, 0);
        const int cfd = socket(AF_INET, SOCK_DGRAM, 0);
        if (tm.fd >= 0 && cfd >= 0) {
            memset(&tm.dst, 0, sizeof(tm.dst));
            tm.dst.sin_family = AF_INET;
            tm.dst.sin_port = htons((uint16_t)atoi(port));
            tm.dst.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            struct sockaddr_in self;
            memset(&self, 0, sizeof(self));
            self.sin_family = AF_INET;
            self.sin_port = htons((uint16_t)atoi(control));
            self.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            struct timeval tv = { 0, 500000 };
            setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
            const int one = 1;
            setsockopt(cfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
            const char *model = getenv("DS4_TELEMETRY_MODEL");
            snprintf(tm.model, sizeof(tm.model), "%s", model ? model : "");
            for (char *p = tm.model; *p; p++) {
                if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '-' || *p == '_')) *p = '_';
            }
            pthread_t thread;
            if (bind(cfd, (struct sockaddr *)&self, sizeof(self)) == 0 &&
                pthread_create(&thread, NULL, tm_control_thread, (void *)(intptr_t)cfd) == 0) {
                pthread_detach(thread);
                state = 1;
                fprintf(stderr, "ds4: telemetry enabled (events to udp/%d, control on udp/%d)\n",
                        atoi(port), atoi(control));
            } else {
                close(cfd);
            }
        }
    }
    atomic_store(&tm.state, state);
    pthread_mutex_unlock(&tm.mu);
}

bool ds4_tm_configured(void) {
    int state = atomic_load(&tm.state);
    if (state == 0) {
        tm_init();
        state = atomic_load(&tm.state);
    }
    return state == 1;
}

int ds4_tm_level(void) {
    return ds4_tm_configured() ? atomic_load(&tm.level) : 0;
}

static void tm_send(const char *data, size_t len) {
    if (tm.fd < 0 || len == 0 || len > 8192) return;
    (void)sendto(tm.fd, data, len, MSG_DONTWAIT, (const struct sockaddr *)&tm.dst, sizeof(tm.dst));
}

/* Forward one of the server's own stable progress records (counts and rates only). */
void ds4_tm_log_line(const char *type, const char *line) {
    if (ds4_tm_level() < 1 || !line) return;
    if (strstr(line, "text_snippet") || strstr(line, "file=")) return;   /* never forward text or paths */
    char buf[1400];
    int n = snprintf(buf, sizeof(buf), "{\"v\":1,\"t\":%.3f,\"src\":\"native\",\"model\":\"%s\",\"kind\":\"log\",\"type\":\"%s\",\"line\":\"",
                     tm_wall(), tm.model, type ? type : "");
    if (n < 0 || (size_t)n >= sizeof(buf)) return;
    for (const char *p = line; *p && (size_t)n < sizeof(buf) - 8; p++) {
        const unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') { buf[n++] = '\\'; buf[n++] = (char)c; }
        else if (c < 0x20) { buf[n++] = ' '; }
        else buf[n++] = (char)c;
    }
    buf[n++] = '"';
    buf[n++] = '}';
    tm_send(buf, (size_t)n);
}

void ds4_tm_ple_read(uint32_t bytes) {
    if (atomic_load(&tm.state) != 1) return;
    atomic_fetch_add(&tm.ple_reads, 1);
    atomic_fetch_add(&tm.ple_bytes, bytes);
}

void ds4_tm_cycle_begin(void) {
    if (ds4_tm_level() < 1) return;
    tm.cycle_t0 = tm_now_ms();
    tm.cycle_proposed = 0;
    tm.cycle_accepted = 0;
}

void ds4_tm_cycle_drafts(int proposed, int accepted) {
    tm.cycle_proposed = proposed;
    tm.cycle_accepted = accepted;
}

void ds4_tm_cycle_end(uint32_t pos, int committed, const int32_t *selected, const float *weights,
                      uint32_t n_layer, uint32_t n_used, const uint64_t *layer_mask) {
    const int level = ds4_tm_level();
    if (level < 1 || committed < 0) return;
    const unsigned long long reads = atomic_load(&tm.ple_reads), bytes = atomic_load(&tm.ple_bytes);
    char buf[8192];
    int n = snprintf(buf, sizeof(buf),
                     "{\"v\":1,\"t\":%.3f,\"src\":\"native\",\"model\":\"%s\",\"kind\":\"tick\",\"seq\":%llu,"
                     "\"pos\":%u,\"n\":%d,\"dp\":%d,\"da\":%d,\"ms\":%.2f,\"ple\":[%llu,%llu]",
                     tm_wall(), tm.model, tm.seq++, pos, committed, tm.cycle_proposed, tm.cycle_accepted,
                     tm.cycle_t0 > 0.0 ? tm_now_ms() - tm.cycle_t0 : 0.0,
                     reads - tm.last_ple_reads, bytes - tm.last_ple_bytes);
    tm.last_ple_reads = reads;
    tm.last_ple_bytes = bytes;
    if (level >= 2 && selected && weights && layer_mask && n_used <= DS4_TM_MAX_USED && n_layer <= DS4_TM_MAX_LAYER) {
        /* Row 0 of every captured layer: expert ids, then weights as integers 0..255. */
        const size_t stride = (size_t)DS4_TM_ROWS * DS4_TM_MAX_USED;
        int m = n + snprintf(buf + n, sizeof(buf) - (size_t)n, ",\"k\":%u,\"r\":[", n_used);
        bool fits = true;
        for (uint32_t il = 0; il < n_layer && fits; il++) {
            const bool have = (layer_mask[il / 64u] >> (il % 64u)) & 1u;
            if ((size_t)m + 8u + (have ? n_used * 9u : 0u) >= sizeof(buf)) { fits = false; break; }
            buf[m++] = '[';
            if (have) {
                const int32_t *ids = selected + il * stride;
                const float *wt = weights + il * stride;
                for (uint32_t i = 0; i < n_used; i++) {
                    int w = (int)(wt[i] * 255.0f + 0.5f);
                    if (w < 0) w = 0;
                    if (w > 255) w = 255;
                    m += snprintf(buf + m, sizeof(buf) - (size_t)m, "%s%d,%d", i ? "," : "", (int)ids[i], w);
                }
            }
            buf[m++] = ']';
            if (il + 1 < n_layer) buf[m++] = ',';
        }
        if (fits && (size_t)m + 4u < sizeof(buf)) {
            buf[m++] = ']';
            n = m;
        }
    }
    if ((size_t)n + 2u >= sizeof(buf)) return;
    buf[n++] = '}';
    tm_send(buf, (size_t)n);
}

/* ---------------------------------------------------------------- calibration */

#define CALIB_X_SAMPLES 1536u
#define CALIB_MID_SAMPLES 6144u

typedef struct {
    double *h_in;        /* [E][E], upper triangle */
    double *h_mid;       /* [FF][FF], upper triangle */
    double *mid_diag;    /* [n_expert][FF] */
    uint64_t *expert_count;
    float *x_samples;    /* [CALIB_X_SAMPLES][E] */
    float *mid_samples;  /* [CALIB_MID_SAMPLES][FF] */
    int32_t *mid_expert; /* [CALIB_MID_SAMPLES] */
    uint64_t n_x, n_mid, seen_x, seen_mid;
} calib_layer;

static struct {
    int state;           /* 0 unknown, 1 on, -1 off */
    char dir[900];
    calib_layer *layer;
    uint32_t n_layer, n_embd, n_ff, n_expert;
    double *scratch;
    size_t scratch_cap;
    uint64_t rng;
} cal;

static uint64_t calib_rand(void) {
    cal.rng = cal.rng * 6364136223846793005ULL + 1442695040888963407ULL;
    return cal.rng >> 17;
}

static void calib_write(void) {
    if (cal.state != 1 || !cal.layer) return;
    for (uint32_t il = 0; il < cal.n_layer; il++) {
        calib_layer *c = &cal.layer[il];
        if (!c->h_in) continue;
        char path[1024];
        snprintf(path, sizeof(path), "%s/calib-layer-%02u.bin", cal.dir, il);
        FILE *fp = fopen(path, "wb");
        if (!fp) continue;
        const uint64_t header[12] = { 0x3142494c41433444ULL /* "D4CALIB1" */, il, cal.n_embd, cal.n_ff, cal.n_expert,
                                      c->seen_x, c->seen_mid, c->n_x, c->n_mid, CALIB_X_SAMPLES, CALIB_MID_SAMPLES, 0 };
        fwrite(header, sizeof(header), 1, fp);
        fwrite(c->h_in, sizeof(double), (size_t)cal.n_embd * cal.n_embd, fp);
        fwrite(c->h_mid, sizeof(double), (size_t)cal.n_ff * cal.n_ff, fp);
        fwrite(c->mid_diag, sizeof(double), (size_t)cal.n_expert * cal.n_ff, fp);
        fwrite(c->expert_count, sizeof(uint64_t), cal.n_expert, fp);
        fwrite(c->x_samples, sizeof(float), (size_t)c->n_x * cal.n_embd, fp);
        fwrite(c->mid_samples, sizeof(float), (size_t)c->n_mid * cal.n_ff, fp);
        fwrite(c->mid_expert, sizeof(int32_t), (size_t)c->n_mid, fp);
        fclose(fp);
    }
    fprintf(stderr, "ds4: calibration statistics written to %s\n", cal.dir);
}

bool ds4_calib_enabled(void) {
    if (cal.state == 0) {
        const char *dir = getenv("DS4_QWEN4_CALIB_DIR");
        if (dir && dir[0] && strlen(dir) < sizeof(cal.dir)) {
            snprintf(cal.dir, sizeof(cal.dir), "%s", dir);
            mkdir(cal.dir, 0700);
            cal.state = 1;
            cal.rng = 0x9E3779B97F4A7C15ULL;
            atexit(calib_write);
            fprintf(stderr, "ds4: calibration capture enabled -> %s (use a small --prefill-chunk)\n", cal.dir);
        } else {
            cal.state = -1;
        }
    }
    return cal.state == 1;
}

static bool calib_layer_init(calib_layer *c) {
    c->h_in = calloc((size_t)cal.n_embd * cal.n_embd, sizeof(double));
    c->h_mid = calloc((size_t)cal.n_ff * cal.n_ff, sizeof(double));
    c->mid_diag = calloc((size_t)cal.n_expert * cal.n_ff, sizeof(double));
    c->expert_count = calloc(cal.n_expert, sizeof(uint64_t));
    c->x_samples = malloc((size_t)CALIB_X_SAMPLES * cal.n_embd * sizeof(float));
    c->mid_samples = malloc((size_t)CALIB_MID_SAMPLES * cal.n_ff * sizeof(float));
    c->mid_expert = malloc((size_t)CALIB_MID_SAMPLES * sizeof(int32_t));
    return c->h_in && c->h_mid && c->mid_diag && c->expert_count && c->x_samples && c->mid_samples && c->mid_expert;
}

static void calib_syrk(double *h, const float *rows, size_t n_rows, size_t dim) {
    const size_t need = n_rows * dim;
    if (need > cal.scratch_cap) {
        free(cal.scratch);
        cal.scratch = malloc(need * sizeof(double));
        cal.scratch_cap = cal.scratch ? need : 0;
    }
    if (!cal.scratch) return;
#ifdef __APPLE__
    vDSP_vspdp(rows, 1, cal.scratch, 1, need);
    cblas_dsyrk(CblasRowMajor, CblasUpper, CblasTrans, (int)dim, (int)n_rows, 1.0, cal.scratch, (int)dim, 1.0, h, (int)dim);
#else
    for (size_t r = 0; r < n_rows; r++) {
        const float *v = rows + r * dim;
        for (size_t i = 0; i < dim; i++) {
            const double vi = v[i];
            double *hi = h + i * dim;
            for (size_t j = i; j < dim; j++) hi[j] += vi * (double)v[j];
        }
    }
#endif
}

void ds4_calib_moe(uint32_t layer, uint32_t n_layer, const float *x, uint32_t n_tokens, uint32_t n_embd,
                   const float *mid, uint32_t mid_slots, uint32_t n_used, uint32_t n_ff,
                   const int32_t *selected, uint32_t n_expert) {
    if (cal.state != 1 || !x || layer >= n_layer) return;
    if (!cal.layer) {
        cal.layer = calloc(n_layer, sizeof(calib_layer));
        cal.n_layer = n_layer;
        cal.n_embd = n_embd;
        cal.n_ff = n_ff;
        cal.n_expert = n_expert;
        if (!cal.layer) { cal.state = -1; return; }
    }
    calib_layer *c = &cal.layer[layer];
    if (!c->h_in && !calib_layer_init(c)) { cal.state = -1; return; }
    calib_syrk(c->h_in, x, n_tokens, n_embd);
    for (uint32_t t = 0; t < n_tokens; t++) {
        c->seen_x++;
        uint64_t slot = c->n_x;
        if (c->n_x < CALIB_X_SAMPLES) c->n_x++;
        else slot = calib_rand() % c->seen_x;
        if (slot < CALIB_X_SAMPLES) memcpy(c->x_samples + slot * n_embd, x + (size_t)t * n_embd, n_embd * sizeof(float));
    }
    if (!mid || !selected || mid_slots < n_used) return;
    for (uint32_t t = 0; t < n_tokens; t++) {
        const float *rows = mid + (size_t)t * mid_slots * n_ff;
        calib_syrk(c->h_mid, rows, n_used, n_ff);
        for (uint32_t s = 0; s < n_used; s++) {
            const int32_t e = selected[(size_t)t * n_used + s];
            if (e < 0 || (uint32_t)e >= n_expert) continue;
            const float *v = rows + (size_t)s * n_ff;
            double *d = c->mid_diag + (size_t)e * n_ff;
            for (uint32_t i = 0; i < n_ff; i++) d[i] += (double)v[i] * (double)v[i];
            c->expert_count[e]++;
            c->seen_mid++;
            uint64_t slot = c->n_mid;
            if (c->n_mid < CALIB_MID_SAMPLES) c->n_mid++;
            else slot = calib_rand() % c->seen_mid;
            if (slot < CALIB_MID_SAMPLES) {
                memcpy(c->mid_samples + slot * n_ff, v, n_ff * sizeof(float));
                c->mid_expert[slot] = e;
            }
        }
    }
}
