/*
 * M4 board application: runs scenes through the HPDP in CAPTURE_CMP mode.
 *
 * The host (sw/host/run_m4.py) loads a calibration table, raw scenes and a job
 * block (hpdp_job.h) into PS DDR over JTAG. For each new job this program
 * configures the core, DMAs the table into the radiometric correction, checks
 * it through the table debug registers, streams the raw scenes into the sensor
 * emulator, captures each compressed scene with DMA S2MM, and writes a result
 * block with the per-scene sizes, counters and timing. The host then reads the
 * compressed scenes back and checks them against the model.
 *
 * With no job present after a few seconds, it runs a self-test instead: two
 * identical LFSR-pattern scenes must compress to identical bitstreams.
 *
 * UART: PS UART0, 115200 8N1. Lines starting "PASS:" / "FAIL:" summarize.
 */
#include <stddef.h>
#include <string.h>
#include "xparameters.h"
#include "xaxidma.h"
#include "xil_cache.h"
#include "xil_io.h"
#include "xil_printf.h"
#ifdef SDT
#include "xiltimer.h"
#else
#include "xtime_l.h"
#endif
#include "hpdp_regs.h"
#include "hpdp_job.h"

#define HPDP_BASE       0xA0000000u                 /* SPEC.md 8.1 */
#define REG(field)      (HPDP_BASE + offsetof(hpdp_regs_t, field))
#define DMA_CHUNK       (32u << 20)                 /* MM2S transfer size (length register: 26 bits) */
#define S2MM_MAX        ((1u << 26) - 64u)

static XAxiDma dma;
static volatile job_t *const job = (job_t *)JOB_ADDR;
static volatile result_t *const res = (result_t *)RESULT_ADDR;

#define MM2S_REG(off)   XAxiDma_ReadReg(dma.RegBase + XAXIDMA_TX_OFFSET, (off))
#define S2MM_REG(off)   XAxiDma_ReadReg(dma.RegBase + XAXIDMA_RX_OFFSET, (off))

static inline u32 rd(UINTPTR a) { return Xil_In32(a); }
static inline void wr(UINTPTR a, u32 v) { Xil_Out32(a, v); }

static u64 now(void)
{
    XTime t;
    XTime_GetTime(&t);
    return (u64)t;
}

static u64 us_to_ticks(u64 us) { return (u64)COUNTS_PER_SECOND * us / 1000000u; }

static void delay_us(u32 us)
{
    u64 end = now() + us_to_ticks(us);
    while (now() < end)
        ;
}

/* Wait until (reg & mask) == value; returns 0 on success */
static int poll(UINTPTR reg, u32 mask, u32 value, u32 timeout_us)
{
    u64 end = now() + us_to_ticks(timeout_us);
    while ((rd(reg) & mask) != value)
        if (now() > end)
            return -1;
    return 0;
}

/* All register writes so far have reached the data path clocks */
static int sync_cfg(void)
{
    return poll(REG(sys.STATUS), HPDP_SYS__STATUS__CFG_PENDING_bm, 0, 1000);
}

static int init_dma(void)
{
    XAxiDma_Config *cfg;
#ifndef SDT
    cfg = XAxiDma_LookupConfig(XPAR_AXIDMA_0_DEVICE_ID);
#else
    cfg = XAxiDma_LookupConfig(XPAR_XAXIDMA_0_BASEADDR);
#endif
    if (!cfg || XAxiDma_CfgInitialize(&dma, cfg) != XST_SUCCESS || XAxiDma_HasSg(&dma)) {
        xil_printf("DMA initialization failed\r\n");
        return -1;
    }
    XAxiDma_IntrDisable(&dma, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DEVICE_TO_DMA);
    XAxiDma_IntrDisable(&dma, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DMA_TO_DEVICE);
    return 0;
}

static int dma_error(void)
{
    return (MM2S_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK) ||
           (S2MM_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK);
}

/* Calibration table entry through the debug registers: {bad, gain, dark} */
static int table_read(u32 addr, u32 *lo, u32 *hi)
{
    wr(REG(rc.TBL_ADDR), addr);
    wr(REG(rc.TBL_CMD), HPDP_RC__TBL_CMD__READ_bm);
    if (poll(REG(rc.STATUS), HPDP_RC__STATUS__TBL_BUSY_bm, 0, 1000))
        return -1;
    *lo = rd(REG(rc.TBL_RDATA_LO));
    *hi = rd(REG(rc.TBL_RDATA_HI));
    return 0;
}

/* ---------------------------------------------------------------------- */

typedef struct {
    u32 nx, ny, nz, depth, scenes, line_period, source, rc_enable;
    u32 pred, wupd, coder, opts;
    u32 cal_addr, cal_bytes, raw_addr, raw_bytes, out_addr, out_capacity;
    int load_table;
} run_t;

static void configure(const run_t *r)
{
    /* soft reset: clears the data path (and the emulator's prefetch buffer), keeps registers */
    wr(REG(sys.CTRL), HPDP_SYS__CTRL__SOFT_RESET_bm);
    delay_us(10);
    wr(REG(sys.CTRL), 0);

    wr(REG(sys.NX), r->nx);
    wr(REG(sys.NY), r->ny);
    wr(REG(sys.NZ), r->nz);
    wr(REG(sys.DEPTH), r->depth);
    wr(REG(sys.MODE), HPDP_SYS__MODE__CAPTURE_bm);
    wr(REG(sys.IRQ_EN), 0);                         /* polled */
    wr(REG(sys.IRQ_STATUS), 0xFFFFFFFFu);
    wr(REG(se.CTRL), 0);
    wr(REG(se.LINE_PERIOD), r->line_period);
    wr(REG(se.LINES), r->ny * r->scenes);
    wr(REG(se.SOURCE), r->source);
    wr(REG(rc.CTRL), r->rc_enable ? HPDP_RC__CTRL__ENABLE_bm : 0);
    wr(REG(cmp.PRED), r->pred);
    wr(REG(cmp.WUPD), r->wupd);
    wr(REG(cmp.CODER), r->coder);
    wr(REG(cmp.OPTS), r->opts);
    wr(REG(se.CNT_CLEAR), 1);
    wr(REG(ct.CNT_CLEAR), 1);
    wr(REG(cmp.CNT_CLEAR), 1);
}

static int load_table(const run_t *r, u32 *mismatch)
{
    u32 entries = r->nx * r->nz, words = (entries + 1) / 2, probe[3], lo, hi;
    const u8 *cal = (const u8 *)(UINTPTR)r->cal_addr;

    wr(REG(sys.DMA_ROUTE), HPDP_SYS__DMA_ROUTE__TO_TABLE_bm);
    if (sync_cfg())
        return -1;
    Xil_DCacheInvalidateRange((UINTPTR)r->cal_addr, r->cal_bytes);
    if (XAxiDma_SimpleTransfer(&dma, (UINTPTR)r->cal_addr, r->cal_bytes, XAXIDMA_DMA_TO_DEVICE) != XST_SUCCESS)
        return -1;
    if (poll(dma.RegBase + XAXIDMA_TX_OFFSET + XAXIDMA_SR_OFFSET, XAXIDMA_IDLE_MASK, XAXIDMA_IDLE_MASK, 100000) ||
        dma_error())
        return -1;
    if (poll(REG(rc.TBL_WORDS), 0xFFFFFFFFu, words, 1000)) {
        xil_printf("  table: %d words loaded, expected %d\r\n", (int)rd(REG(rc.TBL_WORDS)), (int)words);
        return -1;
    }
    /* read back the first, middle and last entries */
    probe[0] = 0;
    probe[1] = entries / 2;
    probe[2] = entries - 1;
    *mismatch = 0;
    for (int k = 0; k < 3; k++) {
        const u8 *rec = cal + 8u * probe[k];
        u32 exp_lo = rec[0] | (rec[1] << 8) | (rec[2] << 16) | ((u32)rec[3] << 24);
        if (table_read(probe[k], &lo, &hi))
            return -1;
        if (lo != exp_lo || (hi & 1u) != (rec[4] & 1u))
            (*mismatch)++;
    }
    return 0;
}

/*
 * One acquisition: stream the raw data (DMA source), run the sensor for all
 * lines, capture each scene. Fills scene offsets/sizes; returns a status.
 */
static int acquire(const run_t *r, u32 *offset, u32 *bytes, u64 *ticks)
{
    u32 mm2s_done = 0, scene = 0, out_off = 0, armed_len = 0;
    int mm2s_busy = 0, s2mm_busy = 0;
    u64 t0 = now(), deadline = ~0ull, run_us;

    run_us = (u64)r->scenes * r->ny * r->line_period / 100u;    /* clk_sensor = 100 MHz */
    wr(REG(sys.DMA_ROUTE), 0);
    if (sync_cfg())
        return ST_NO_HW;
    if (rd(REG(cmp.STATUS)) & HPDP_CMP__STATUS__CFG_ERROR_bm)
        return ST_CFG_ERROR;

    Xil_DCacheInvalidateRange((UINTPTR)r->out_addr, r->out_capacity);
    if (r->source == 0)
        Xil_DCacheInvalidateRange((UINTPTR)r->raw_addr, r->raw_bytes);

    for (;;) {
        /* S2MM: one transfer per scene, ended by tlast */
        if (s2mm_busy && !XAxiDma_Busy(&dma, XAXIDMA_DEVICE_TO_DMA)) {
            s2mm_busy = 0;
            bytes[scene] = S2MM_REG(XAXIDMA_BUFFLEN_OFFSET);
            offset[scene] = out_off;
            if (bytes[scene] >= armed_len)
                return ST_OVERFLOW;                 /* filled the buffer: no tlast seen */
            out_off = (out_off + bytes[scene] + 63u) & ~63u;
            *ticks = now() - t0;
            if (++scene == r->scenes)
                break;
        }
        if (!s2mm_busy && scene < r->scenes) {
            if (out_off + 64u > r->out_capacity)
                return ST_OVERFLOW;
            armed_len = r->out_capacity - out_off;
            if (armed_len > S2MM_MAX)
                armed_len = S2MM_MAX;
            if (XAxiDma_SimpleTransfer(&dma, (UINTPTR)(r->out_addr + out_off), armed_len,
                                       XAXIDMA_DEVICE_TO_DMA) != XST_SUCCESS)
                return ST_DMA;
            s2mm_busy = 1;
        }
        /* MM2S: raw data in chunks */
        if (r->source == 0) {
            if (mm2s_busy && !XAxiDma_Busy(&dma, XAXIDMA_DMA_TO_DEVICE))
                mm2s_busy = 0;
            if (!mm2s_busy && mm2s_done < r->raw_bytes) {
                u32 len = r->raw_bytes - mm2s_done;
                if (len > DMA_CHUNK)
                    len = DMA_CHUNK;
                if (XAxiDma_SimpleTransfer(&dma, (UINTPTR)(r->raw_addr + mm2s_done), len,
                                           XAXIDMA_DMA_TO_DEVICE) != XST_SUCCESS)
                    return ST_DMA;
                mm2s_done += len;
                mm2s_busy = 1;
            }
        }
        /* start the sensor once the emulator's buffer has had time to fill */
        if (!(rd(REG(se.CTRL)) & HPDP_SE__CTRL__RUN_bm)) {
            delay_us(20);
            wr(REG(se.CTRL), HPDP_SE__CTRL__RUN_bm);
            t0 = now();
            deadline = t0 + us_to_ticks(2 * run_us + 2000000u);
        }
        if (dma_error())
            return ST_DMA;
        if (now() > deadline)
            return ST_TIMEOUT;
    }
    wr(REG(se.CTRL), 0);
    return ST_OK;
}

static void fill_counters(volatile result_t *o)
{
    o->build_id        = rd(REG(sys.BUILD_ID));
    o->scenes_done     = rd(REG(cmp.SCENES_DONE));
    o->se_lines        = rd(REG(se.LINES_EMITTED));
    o->se_underrun     = rd(REG(se.UNDERRUN_SAMPLES));
    o->se_overruns     = rd(REG(se.PERIOD_OVERRUNS));
    o->se_dropped      = rd(REG(se.DROPPED_SAMPLES));
    o->rc_clamped      = rd(REG(rc.SCENE_CLAMPED));
    o->rc_replaced     = rd(REG(rc.SCENE_REPLACED));
    o->tbl_words       = rd(REG(rc.TBL_WORDS));
    o->ct_lines        = rd(REG(ct.LINES_OUT));
    o->ct_dropped      = rd(REG(ct.DROPPED_LINES));
    o->ct_unfilled     = rd(REG(ct.UNFILLED_LINES));
    o->cmp_samples     = rd(REG(cmp.SAMPLES_IN));
    o->cmp_bytes       = rd(REG(cmp.BYTES_OUT));
    o->cmp_last_cycles = rd(REG(cmp.LAST_CYCLES));
    o->cmp_stalls      = rd(REG(cmp.OUT_STALLS));
}

static void print_summary(const run_t *r, volatile result_t *o, u64 ticks)
{
    u64 samples = (u64)r->nx * r->ny * r->nz * r->scenes;
    u64 us = ticks * 1000000u / COUNTS_PER_SECOND;
    u32 total = 0;

    for (u32 s = 0; s < o->scenes_done && s < r->scenes; s++) {
        total += o->scene_bytes[s];
        xil_printf("  scene %d: %d bytes, %d.%02d bits/sample\r\n", (int)s, (int)o->scene_bytes[s],
                   (int)(800u * o->scene_bytes[s] / (r->nx * r->ny * r->nz) / 100u),
                   (int)(800u * o->scene_bytes[s] / (r->nx * r->ny * r->nz) % 100u));
    }
    xil_printf("  %d samples in %d us: %d Msamples/s; ratio %d.%02d:1\r\n", (int)samples, (int)us,
               us ? (int)(samples / us) : 0,
               total ? (int)(samples * r->depth / (8u * total)) : 0,
               total ? (int)(samples * r->depth * 100u / (8u * total) % 100u) : 0);
    xil_printf("  emulator: %d lines, %d underruns, %d overruns, %d dropped; corner turn: %d lines, %d dropped\r\n",
               (int)o->se_lines, (int)o->se_underrun, (int)o->se_overruns, (int)o->se_dropped,
               (int)o->ct_lines, (int)o->ct_dropped);
    xil_printf("  correction: %d clamped, %d replaced (last scene); compressor: %d cycles last scene, %d output stalls\r\n",
               (int)o->rc_clamped, (int)o->rc_replaced, (int)o->cmp_last_cycles, (int)o->cmp_stalls);
}

static int run_job(void)
{
    run_t r;
    u32 offset[MAX_SCENES], bytes[MAX_SCENES], mismatch = 0;
    u64 ticks = 0;
    int st = ST_OK;

    r.nx = job->nx;  r.ny = job->ny;  r.nz = job->nz;  r.depth = job->depth;
    r.scenes = job->scenes;  r.line_period = job->line_period;
    r.source = job->source;  r.rc_enable = job->rc_enable;
    r.pred = job->cmp_pred;  r.wupd = job->cmp_wupd;  r.coder = job->cmp_coder;  r.opts = job->cmp_opts;
    r.cal_addr = job->cal_addr;  r.cal_bytes = job->cal_bytes;
    r.raw_addr = job->raw_addr;  r.raw_bytes = job->raw_bytes;
    r.out_addr = job->out_addr;  r.out_capacity = job->out_capacity;
    r.load_table = (job->flags & JOB_LOAD_TABLE) != 0;

    memset((void *)res, 0, sizeof(result_t));
    res->version = JOB_VERSION;
    res->seq = job->seq;
    res->timer_hz = COUNTS_PER_SECOND;

    xil_printf("\r\njob %d: %dx%dx%d D=%d, %d scenes, line period %d, source %d, correction %s\r\n",
               (int)job->seq, (int)r.nx, (int)r.ny, (int)r.nz, (int)r.depth, (int)r.scenes,
               (int)r.line_period, (int)r.source, r.rc_enable ? "on" : "off");

    if (job->version != JOB_VERSION || r.scenes == 0 || r.scenes > MAX_SCENES || r.nx == 0 ||
        r.nx > 512 || r.nz < 16 || r.nz > 256 || r.ny == 0 || r.depth < 2 || r.depth > 16 ||
        r.line_period < r.nx * r.nz + 1 ||
        (r.source == 0 && (r.raw_bytes % 16 || r.raw_bytes < 2u * r.nx * r.ny * r.nz * r.scenes)) ||
        (r.load_table && r.cal_bytes != 8u * r.nx * r.nz + (r.nx * r.nz % 2 ? 8u : 0u))) {
        st = ST_BAD_JOB;
    }
    if (st == ST_OK) {
        configure(&r);
        if (sync_cfg())
            st = ST_NO_HW;
    }
    if (st == ST_OK && r.load_table && load_table(&r, &mismatch))
        st = ST_TABLE;
    if (st == ST_OK && mismatch)
        st = ST_TABLE;
    if (st == ST_OK)
        st = acquire(&r, offset, bytes, &ticks);
    wr(REG(se.CTRL), 0);

    fill_counters(res);
    res->tbl_mismatch = mismatch;
    res->ticks_lo = (u32)ticks;
    res->ticks_hi = (u32)(ticks >> 32);
    for (u32 s = 0; s < r.scenes && s < MAX_SCENES; s++) {
        res->scene_offset[s] = offset[s];
        res->scene_bytes[s] = bytes[s];
    }
    res->status = st;
    if (st == ST_OK)
        print_summary(&r, res, ticks);
    /* make the output and result visible to the debugger, magic last */
    Xil_DCacheInvalidateRange((UINTPTR)r.out_addr, r.out_capacity);
    Xil_DCacheFlushRange((UINTPTR)res, sizeof(result_t));
    res->magic = RESULT_MAGIC;
    Xil_DCacheFlushRange((UINTPTR)res, sizeof(result_t));
    xil_printf("%s: hpdp_run job %d, status %d, %d scenes\r\n", st == ST_OK ? "DONE" : "FAIL",
               (int)job->seq, st, (int)res->scenes_done);
    return st;
}

/* No host: two identical LFSR scenes must give identical bitstreams */
static int self_test(void)
{
    run_t r = {
        .nx = 64, .ny = 16, .nz = 32, .depth = 12, .scenes = 2, .line_period = 64 * 32 + 101,
        .source = 2, .rc_enable = 0,
        .pred  = 3 | (1u << 4) | (13u << 8) | (32u << 16),         /* P=3 full, omega 13, R 32 */
        .wupd  = 0x1Fu | (3u << 8) | (6u << 16),                   /* nu -1..3, t_inc 2^6 */
        .coder = 18 | (1u << 8) | (6u << 12) | (7u << 16) | (1u << 24),
        .opts  = 0,
        .out_addr = OUT_ADDR, .out_capacity = 4u << 20,
        .load_table = 0,
    };
    u32 offset[MAX_SCENES], bytes[MAX_SCENES];
    u64 ticks = 0;
    int st;

    xil_printf("\r\nno job at 0x%08x: self-test, 2 LFSR scenes %dx%dx%d\r\n", JOB_ADDR, (int)r.nx,
               (int)r.ny, (int)r.nz);
    configure(&r);
    if (sync_cfg()) {
        xil_printf("FAIL: hpdp_run self-test: no response from the HPDP registers\r\n");
        return -1;
    }
    st = acquire(&r, offset, bytes, &ticks);
    fill_counters(res);
    if (st == ST_OK) {
        const u8 *out = (const u8 *)(UINTPTR)r.out_addr;
        Xil_DCacheInvalidateRange((UINTPTR)r.out_addr, r.out_capacity);
        res->scene_bytes[0] = bytes[0];
        res->scene_bytes[1] = bytes[1];
        res->scenes_done = 2;
        print_summary(&r, res, ticks);
        /* header: user data byte, then Nx in 16 bits */
        if (bytes[0] != bytes[1] || memcmp(out + offset[0], out + offset[1], bytes[0]) != 0 ||
            bytes[0] < 20 || out[1] != (r.nx >> 8) || out[2] != (r.nx & 0xFF))
            st = -1;
        Xil_DCacheInvalidateRange((UINTPTR)r.out_addr, r.out_capacity);
    }
    xil_printf("%s: hpdp_run self-test, status %d, scenes %d and %d bytes\r\n",
               st == ST_OK ? "PASS" : "FAIL", st, (int)bytes[0], (int)bytes[1]);
    return st;
}

int main(void)
{
    u32 id, last_seq = 0;
    int have_job = 0;
    u64 start;

    xil_printf("\r\nHPDP M4: hpdp_run\r\n");
    id = rd(REG(sys.ID));
    xil_printf("SYS.ID 0x%08x, build 0x%08x\r\n", (unsigned)id, (unsigned)rd(REG(sys.BUILD_ID)));
    if ((id >> 16) != 0x4850 || init_dma()) {
        xil_printf("FAIL: hpdp_run: HPDP not found\r\n");
        return 1;
    }
    if (poll(REG(sys.STATUS), HPDP_SYS__STATUS__SENSOR_LOCKED_bm, HPDP_SYS__STATUS__SENSOR_LOCKED_bm, 100000))
        xil_printf("warning: clk_sensor MMCM not locked\r\n");

    start = now();
    for (;;) {
        Xil_DCacheInvalidateRange(JOB_ADDR, sizeof(job_t));
        if (job->magic == JOB_MAGIC && (!have_job || job->seq != last_seq)) {
            have_job = 1;
            last_seq = job->seq;
            run_job();
        } else if (!have_job && now() - start > us_to_ticks(3000000u)) {
            self_test();
            have_job = 1;                           /* self-test once, then wait for jobs */
            last_seq = 0xFFFFFFFFu;
        }
        delay_us(1000);
    }
}
