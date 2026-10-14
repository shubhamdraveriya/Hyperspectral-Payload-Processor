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
 *
 * Version 3 (M6) adds the upload of the ground records over Ethernet (net.h):
 * net_ctrl bit 0, address net_ip / net_mask (0: DHCP, else link-local), port.
 *
 * Version 4 (M7) adds memory ECC (RAD-02): up to three error injections armed
 * before the scenes (ecc_inject[], ECC_INJ_*; a calibration table entry is
 * rewritten so that the error goes in), and the ECC counters in the result.
 *
 * Version 5 (M7) adds the pipeline watchdog counters (RAD-05) to the result; a
 * stalled stage ends the job (ST_STALL). Flag JOB_WDT arms the PS watchdog for
 * the job; JOB_WDT_TEST arms it and hangs, so the board must reset.
 *
 * Version 6 (M7) adds the SEM controller (RAD-01): hpdp_run starts it at power-up
 * (configuration logic to the ICAP, grant); a job can inject a configuration
 * memory error before the scenes (sem_inject, SEM_INJ_*), and the result
 * carries the controller's state and counters.
 *
 * Version 7 (M7) adds the SEM campaign (RAD-07 b): sem_count configuration
 * memory bit errors before the scenes, one at a time, at frames drawn from
 * [sem_lfa_lo, sem_lfa_hi]; the result counts the corrected ones and their
 * latency. The result also carries the RAD-03 / RAD-04 counters (weight ECC,
 * TMR disagreements).
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
#define JOB_VERSION     7u
#define MAX_SCENES      64u

#define JOB_LOAD_TABLE  0x1u            /* flags: DMA the calibration table first */
#define JOB_NO_IDLE     0x2u            /*        no AOS idle frames while the scenes go down */
#define JOB_NO_GR_CAPTURE 0x4u          /*        ground records counted, not captured (long runs) */
#define JOB_WDT         0x8u            /*        PS watchdog armed during the job (RAD-05) */
#define JOB_WDT_TEST    0x10u           /*        arm it, then hang: the system must reset */

#define MODE_CAPTURE    0x1u            /* mode bits */
#define MODE_DOWNLINK   0x2u
#define MODE_PRBS       0x4u

#define NET_UPLOAD      0x1u            /* net_ctrl: serve the ground records over TCP */

#define ECC_INJ_ENABLE  0x80000000u     /* ecc_inject[]: arm this injection */
#define ECC_INJ_DBE     0x40000000u     /*   double-bit error (else single-bit) */
#define ECC_INJ_MEM_SH  28u             /*   memory [29:28]: 0 table, 1 corner turn, 2 line buffer */
#define ECC_INJ_ADDR_bm 0x000FFFFFu     /*   address [19:0], as RAD.ECC_INJECT_ADDR */

#define SEM_INJ_ENABLE  0x80000000u     /* sem_inject: inject before the scenes */
#define SEM_INJ_ADDR_bm 0x3FFFFFFFu     /*   [29:0] as RAD.SEM_INJ_ADDR: frame, word, bit */

#define SEM_CAMP_DESIGN 0x1u            /* sem_flags: any word and bit of the frame (else the
                                           frame's ECC word: word 46, bits 0 - 7) */
#define SEM_CAMP_MAX    10000u          /* sem_count, at most */

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
    /* version 3 */
    u32 net_ctrl, net_ip, net_mask, net_port;      /* ground record upload (net.h) */
    u32 repeat;                                    /* the raw scenes streamed this many times (0 = 1):
                                                      long runs (VER-08); DOWNLINK without CAPTURE */
    /* version 4 */
    u32 ecc_inject[3];                             /* ECC_INJ_* */
    /* version 6 */
    u32 sem_inject;                                /* SEM_INJ_* */
    /* version 7 */
    u32 sem_count;                                 /* SEM campaign: bit errors before the scenes */
    u32 sem_lfa_lo, sem_lfa_hi;                    /*   linear frame addresses drawn from */
    u32 sem_seed;                                  /*   xorshift32 seed (0: 1) */
    u32 sem_flags;                                 /*   SEM_CAMP_* */
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
    ST_ECC,                 /* an ECC error injection could not be armed */
    ST_STALL,               /* a pipeline watchdog found a stage stalled (RAD-05) */
    ST_SEM,                 /* the SEM controller is not observing, or took no command */
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
    /* version 3 */
    u32 net_ip, upload_status, upload_bytes;       /* UPLOAD_* (net.h), bytes acknowledged */
    /* version 4: RAD counters, reads that returned a corrected / an uncorrectable word */
    u32 ecc_cal_sbe, ecc_cal_dbe, ecc_ct_sbe, ecc_ct_dbe, ecc_lb_sbe, ecc_lb_dbe;
    u32 ecc_pending;                               /* RAD.STATUS: injections not yet written */
    /* version 5: pipeline watchdog stalls (RAD-05) */
    u32 wd_cmp, wd_dl, wd_gr;
    /* version 6: SEM controller (RAD-01) */
    u32 sem_status, sem_corrections, sem_uncorrectable, sem_heartbeats;
    u32 sem_inject_ms;                             /* injection to correction (0: none seen) */
    /* version 7: SEM campaign (RAD-07 b) */
    u32 sem_injected, sem_corrected, sem_missed;   /* missed: no correction within 5 s (undone) */
    u32 sem_lat_min_ms, sem_lat_max_ms, sem_lat_sum_ms;    /* injection to correction, corrected ones */
    u32 ecc_w_sbe, ecc_w_dbe;                      /* compressor weights (RAD-03) */
    u32 tmr_cmp, tmr_regs, tmr_fsm;                /* TMR copies disagreed: compressor memories,
                                                      register block (RAD-03), state machines (RAD-04) */
    u32 scene_offset[MAX_SCENES];                  /* from out_addr */
    u32 scene_bytes[MAX_SCENES];
} result_t;

#endif
