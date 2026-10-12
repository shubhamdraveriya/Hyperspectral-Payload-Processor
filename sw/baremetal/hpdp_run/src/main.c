/*
 * HPDP board application (M4, M5): runs scenes through the HPDP.
 *
 * The host (sw/host/run_m4.py, run_m5.py) loads a calibration table, raw scenes
 * and a job block (hpdp_job.h) into PS DDR over JTAG. For each new job this
 * program configures the core, DMAs the table into the radiometric correction,
 * checks it through the table debug registers, streams the raw scenes into the
 * sensor emulator and, depending on the job's mode:
 *
 *   CAPTURE   captures each compressed scene with DMA S2MM (CAPTURE_CMP)
 *   DOWNLINK  brings the link up (SW-03), sends the scenes down the downlink
 *             chain (packets, AOS frames, RS, CADUs, error injector, GTH link)
 *             and captures the ground receiver's records with the ground DMA
 *   PRBS      runs PRBS-31 over the link and counts errors (VER-06)
 *
 * and writes a result block with sizes, counters and timing. The host then
 * reads the output back and checks it against the model.
 *
 * With no job present after a few seconds, it runs a self-test instead: two
 * identical LFSR-pattern scenes must compress to identical bitstreams.
 *
 * UART: PS UART0, 115200 8N1. Lines starting "PASS:" / "FAIL:" / "DONE:" summarize.
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
#include "net.h"

#define HPDP_BASE       0xA0000000u                 /* SPEC.md 8.1 */
#define DMA_BASE        0xA0100000u                 /* MM2S + compressor capture S2MM */
#define DMA_GR_BASE     0xA0110000u                 /* ground capture S2MM */
#define REG(field)      (HPDP_BASE + offsetof(hpdp_regs_t, field))
#define DMA_CHUNK       (32u << 20)                 /* MM2S transfer size (length register: 26 bits) */
#define S2MM_MAX        ((1u << 26) - 64u)
#define RECORD_BYTES    1024u
#define PRBS_31         5u

static XAxiDma dma, dma_gr;
static volatile job_t *const job = (job_t *)JOB_ADDR;
static volatile result_t *const res = (result_t *)RESULT_ADDR;
static volatile u32 gr_avail;                       /* ground record bytes in DDR (upload stream) */

#define MM2S_REG(off)   XAxiDma_ReadReg(dma.RegBase + XAXIDMA_TX_OFFSET, (off))
#define S2MM_REG(off)   XAxiDma_ReadReg(dma.RegBase + XAXIDMA_RX_OFFSET, (off))
#define GR_REG(off)     XAxiDma_ReadReg(dma_gr.RegBase + XAXIDMA_RX_OFFSET, (off))

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

/* The AXI DMA at base address `base`, simple mode */
static int init_one_dma(XAxiDma *d, UINTPTR base)
{
    XAxiDma_Config *cfg = NULL;
#ifdef SDT
    cfg = XAxiDma_LookupConfig(base);
#else
    for (u32 id = 0; id < 2 && !(cfg && cfg->BaseAddr == base); id++)
        cfg = XAxiDma_LookupConfig(id);
    if (cfg && cfg->BaseAddr != base)
        cfg = NULL;
#endif
    if (!cfg || XAxiDma_CfgInitialize(d, cfg) != XST_SUCCESS || XAxiDma_HasSg(d)) {
        xil_printf("DMA at 0x%08x: initialization failed\r\n", (unsigned)base);
        return -1;
    }
    XAxiDma_IntrDisable(d, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DEVICE_TO_DMA);
    if (d->HasMm2S)
        XAxiDma_IntrDisable(d, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DMA_TO_DEVICE);
    return 0;
}

static int init_dma(void)
{
    return init_one_dma(&dma, DMA_BASE) || init_one_dma(&dma_gr, DMA_GR_BASE);
}

/* Cancel an armed ground transfer (the next job re-arms) */
static void reset_dma_gr(void)
{
    XAxiDma_Reset(&dma_gr);
    for (int k = 0; k < 1000 && !XAxiDma_ResetIsDone(&dma_gr); k++)
        delay_us(10);
    XAxiDma_IntrDisable(&dma_gr, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DEVICE_TO_DMA);
}

static int dma_error(void)
{
    return (MM2S_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK) ||
           (S2MM_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK) ||
           (GR_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK);
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
    u32 mode, idle_frames;
    u32 pkt_apid, pkt_data, aos_frame_hdr, aos_frame_gap;
    u32 ei_ctrl, ei_threshold, ei_seed, ei_burst_len, ei_period, ei_asm_bits;
    u32 lnk_ctrl, gr_sync, gr_xfer, gr_addr, gr_capacity, link_timeout_ms, prbs_seconds;
    u32 net_ctrl, net_ip, net_mask, net_port;
    u32 repeat, total;                  /* raw buffer passes; scenes in all (scenes * repeat) */
    int gr_capture;
} run_t;

/* LNK.CTRL without PRBS and without the reset requests */
static u32 link_ctrl(const run_t *r)
{
    return r->lnk_ctrl & ~(HPDP_LNK__CTRL__PRBS_SEL_bm | HPDP_LNK__CTRL__GT_RESET_bm |
                           HPDP_LNK__CTRL__RX_RESET_bm);
}

/* Transceiver TX up (after configuration the power-on reset has run already;
 * if not, run the reset sequence again) */
static int gt_tx_up(u32 ctrl)
{
    if (rd(REG(lnk.STATUS)) & HPDP_LNK__STATUS__TX_READY_bm)
        return 0;
    wr(REG(lnk.CTRL), ctrl | HPDP_LNK__CTRL__GT_RESET_bm);
    delay_us(10);
    wr(REG(lnk.CTRL), ctrl);
    return poll(REG(lnk.STATUS), HPDP_LNK__STATUS__TX_READY_bm, HPDP_LNK__STATUS__TX_READY_bm, 100000);
}

/*
 * Link bring-up (SW-03): transmitters on, transceivers up, then wait for the
 * link layer to lock (1024 good words after alignment). A receiver that came
 * out of reset before anything was transmitted may need its data path reset
 * again: up to 4 RX resets.
 */
static int link_up(const run_t *r, u32 *tries)
{
    u32 ctrl = link_ctrl(r);
    u32 slice = (r->link_timeout_ms ? r->link_timeout_ms : 1000u) * 200u;    /* us, 5 slices */

    *tries = 0;
    wr(REG(lnk.CTRL), ctrl);
    if (gt_tx_up(ctrl))
        return -1;
    for (int k = 0; k < 5; k++) {
        if (!poll(REG(lnk.STATUS), HPDP_LNK__STATUS__LOCKED_bm, HPDP_LNK__STATUS__LOCKED_bm, slice))
            return 0;
        if (k == 4)
            break;
        (*tries)++;
        wr(REG(lnk.CTRL), ctrl | HPDP_LNK__CTRL__RX_RESET_bm);
        delay_us(10);
        wr(REG(lnk.CTRL), ctrl);
    }
    return -1;
}

static void configure(const run_t *r)
{
    int dl = (r->mode & MODE_DOWNLINK) != 0;

    /* soft reset: clears the clk_sys / clk_sensor data paths (the emulator's
     * prefetch buffer, the downlink chain, the frame sync), keeps registers */
    wr(REG(sys.CTRL), HPDP_SYS__CTRL__SOFT_RESET_bm);
    delay_us(10);
    wr(REG(sys.CTRL), 0);

    wr(REG(sys.NX), r->nx);
    wr(REG(sys.NY), r->ny);
    wr(REG(sys.NZ), r->nz);
    wr(REG(sys.DEPTH), r->depth);
    wr(REG(sys.MODE), ((r->mode & MODE_CAPTURE) ? HPDP_SYS__MODE__CAPTURE_bm : 0) |
                      (dl ? HPDP_SYS__MODE__DOWNLINK_bm : 0));
    wr(REG(sys.IRQ_EN), 0);                         /* polled */
    wr(REG(sys.IRQ_STATUS), 0xFFFFFFFFu);
    wr(REG(se.CTRL), 0);
    wr(REG(se.LINE_PERIOD), r->line_period);
    wr(REG(se.LINES), r->ny * r->total);
    wr(REG(se.SOURCE), r->source);
    wr(REG(rc.CTRL), r->rc_enable ? HPDP_RC__CTRL__ENABLE_bm : 0);
    wr(REG(cmp.PRED), r->pred);
    wr(REG(cmp.WUPD), r->wupd);
    wr(REG(cmp.CODER), r->coder);
    wr(REG(cmp.OPTS), r->opts);
    if (dl) {
        wr(REG(pkt.APID), r->pkt_apid);
        wr(REG(pkt.PKT_DATA), r->pkt_data);
        wr(REG(aos.CTRL), r->idle_frames ? HPDP_AOS__CTRL__IDLE_EN_bm : 0);
        wr(REG(aos.FRAME_HDR), r->aos_frame_hdr);
        wr(REG(aos.FRAME_GAP), r->aos_frame_gap);
        wr(REG(ei.THRESHOLD), r->ei_threshold);
        wr(REG(ei.SEED), r->ei_seed);
        wr(REG(ei.BURST_LEN), r->ei_burst_len);
        wr(REG(ei.PERIOD), r->ei_period);
        wr(REG(ei.ASM_BITS), r->ei_asm_bits);
        wr(REG(ei.CTRL), r->ei_ctrl);
        wr(REG(ei.SEED_LOAD), 1);
        wr(REG(gr.SYNC), r->gr_sync);
        wr(REG(gr.XFER), r->gr_xfer);
        wr(REG(gr.CTRL), (r->gr_capture ? HPDP_GR__CTRL__CAPTURE_bm : 0) | HPDP_GR__CTRL__SKIP_IDLE_bm);
    } else {
        wr(REG(aos.CTRL), 0);
        wr(REG(ei.CTRL), 0);
        wr(REG(gr.CTRL), 0);
    }
    wr(REG(se.CNT_CLEAR), 1);
    wr(REG(ct.CNT_CLEAR), 1);
    wr(REG(cmp.CNT_CLEAR), 1);
    wr(REG(pkt.CNT_CLEAR), 1);
    wr(REG(aos.CNT_CLEAR), 1);
    wr(REG(ei.CNT_CLEAR), 1);
    wr(REG(gr.CNT_CLEAR), 1);
    wr(REG(lnk.CNT_CLEAR), 1);
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
 * lines; capture each compressed scene (CAPTURE) and/or the ground records
 * (DOWNLINK: transfers of gr_xfer records, back to back at gr_addr).
 *
 * The end of a downlink run, in steps (drain):
 *   0  every scene packetized
 *   1  the science frame count has settled: the last frames are on their way;
 *      idle frames on (they let the frame sync decide the last CADUs)
 *   2  3 more CADUs have left the channel coder, so the last science CADU has
 *      passed the error injector: injector off (clean idle frames, which the
 *      ground capture skips)
 *   3  every captured record is in memory and none arrives for 5 ms
 * Fills scene offsets/sizes; returns a status.
 */
static int acquire(const run_t *r, u32 *offset, u32 *bytes, u64 *ticks, u32 *gr_bytes, u64 *dl_ticks)
{
    const int capture = (r->mode & MODE_CAPTURE) != 0, downlink = (r->mode & MODE_DOWNLINK) != 0;
    u32 mm2s_done = 0, scene = 0, out_off = 0, armed_len = 0, gr_off = 0, gr_armed = 0;
    u32 sci_prev = 0, cadus_mark = 0, pass = 0;
    int mm2s_busy = 0, s2mm_busy = 0, gr_busy = 0, gr_full = 0, drain = 0;
    u64 t0 = now(), deadline = ~0ull, run_us, quiet = 0, t_mark = 0;
    u64 settle = us_to_ticks(200u + r->aos_frame_gap / 250u);  /* frame_gap: clk_sys (250 MHz) cycles */

    run_us = (u64)r->total * r->ny * r->line_period / 100u;     /* clk_sensor = 100 MHz */
    run_us += (u64)r->nx * r->ny * r->nz * r->total * 2u / 50u; /* downlink at >= 50 MB/s */
    *gr_bytes = 0;
    gr_avail = 0;
    wr(REG(sys.DMA_ROUTE), 0);
    if (sync_cfg())
        return ST_NO_HW;
    if (rd(REG(cmp.STATUS)) & HPDP_CMP__STATUS__CFG_ERROR_bm)
        return ST_CFG_ERROR;

    if (capture)
        Xil_DCacheInvalidateRange((UINTPTR)r->out_addr, r->out_capacity);
    if (downlink)
        Xil_DCacheInvalidateRange((UINTPTR)r->gr_addr, r->gr_capacity);
    if (r->source == 0)
        Xil_DCacheInvalidateRange((UINTPTR)r->raw_addr, r->raw_bytes);

    for (;;) {
        int cmp_done;

        /* S2MM: one transfer per scene, ended by tlast */
        if (capture) {
            if (s2mm_busy && !XAxiDma_Busy(&dma, XAXIDMA_DEVICE_TO_DMA)) {
                s2mm_busy = 0;
                bytes[scene] = S2MM_REG(XAXIDMA_BUFFLEN_OFFSET);
                offset[scene] = out_off;
                if (bytes[scene] >= armed_len)
                    return ST_OVERFLOW;             /* filled the buffer: no tlast seen */
                out_off = (out_off + bytes[scene] + 63u) & ~63u;
                *ticks = now() - t0;
                scene++;
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
        }
        /* ground S2MM: transfers end on tlast after gr_xfer records (or a
         * partial group, after 2^16 clk_sys cycles without a record) */
        if (downlink && r->gr_capture) {
            if (gr_busy && !XAxiDma_Busy(&dma_gr, XAXIDMA_DEVICE_TO_DMA)) {
                gr_busy = 0;
                gr_off += GR_REG(XAXIDMA_BUFFLEN_OFFSET);
                gr_avail = gr_off;                  /* complete records: ready for the upload */
                *dl_ticks = now() - t0;
            }
            if (!gr_busy && !gr_full) {
                u32 left = r->gr_capacity - gr_off;
                if (left < r->gr_xfer * RECORD_BYTES + 64u) {
                    gr_full = 1;                    /* the next group would not fit */
                } else {
                    gr_armed = left > S2MM_MAX ? S2MM_MAX : left;
                    if (XAxiDma_SimpleTransfer(&dma_gr, (UINTPTR)(r->gr_addr + gr_off), gr_armed,
                                               XAXIDMA_DEVICE_TO_DMA) != XST_SUCCESS)
                        return ST_DMA;
                    gr_busy = 1;
                }
            }
        }
        /* MM2S: raw data in chunks; with repeat, the buffer again from the start */
        if (r->source == 0) {
            if (mm2s_busy && !XAxiDma_Busy(&dma, XAXIDMA_DMA_TO_DEVICE))
                mm2s_busy = 0;
            if (!mm2s_busy && mm2s_done == r->raw_bytes && pass + 1 < r->repeat) {
                pass++;
                mm2s_done = 0;
            }
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
        if (!(rd(REG(se.CTRL)) & HPDP_SE__CTRL__RUN_bm) && deadline == ~0ull) {
            delay_us(20);
            wr(REG(se.CTRL), HPDP_SE__CTRL__RUN_bm);
            t0 = now();
            deadline = t0 + us_to_ticks(2 * run_us + 2000000u);
        }

        cmp_done = capture ? scene == r->scenes : rd(REG(cmp.SCENES_DONE)) >= r->total;
        if (cmp_done && !capture && !*ticks)
            *ticks = now() - t0;
        if (cmp_done && !downlink)
            break;
        if (cmp_done && downlink) {
            u32 captured = rd(REG(gr.CAPTURED)), sci;
            if (gr_full && captured * RECORD_BYTES > gr_off)
                return ST_GR_OVERFLOW;
            switch (drain) {
            case 0:
                if (rd(REG(pkt.SCENES)) >= r->total) {
                    sci_prev = rd(REG(aos.SCIENCE_FRAMES));
                    t_mark = now();
                    drain = 1;
                }
                break;
            case 1:
                sci = rd(REG(aos.SCIENCE_FRAMES));
                if (sci != sci_prev) {
                    sci_prev = sci;
                    t_mark = now();
                } else if (now() - t_mark > settle) {
                    cadus_mark = rd(REG(aos.CADUS));
                    wr(REG(aos.CTRL), HPDP_AOS__CTRL__IDLE_EN_bm);
                    drain = 2;
                }
                break;
            case 2:
                if (rd(REG(aos.CADUS)) - cadus_mark >= 3) {
                    wr(REG(ei.CTRL), 0);
                    quiet = 0;
                    drain = 3;
                }
                break;
            default:
                if (captured * RECORD_BYTES == gr_off) {
                    if (!quiet)
                        quiet = now();
                    else if (now() - quiet > us_to_ticks(5000))
                        drain = 4;
                } else {
                    quiet = 0;
                }
                break;
            }
            if (drain == 4)
                break;
        }
        if (dma_error())
            return ST_DMA;
        if (now() > deadline)
            return ST_TIMEOUT;
        net_poll();                                 /* live upload (no-op without network) */
    }
    wr(REG(se.CTRL), 0);
    *gr_bytes = gr_off;
    return ST_OK;
}

/* Waits about us microseconds, keeping the network going */
static void idle_us(u32 us)
{
    u64 end = now() + us_to_ticks(us);
    while (now() < end)
        net_poll();
}

/* PRBS-31 over the link (LNK-06, VER-06): generator on both transmitters,
 * checker on the receiving channel; 20 line bits per word */
static int prbs_run(const run_t *r, volatile result_t *o)
{
    u32 ctrl = link_ctrl(r), hi, lo, hi2;
    u64 t0, next, end, words;

    wr(REG(lnk.CTRL), ctrl);
    if (gt_tx_up(ctrl))
        return ST_LINK;
    wr(REG(lnk.CTRL), ctrl | (PRBS_31 << HPDP_LNK__CTRL__PRBS_SEL_bp));
    delay_us(2000);
    wr(REG(lnk.CNT_CLEAR), 1);                      /* also resets the GTH PRBS counters */
    delay_us(100);
    if (poll(REG(lnk.STATUS), HPDP_LNK__STATUS__PRBS_LOCKED_bm, HPDP_LNK__STATUS__PRBS_LOCKED_bm, 100000)) {
        xil_printf("  PRBS checker not locked (LNK.STATUS 0x%08x)\r\n", (unsigned)rd(REG(lnk.STATUS)));
        wr(REG(lnk.CTRL), ctrl);
        return ST_LINK;
    }
    t0 = now();
    next = t0 + us_to_ticks(10000000u);
    end = t0 + us_to_ticks((u64)r->prbs_seconds * 1000000u);
    while (now() < end) {
        if (now() > next) {
            next += us_to_ticks(10000000u);
            xil_printf("  PRBS %d s: %d Mwords, %d errors, %s\r\n", (int)((now() - t0) / COUNTS_PER_SECOND),
                       (int)(rd(REG(lnk.PRBS_WORDS_LO)) >> 20), (int)rd(REG(lnk.PRBS_ERRORS)),
                       (rd(REG(lnk.STATUS)) & HPDP_LNK__STATUS__PRBS_LOCKED_bm) ? "locked" : "NOT LOCKED");
        }
    }
    do {
        hi  = rd(REG(lnk.PRBS_WORDS_HI));
        lo  = rd(REG(lnk.PRBS_WORDS_LO));
        hi2 = rd(REG(lnk.PRBS_WORDS_HI));
    } while (hi != hi2);
    words = ((u64)hi << 32) | lo;
    o->prbs_errors   = rd(REG(lnk.PRBS_ERRORS));
    o->prbs_words_lo = lo;
    o->prbs_words_hi = hi;
    o->prbs_status   = rd(REG(lnk.STATUS));
    o->prbs_ticks_lo = (u32)(now() - t0);
    o->prbs_ticks_hi = (u32)((now() - t0) >> 32);
    wr(REG(lnk.CTRL), ctrl);
    xil_printf("  PRBS-31: %d s, %d Gbit checked, %d word errors, checker %s\r\n", (int)r->prbs_seconds,
               (int)(words * 20u / 1000000000u), (int)o->prbs_errors,
               (o->prbs_status & HPDP_LNK__STATUS__PRBS_LOCKED_bm) ? "locked" : "NOT LOCKED");
    return (o->prbs_status & HPDP_LNK__STATUS__PRBS_LOCKED_bm) ? ST_OK : ST_LINK;
}

static void fill_counters(volatile result_t *o)
{
    o->build_id         = rd(REG(sys.BUILD_ID));
    o->scenes_done      = rd(REG(cmp.SCENES_DONE));
    o->se_lines         = rd(REG(se.LINES_EMITTED));
    o->se_underrun      = rd(REG(se.UNDERRUN_SAMPLES));
    o->se_overruns      = rd(REG(se.PERIOD_OVERRUNS));
    o->se_dropped       = rd(REG(se.DROPPED_SAMPLES));
    o->rc_clamped       = rd(REG(rc.SCENE_CLAMPED));
    o->rc_replaced      = rd(REG(rc.SCENE_REPLACED));
    o->tbl_words        = rd(REG(rc.TBL_WORDS));
    o->ct_lines         = rd(REG(ct.LINES_OUT));
    o->ct_dropped       = rd(REG(ct.DROPPED_LINES));
    o->ct_unfilled      = rd(REG(ct.UNFILLED_LINES));
    o->cmp_samples      = rd(REG(cmp.SAMPLES_IN));
    o->cmp_bytes        = rd(REG(cmp.BYTES_OUT));
    o->cmp_last_cycles  = rd(REG(cmp.LAST_CYCLES));
    o->cmp_stalls       = rd(REG(cmp.OUT_STALLS));
    o->pkt_packets      = rd(REG(pkt.PACKETS));
    o->pkt_scenes       = rd(REG(pkt.SCENES));
    o->pkt_ts_overflows = rd(REG(pkt.TS_OVERFLOWS));
    o->aos_science      = rd(REG(aos.SCIENCE_FRAMES));
    o->aos_idle         = rd(REG(aos.IDLE_FRAMES));
    o->aos_cadus        = rd(REG(aos.CADUS));
    o->ei_bits          = rd(REG(ei.INJECTED_BITS));
    o->gr_cadus         = rd(REG(gr.CADUS));
    o->gr_sync_losses   = rd(REG(gr.SYNC_LOSSES));
    o->gr_asm_errors    = rd(REG(gr.ASM_BIT_ERRORS));
    o->gr_dropped       = rd(REG(gr.DROPPED_RECORDS));
    o->gr_skipped       = rd(REG(gr.SKIPPED_IDLE));
    o->gr_captured      = rd(REG(gr.CAPTURED));
    o->lnk_code_errors  = rd(REG(lnk.CODE_ERRORS));
    o->lnk_lock_losses  = rd(REG(lnk.LOCK_LOSSES));
    o->lnk_rx_words     = rd(REG(lnk.RX_WORDS));
    o->lnk_tx_data      = rd(REG(lnk.TX_DATA_WORDS));
    o->lnk_tx_idle      = rd(REG(lnk.TX_IDLE_WORDS));
    o->lnk_rx_overflows = rd(REG(lnk.RX_OVERFLOWS));
    o->lnk_clk_corr     = rd(REG(lnk.CLK_CORRECTIONS));
    o->lnk_buf_errors   = rd(REG(lnk.BUF_ERRORS));
}

static void print_summary(const run_t *r, volatile result_t *o, u64 ticks)
{
    u64 samples = (u64)r->nx * r->ny * r->nz * r->total;
    u64 us = ticks * 1000000u / COUNTS_PER_SECOND;
    u32 total = (r->mode & MODE_CAPTURE) ? 0 : o->cmp_bytes;

    if (r->mode & MODE_CAPTURE) {
        for (u32 s = 0; s < o->scenes_done && s < r->scenes; s++) {
            total += o->scene_bytes[s];
            xil_printf("  scene %d: %d bytes, %d.%02d bits/sample\r\n", (int)s, (int)o->scene_bytes[s],
                       (int)(800u * o->scene_bytes[s] / (r->nx * r->ny * r->nz) / 100u),
                       (int)(800u * o->scene_bytes[s] / (r->nx * r->ny * r->nz) % 100u));
        }
    }
    if (total == 0xFFFFFFFFu) {
        /* long run: CMP.BYTES_OUT saturated; the ratio of the last scene instead */
        samples /= r->total;
        total = rd(REG(cmp.LAST_BYTES));
        xil_printf("  %d Msamples in %d ms: %d Msamples/s; last scene ratio %d.%02d:1\r\n",
                   (int)(samples * r->total / 1000000u), (int)(us / 1000u),
                   us ? (int)(samples * r->total / us) : 0,
                   total ? (int)(samples * r->depth / (8u * total)) : 0,
                   total ? (int)(samples * r->depth * 100u / (8u * total) % 100u) : 0);
    } else {
        xil_printf("  %d Msamples in %d ms: %d Msamples/s; ratio %d.%02d:1\r\n", (int)(samples / 1000000u),
                   (int)(us / 1000u), us ? (int)(samples / us) : 0,
                   total ? (int)(samples * r->depth / (8u * total)) : 0,
                   total ? (int)(samples * r->depth * 100u / (8u * total) % 100u) : 0);
    }
    xil_printf("  emulator: %d lines, %d underruns, %d overruns, %d dropped; corner turn: %d lines, %d dropped, "
               "%d unfilled; scene guard: %d scenes padded\r\n",
               (int)o->se_lines, (int)o->se_underrun, (int)o->se_overruns, (int)o->se_dropped,
               (int)o->ct_lines, (int)o->ct_dropped, (int)o->ct_unfilled, (int)rd(REG(ct.SCENES_PADDED)));
    xil_printf("  correction: %d clamped, %d replaced (last scene); compressor: %d cycles last scene, %d output stalls\r\n",
               (int)o->rc_clamped, (int)o->rc_replaced, (int)o->cmp_last_cycles, (int)o->cmp_stalls);
    if (r->mode & MODE_DOWNLINK) {
        xil_printf("  downlink: %d packets, %d scenes, %d science / %d idle frames, %d CADUs, %d bits injected\r\n",
                   (int)o->pkt_packets, (int)o->pkt_scenes, (int)o->aos_science, (int)o->aos_idle,
                   (int)o->aos_cadus, (int)o->ei_bits);
        xil_printf("  link: %d code errors, %d lock losses, %d RX words, %d clock corrections, %d buffer errors\r\n",
                   (int)o->lnk_code_errors, (int)o->lnk_lock_losses, (int)o->lnk_rx_words,
                   (int)o->lnk_clk_corr, (int)o->lnk_buf_errors);
        xil_printf("  ground: %d CADUs, %d sync losses, %d ASM bit errors, %d dropped, %d idle skipped, %d captured (%d bytes)\r\n",
                   (int)o->gr_cadus, (int)o->gr_sync_losses, (int)o->gr_asm_errors, (int)o->gr_dropped,
                   (int)o->gr_skipped, (int)o->gr_captured, (int)o->gr_bytes);
    }
}

static int run_job(void)
{
    run_t r;
    u32 offset[MAX_SCENES], bytes[MAX_SCENES], mismatch = 0, gr_bytes = 0, tries = 0;
    u64 ticks = 0, dl_ticks = 0;
    int st = ST_OK, upload = 0;

    memset(offset, 0, sizeof(offset));
    memset(bytes, 0, sizeof(bytes));
    r.nx = job->nx;  r.ny = job->ny;  r.nz = job->nz;  r.depth = job->depth;
    r.scenes = job->scenes;  r.line_period = job->line_period;
    r.source = job->source;  r.rc_enable = job->rc_enable;
    r.pred = job->cmp_pred;  r.wupd = job->cmp_wupd;  r.coder = job->cmp_coder;  r.opts = job->cmp_opts;
    r.cal_addr = job->cal_addr;  r.cal_bytes = job->cal_bytes;
    r.raw_addr = job->raw_addr;  r.raw_bytes = job->raw_bytes;
    r.out_addr = job->out_addr;  r.out_capacity = job->out_capacity;
    r.load_table = (job->flags & JOB_LOAD_TABLE) != 0;
    r.mode = job->mode;  r.idle_frames = !(job->flags & 0x2u);   /* flags bit 1: no AOS idle frames */
    r.pkt_apid = job->pkt_apid;  r.pkt_data = job->pkt_data;
    r.aos_frame_hdr = job->aos_frame_hdr;  r.aos_frame_gap = job->aos_frame_gap;
    r.ei_ctrl = job->ei_ctrl;  r.ei_threshold = job->ei_threshold;  r.ei_seed = job->ei_seed;
    r.ei_burst_len = job->ei_burst_len;  r.ei_period = job->ei_period;  r.ei_asm_bits = job->ei_asm_bits;
    r.lnk_ctrl = job->lnk_ctrl;  r.gr_sync = job->gr_sync;  r.gr_xfer = job->gr_xfer;
    r.gr_addr = job->gr_addr;  r.gr_capacity = job->gr_capacity;
    r.link_timeout_ms = job->link_timeout_ms;  r.prbs_seconds = job->prbs_seconds;
    r.net_ctrl = job->net_ctrl;  r.net_ip = job->net_ip;  r.net_mask = job->net_mask;
    r.net_port = job->net_port ? job->net_port : NET_PORT;
    r.repeat = job->repeat ? job->repeat : 1;
    r.total = r.scenes * r.repeat;
    r.gr_capture = !(job->flags & JOB_NO_GR_CAPTURE);

    memset((void *)res, 0, sizeof(result_t));
    res->version = JOB_VERSION;
    res->seq = job->seq;
    res->timer_hz = COUNTS_PER_SECOND;

    if (job->version != JOB_VERSION) {
        st = ST_BAD_JOB;
    } else if (r.mode & MODE_PRBS) {
        xil_printf("\r\njob %d: PRBS-31 for %d s, LNK.CTRL 0x%08x\r\n", (int)job->seq, (int)r.prbs_seconds,
                   (unsigned)link_ctrl(&r));
        if (r.prbs_seconds == 0)
            st = ST_BAD_JOB;
        else
            st = prbs_run(&r, res);
        res->lnk_status = rd(REG(lnk.STATUS));
    } else {
        xil_printf("\r\njob %d: %dx%dx%d D=%d, %d scenes, line period %d, source %d, correction %s, %s%s\r\n",
                   (int)job->seq, (int)r.nx, (int)r.ny, (int)r.nz, (int)r.depth, (int)r.scenes,
                   (int)r.line_period, (int)r.source, r.rc_enable ? "on" : "off",
                   (r.mode & MODE_CAPTURE) ? "capture " : "", (r.mode & MODE_DOWNLINK) ? "downlink" : "");
        if (r.scenes == 0 || r.scenes > MAX_SCENES || r.nx == 0 || r.nx > 512 || r.nz < 16 || r.nz > 256 ||
            r.ny == 0 || r.depth < 2 || r.depth > 16 || r.line_period < r.nx * r.nz + 1 ||
            !(r.mode & (MODE_CAPTURE | MODE_DOWNLINK)) ||
            (r.source == 0 && (r.raw_bytes % 16 || r.raw_bytes < 2u * r.nx * r.ny * r.nz * r.scenes)) ||
            (r.load_table && r.cal_bytes != 8u * r.nx * r.nz + (r.nx * r.nz % 2 ? 8u : 0u)) ||
            ((r.mode & MODE_DOWNLINK) && (r.gr_xfer == 0 || r.gr_xfer > 0xFFFFu || r.pkt_data == 0 ||
                                          r.pkt_data > 2048u || r.gr_capacity < (r.gr_xfer + 1u) * RECORD_BYTES)) ||
            /* repeated runs: DMA source with no padding (scenes follow each other
               exactly), compressed scenes not captured, the line count in 32 bits */
            (r.repeat > 1 && (r.source != 0 || (r.mode & MODE_CAPTURE) ||
                              r.raw_bytes != 2u * r.nx * r.ny * r.nz * r.scenes ||
                              (u64)r.ny * r.scenes * r.repeat > 0xFFFFFFFFull)))
            st = ST_BAD_JOB;
        if (r.repeat > 1)
            xil_printf("  raw scenes streamed %d times: %d scenes, %d lines\r\n", (int)r.repeat, (int)r.total,
                       (int)(r.total * r.ny));
        upload = st == ST_OK && (r.mode & MODE_DOWNLINK) && (r.net_ctrl & NET_UPLOAD);
        if (upload) {
            /* first upload job: bring the network up (DHCP can take 15 s) */
            if (net_start(r.net_ip, r.net_mask, r.net_port) == 0) {
                net_stream((const u8 *)(UINTPTR)r.gr_addr, &gr_avail);
            } else {
                xil_printf("  network not available: no upload\r\n");
                upload = 0;
            }
        }
        if (st == ST_OK && (r.mode & MODE_DOWNLINK)) {
            if (link_up(&r, &tries)) {
                xil_printf("  link not LOCKED: LNK.STATUS 0x%08x after %d RX resets\r\n",
                           (unsigned)rd(REG(lnk.STATUS)), (int)tries);
                st = ST_LINK;
            }
            res->lnk_status = rd(REG(lnk.STATUS));
            res->link_tries = tries;
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
            st = acquire(&r, offset, bytes, &ticks, &gr_bytes, &dl_ticks);
        wr(REG(se.CTRL), 0);
        if (r.mode & MODE_DOWNLINK) {
            wr(REG(gr.CTRL), HPDP_GR__CTRL__SKIP_IDLE_bm);   /* stop capturing */
            reset_dma_gr();
        }
        if (upload) {
            /* every record is in DDR: deliver the rest to the connected client
               (one that connects later still gets the whole stream) */
            net_stream_end();
            res->upload_status = net_finish(60000u);
            xil_printf("  upload: %d of %d bytes acknowledged, %s\r\n", (int)net_acked(), (int)gr_bytes,
                       res->upload_status == UPLOAD_COMPLETE ? "complete" :
                       res->upload_status == UPLOAD_NO_CLIENT ? "no client connected (served on request)" :
                       "incomplete");
        }
    }
    res->net_ip = net_ip();
    res->upload_bytes = net_acked();

    fill_counters(res);
    res->tbl_mismatch = mismatch;
    res->ticks_lo = (u32)ticks;
    res->ticks_hi = (u32)(ticks >> 32);
    res->gr_bytes = gr_bytes;
    res->downlink_ticks_lo = (u32)dl_ticks;
    res->downlink_ticks_hi = (u32)(dl_ticks >> 32);
    for (u32 s = 0; s < r.scenes && s < MAX_SCENES; s++) {
        res->scene_offset[s] = offset[s];
        res->scene_bytes[s] = bytes[s];
    }
    res->status = st;
    if (st == ST_OK && !(r.mode & MODE_PRBS))
        print_summary(&r, res, ticks);
    /* make the output and result visible to the debugger, magic last */
    if (r.mode & MODE_CAPTURE)
        Xil_DCacheInvalidateRange((UINTPTR)r.out_addr, r.out_capacity);
    if (r.mode & MODE_DOWNLINK)
        Xil_DCacheInvalidateRange((UINTPTR)r.gr_addr, r.gr_capacity);
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
        .mode = MODE_CAPTURE,
        .repeat = 1, .total = 2,
    };
    u32 offset[MAX_SCENES], bytes[MAX_SCENES], gr_bytes;
    u64 ticks = 0, dl_ticks = 0;
    int st;

    xil_printf("\r\nno job at 0x%08x: self-test, 2 LFSR scenes %dx%dx%d\r\n", JOB_ADDR, (int)r.nx,
               (int)r.ny, (int)r.nz);
    configure(&r);
    if (sync_cfg()) {
        xil_printf("FAIL: hpdp_run self-test: no response from the HPDP registers\r\n");
        return -1;
    }
    st = acquire(&r, offset, bytes, &ticks, &gr_bytes, &dl_ticks);
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

    xil_printf("\r\nHPDP M5: hpdp_run\r\n");
    id = rd(REG(sys.ID));
    xil_printf("SYS.ID 0x%08x, build 0x%08x, LNK.STATUS 0x%08x\r\n", (unsigned)id,
               (unsigned)rd(REG(sys.BUILD_ID)), (unsigned)rd(REG(lnk.STATUS)));
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
        idle_us(1000);                              /* serves late upload clients */
    }
}
