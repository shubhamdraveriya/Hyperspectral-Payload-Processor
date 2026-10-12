/*
 * Job and result blocks shared with the host (sw/host/run_m4.py, run_m5.py).
 * All fields are little-endian u32; the host packs them in this order.
 *
 * The host loads the calibration table, the raw scenes and a job into PS DDR
 * over JTAG, then waits for a result with the same seq. hpdp_run keeps polling
 * the job block, so several jobs can run without reloading the program.
 *
 * Version 2 (M5) adds the downlink chain, the link and the ground capture:
 *   mode CAPTURE     compressed scenes to out_addr (CAPTURE_CMP), one per scene
 *   mode DOWNLINK    compressed scenes down the downlink chain and the link; the
 *                    ground records (1024 bytes each) to gr_addr, back to back
 *   mode PRBS        no scenes: PRBS-31 over the link for prbs_seconds (VER-06)
 */
#ifndef HPDP_JOB_H
#define HPDP_JOB_H

#include "xil_types.h"

#define JOB_ADDR        0x10000000u     /* job block (written by the host) */
#define RESULT_ADDR     0x10001000u     /* result block (written by hpdp_run) */
#define CAL_ADDR        0x11000000u     /* suggested buffer addresses */
#define RAW_ADDR        0x12000000u
#define OUT_ADDR        0x30000000u
#define GR_ADDR         0x40000000u

#define JOB_MAGIC       0x424F4A48u     /* "HJOB" */
#define RESULT_MAGIC    0x53455248u     /* "HRES" */
#define JOB_VERSION     2u
#define MAX_SCENES      64u

#define JOB_LOAD_TABLE  0x1u            /* flags: DMA the calibration table first */

#define MODE_CAPTURE    0x1u            /* mode bits */
#define MODE_DOWNLINK   0x2u
#define MODE_PRBS       0x4u

typedef struct {
    u32 magic, version, seq, flags;
    u32 nx, ny, nz, depth;
    u32 scenes, line_period, source, rc_enable;
    u32 cmp_pred, cmp_wupd, cmp_coder, cmp_opts;   /* CMP register values */
    u32 cal_addr, cal_bytes;                       /* .cal records (8 bytes each) */
    u32 raw_addr, raw_bytes;                       /* all scenes, BIL, multiple of 16 bytes */
    u32 out_addr, out_capacity;                    /* compressed scenes, 64-byte aligned each */
    /* version 2 */
    u32 mode;                                      /* MODE_* */
    u32 pkt_apid, pkt_data;                        /* PKT register values */
    u32 aos_frame_hdr, aos_frame_gap;              /* AOS register values */
    u32 ei_ctrl, ei_threshold, ei_seed, ei_burst_len, ei_period, ei_asm_bits;
    u32 lnk_ctrl;                                  /* LNK.CTRL (prbs_sel, resets ignored) */
    u32 gr_sync, gr_xfer;                          /* GR register values */
    u32 gr_addr, gr_capacity;                      /* ground records, a multiple of gr_xfer KB */
    u32 link_timeout_ms;                           /* wait for LOCKED */
    u32 prbs_seconds;
} job_t;

enum {
    ST_OK = 0,
    ST_BAD_JOB,             /* job fields out of range */
    ST_NO_HW,               /* HPDP registers not found */
    ST_CFG_ERROR,           /* compressor rejected the configuration */
    ST_TABLE,               /* calibration table load or readback failed */
    ST_DMA,                 /* DMA error */
    ST_TIMEOUT,             /* scenes did not complete in time */
    ST_OVERFLOW,            /* output buffer too small */
    ST_LINK,                /* link not LOCKED (or PRBS checker not locked) */
    ST_GR_OVERFLOW,         /* ground buffer full */
};

typedef struct {
    u32 magic, version, seq, status;
    u32 build_id, scenes_done, timer_hz, ticks_lo;
    u32 ticks_hi;                                  /* timer ticks from run start to the last scene */
    u32 se_lines, se_underrun, se_overruns, se_dropped;
    u32 rc_clamped, rc_replaced, tbl_words, tbl_mismatch;
    u32 ct_lines, ct_dropped, ct_unfilled;
    u32 cmp_samples, cmp_bytes, cmp_last_cycles, cmp_stalls;
    /* version 2 */
    u32 lnk_status, link_tries;                    /* LNK.STATUS after bring-up, RX resets needed */
    u32 pkt_packets, pkt_scenes, pkt_ts_overflows;
    u32 aos_science, aos_idle, aos_cadus, ei_bits;
    u32 gr_cadus, gr_sync_losses, gr_asm_errors, gr_dropped, gr_skipped, gr_captured;
    u32 gr_bytes;                                  /* ground record bytes at gr_addr */
    u32 lnk_code_errors, lnk_lock_losses, lnk_rx_words, lnk_tx_data, lnk_tx_idle;
    u32 lnk_rx_overflows, lnk_clk_corr, lnk_buf_errors;
    u32 prbs_errors, prbs_words_lo, prbs_words_hi; /* words of 20 line bits */
    u32 prbs_ticks_lo, prbs_ticks_hi, prbs_status;
    u32 downlink_ticks_lo, downlink_ticks_hi;      /* run start to the last ground record */
    u32 scene_offset[MAX_SCENES];                  /* from out_addr */
    u32 scene_bytes[MAX_SCENES];
} result_t;

#endif
