/*
 * Job and result blocks shared with the host (sw/host/run_m4.py). All fields
 * are little-endian u32; the host packs them in this order.
 *
 * The host loads the calibration table, the raw scenes and a job into PS DDR
 * over JTAG, then waits for a result with the same seq. hpdp_run keeps polling
 * the job block, so several jobs can run without reloading the program.
 */
#ifndef HPDP_JOB_H
#define HPDP_JOB_H

#include "xil_types.h"

#define JOB_ADDR        0x10000000u     /* job block (written by the host) */
#define RESULT_ADDR     0x10001000u     /* result block (written by hpdp_run) */
#define CAL_ADDR        0x11000000u     /* suggested buffer addresses */
#define RAW_ADDR        0x12000000u
#define OUT_ADDR        0x30000000u

#define JOB_MAGIC       0x424F4A48u     /* "HJOB" */
#define RESULT_MAGIC    0x53455248u     /* "HRES" */
#define JOB_VERSION     1u
#define MAX_SCENES      64u

#define JOB_LOAD_TABLE  0x1u            /* flags: DMA the calibration table first */

typedef struct {
    u32 magic, version, seq, flags;
    u32 nx, ny, nz, depth;
    u32 scenes, line_period, source, rc_enable;
    u32 cmp_pred, cmp_wupd, cmp_coder, cmp_opts;   /* CMP register values */
    u32 cal_addr, cal_bytes;                       /* .cal records (8 bytes each) */
    u32 raw_addr, raw_bytes;                       /* all scenes, BIL, multiple of 16 bytes */
    u32 out_addr, out_capacity;                    /* compressed scenes, 64-byte aligned each */
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
};

typedef struct {
    u32 magic, version, seq, status;
    u32 build_id, scenes_done, timer_hz, ticks_lo;
    u32 ticks_hi;                                  /* timer ticks from run start to the last scene */
    u32 se_lines, se_underrun, se_overruns, se_dropped;
    u32 rc_clamped, rc_replaced, tbl_words, tbl_mismatch;
    u32 ct_lines, ct_dropped, ct_unfilled;
    u32 cmp_samples, cmp_bytes, cmp_last_cycles, cmp_stalls;
    u32 scene_offset[MAX_SCENES];                  /* from out_addr */
    u32 scene_bytes[MAX_SCENES];
} result_t;

#endif
