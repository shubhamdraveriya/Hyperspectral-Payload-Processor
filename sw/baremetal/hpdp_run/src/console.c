/*
 * UART console (SPEC SW-04) and SD card loading (SW-02).
 *
 * Commands typed on the PS UART (115200 8N1) build a job block the way the host
 * tools do (sw/host/run_m5.py: the same defaults), at JOB_ADDR with a new
 * sequence number. main() runs it as it runs a host job, so a console run gets
 * the same checks, link bring-up, counters and per-scene summary (SW-06); the
 * JTAG job path is unchanged. Raw scenes (.hpdp) and calibration tables (.cal,
 * SPEC 7) come from the SD card (FAT32, opened read-only: the card is never
 * written). Type "help" for the commands.
 */
#include <stdlib.h>
#include <string.h>
#include "xparameters.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xuartps_hw.h"
#include "ff.h"
#include "hpdp_regs.h"
#include "hpdp_job.h"
#include "console.h"

#define LINE_LEN        128
#define MAX_ARGS        12
#define HDR_BYTES       64u
#define RAW_LIMIT       (OUT_ADDR - RAW_ADDR)      /* raw scene buffer */
#define CAL_LIMIT       (RAW_ADDR - CAL_ADDR)      /* calibration table buffer */
#define OUT_LIMIT       (GR_ADDR - OUT_ADDR)       /* compressed scenes */
#define GR_CAPACITY     (512u << 20)               /* ground records */
#define GR_XFER         64u                        /* records per ground DMA transfer */
#define CON_SEQ         0x43000000u                /* console job numbers ("C" ...), apart from the host's */
#define READ_CHUNK      (4u << 20)

/* register field from a value; reset values (the RDL defaults) are the
   compressor's default configuration, as in the Python model */
#define FIELD(reg, f, v)  (((u32)(v) << HPDP_##reg##__##f##_bp) & HPDP_##reg##__##f##_bm)
#define DEFAULT(reg, f)   FIELD(reg, f, HPDP_##reg##__##f##_reset)

static struct {
    u32 scenes, nx, ny, nz, depth, raw_bytes;     /* loaded scenes (0: none) */
    u32 cal_nx, cal_nz, cal_bytes;                 /* loaded table (cal_bytes 0: none) */
    u32 mode, raw, rc, period, repeat, upload;
    u32 ei_ctrl, ei_threshold, ei_seed, ei_burst_len, ei_period, ei_asm_bits;
    u32 rx_sel, gt_loopback, fabric;
    u32 seq;
} s = {.mode = MODE_DOWNLINK, .rc = 1, .repeat = 1, .ei_seed = 1, .ei_period = 1, .seq = CON_SEQ};

static FATFS fs;
static int mounted;
static char line[LINE_LEN];
static u32 len;

/* ---------------------------------------------------------------- SD card */

static int sd_mount(void)
{
    FRESULT r;

    if (mounted)
        return 0;
    r = f_mount(&fs, "0:/", 1);
    if (r != FR_OK) {
        xil_printf("SD card: not mounted (FatFs error %d): card inserted, FAT32?\r\n", (int)r);
        return -1;
    }
    mounted = 1;
    return 0;
}

static u32 le32(const u8 *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((u32)p[3] << 24); }
static u32 le16(const u8 *p) { return p[0] | (p[1] << 8); }

/* n bytes from the file to DDR at dst (a dot per 4 MB); 0 when all were read */
static int read_to(FIL *f, UINTPTR dst, u32 n)
{
    u32 done = 0;

    while (done < n) {
        UINT want = (n - done > READ_CHUNK) ? READ_CHUNK : n - done, got = 0;
        if (f_read(f, (void *)(dst + done), want, &got) != FR_OK || got != want)
            return -1;
        done += got;
        if (n > READ_CHUNK)
            xil_printf(".");
    }
    if (n > READ_CHUNK)
        xil_printf("\r\n");
    Xil_DCacheFlushRange(dst, n);                  /* for the DMA */
    return 0;
}

static void cmd_ls(void)
{
    DIR d;
    FILINFO fi;
    u32 n = 0;

    if (sd_mount() || f_opendir(&d, "0:/") != FR_OK) {
        xil_printf("SD card: no directory\r\n");
        return;
    }
    while (f_readdir(&d, &fi) == FR_OK && fi.fname[0]) {
        if (!(fi.fattrib & AM_DIR)) {
            xil_printf("  %10d  %s\r\n", (int)fi.fsize, fi.fname);
            n++;
        }
    }
    f_closedir(&d);
    xil_printf("%d files\r\n", (int)n);
}

/* load FILE.hpdp ...: raw scenes, BIL, all of one size and depth (SW-02) */
static void cmd_load(int argc, char **argv)
{
    u32 nx = 0, ny = 0, nz = 0, depth = 0, off = 0, k;

    if (argc < 2 || argc - 1 > (int)MAX_SCENES) {
        xil_printf("load FILE.hpdp [FILE.hpdp ...]   (1 .. %d scenes)\r\n", (int)MAX_SCENES);
        return;
    }
    if (sd_mount())
        return;
    s.scenes = 0;
    for (k = 1; k < (u32)argc; k++) {
        FIL f;
        u8 h[HDR_BYTES];
        UINT got;
        u32 bytes;

        if (f_open(&f, argv[k], FA_READ) != FR_OK) {
            xil_printf("%s: cannot open\r\n", argv[k]);
            return;
        }
        if (f_read(&f, h, HDR_BYTES, &got) != FR_OK || got != HDR_BYTES || memcmp(h, "HPDP", 4) != 0 ||
            le16(h + 6) != HDR_BYTES) {
            xil_printf("%s: not a .hpdp scene (SPEC 7)\r\n", argv[k]);
            f_close(&f);
            return;
        }
        if (h[21] != 0) {
            xil_printf("%s: sample order %d; the sensor emulator needs BIL (0)\r\n", argv[k], (int)h[21]);
            f_close(&f);
            return;
        }
        if (k == 1) {
            nx = le32(h + 8);  ny = le32(h + 12);  nz = le32(h + 16);  depth = h[20];
        } else if (le32(h + 8) != nx || le32(h + 12) != ny || le32(h + 16) != nz || h[20] != depth) {
            xil_printf("%s: size or depth differs from the first scene\r\n", argv[k]);
            f_close(&f);
            return;
        }
        bytes = 2u * nx * ny * nz;
        if ((u64)off + bytes + 16u > RAW_LIMIT) {
            xil_printf("%s: the scenes do not fit the raw buffer (%d MB)\r\n", argv[k], (int)(RAW_LIMIT >> 20));
            f_close(&f);
            return;
        }
        xil_printf("%s: %dx%dx%d D=%d, %d bytes\r\n", argv[k], (int)nx, (int)ny, (int)nz, (int)depth, (int)bytes);
        if (read_to(&f, RAW_ADDR + off, bytes)) {
            xil_printf("%s: read error\r\n", argv[k]);
            f_close(&f);
            return;
        }
        f_close(&f);
        off += bytes;
    }
    while (off % 16u) {                            /* DMA transfers: a multiple of 16 bytes */
        *(volatile u8 *)(UINTPTR)(RAW_ADDR + off) = 0;
        off++;
    }
    Xil_DCacheFlushRange(RAW_ADDR, off);
    s.scenes = (u32)argc - 1;
    s.nx = nx;  s.ny = ny;  s.nz = nz;  s.depth = depth;  s.raw_bytes = off;
    xil_printf("%d scenes loaded\r\n", (int)s.scenes);
}

/* cal FILE.cal: calibration table, one 8-byte record per (band, pixel) (SW-02) */
static void cmd_cal(int argc, char **argv)
{
    FIL f;
    u8 h[HDR_BYTES];
    UINT got;
    u32 nx, nz, bytes;

    if (argc != 2) {
        xil_printf("cal FILE.cal\r\n");
        return;
    }
    if (sd_mount())
        return;
    if (f_open(&f, argv[1], FA_READ) != FR_OK) {
        xil_printf("%s: cannot open\r\n", argv[1]);
        return;
    }
    if (f_read(&f, h, HDR_BYTES, &got) != FR_OK || got != HDR_BYTES || memcmp(h, "HCAL", 4) != 0 ||
        le16(h + 6) != HDR_BYTES || h[16] != 14) {
        xil_printf("%s: not a .cal table with 14 gain fraction bits (SPEC 7)\r\n", argv[1]);
        f_close(&f);
        return;
    }
    nx = le32(h + 8);
    nz = le32(h + 12);
    bytes = 8u * nx * nz;
    if (bytes + 16u > CAL_LIMIT) {
        xil_printf("%s: too large\r\n", argv[1]);
        f_close(&f);
        return;
    }
    if (read_to(&f, CAL_ADDR, bytes)) {
        xil_printf("%s: read error\r\n", argv[1]);
        f_close(&f);
        return;
    }
    f_close(&f);
    if (bytes % 16u) {                             /* an odd record count: one zero record more */
        memset((void *)(UINTPTR)(CAL_ADDR + bytes), 0, 8);
        bytes += 8;
        Xil_DCacheFlushRange(CAL_ADDR, bytes);
    }
    s.cal_nx = nx;  s.cal_nz = nz;  s.cal_bytes = bytes;
    xil_printf("%s: table for %dx%d loaded\r\n", argv[1], (int)nx, (int)nz);
}

/* ---------------------------------------------------------------- jobs */

static u32 link_word(void)
{
    return FIELD(LNK__CTRL, TX_ENABLE, 1) | FIELD(LNK__CTRL, FABRIC_LOOPBACK, s.fabric) |
           FIELD(LNK__CTRL, RX_SEL, s.rx_sel) | FIELD(LNK__CTRL, GT_LOOPBACK, s.gt_loopback);
}

/* the job block for main(): magic written last */
static void post_job(job_t *j)
{
    volatile job_t *dst = (volatile job_t *)JOB_ADDR;

    j->version = JOB_VERSION;
    j->seq = ++s.seq;
    j->magic = 0;
    memcpy((void *)dst, j, sizeof(job_t));
    Xil_DCacheFlushRange(JOB_ADDR, sizeof(job_t));
    dst->magic = JOB_MAGIC;
    Xil_DCacheFlushRange(JOB_ADDR, sizeof(job_t));
}

/* run: the loaded scenes with the current settings (SW-04) */
static void cmd_run(void)
{
    job_t j;
    u32 omega = HPDP_CMP__PRED__OMEGA_reset, r, k, mode = s.mode;

    if (!s.scenes) {
        xil_printf("no scenes: load FILE.hpdp ... first\r\n");
        return;
    }
    if (s.rc && (s.cal_bytes == 0 || s.cal_nx != s.nx || s.cal_nz != s.nz)) {
        xil_printf("correction on needs a %dx%d table (cal FILE.cal), or: correction off\r\n", (int)s.nx,
                   (int)s.nz);
        return;
    }
    if (s.repeat > 1 && (mode & MODE_CAPTURE)) {
        xil_printf("repeat %d: downlink only (the compressed scenes are not captured)\r\n", (int)s.repeat);
        mode = MODE_DOWNLINK;
    }
    memset(&j, 0, sizeof(j));
    j.flags = (s.rc ? JOB_LOAD_TABLE : 0) | (s.repeat > 1 ? JOB_NO_GR_CAPTURE : 0);
    j.nx = s.nx;  j.ny = s.ny;  j.nz = s.nz;  j.depth = s.depth;  j.scenes = s.scenes;
    j.line_period = s.period ? s.period : s.nx * s.nz + 1;
    j.source = 0;                                  /* DMA */
    j.rc_enable = s.rc;
    /* compressor: the default configuration; R and K follow the depth as in the model */
    r = s.depth + omega + 2 > 32 ? s.depth + omega + 2 : 32;
    k = s.depth - 2 < 7 ? s.depth - 2 : 7;
    j.cmp_pred = DEFAULT(CMP__PRED, BANDS) | DEFAULT(CMP__PRED, FULL) | DEFAULT(CMP__PRED, LOCAL_SUM) |
                 FIELD(CMP__PRED, OMEGA, omega) | FIELD(CMP__PRED, REG_SIZE, r);
    j.cmp_wupd = DEFAULT(CMP__WUPD, V_MIN) | DEFAULT(CMP__WUPD, V_MAX) | DEFAULT(CMP__WUPD, T_INC_LOG2);
    j.cmp_coder = DEFAULT(CMP__CODER, U_MAX) | DEFAULT(CMP__CODER, GAMMA0) | DEFAULT(CMP__CODER, GAMMA_STAR) |
                  FIELD(CMP__CODER, K_INIT, k) | DEFAULT(CMP__CODER, WORD_BYTES);
    j.cmp_opts = FIELD(CMP__OPTS, RAW, s.raw);
    j.cal_addr = CAL_ADDR;  j.cal_bytes = s.rc ? s.cal_bytes : 0;
    j.raw_addr = RAW_ADDR;  j.raw_bytes = s.raw_bytes;
    j.out_addr = OUT_ADDR;
    j.out_capacity = ((s.raw_bytes / 2u * 3u + 64u * s.scenes + (1u << 20)) + 0xFFFFu) & ~0xFFFFu;
    if (j.out_capacity > OUT_LIMIT)
        j.out_capacity = OUT_LIMIT;
    j.mode = mode;
    /* downlink: run_m5.py's defaults */
    j.pkt_apid = 0x100;
    j.pkt_data = 2048;
    j.aos_frame_hdr = DEFAULT(AOS__FRAME_HDR, SCID) | DEFAULT(AOS__FRAME_HDR, VCID);
    j.ei_ctrl = s.ei_ctrl;  j.ei_threshold = s.ei_threshold;  j.ei_seed = s.ei_seed;
    j.ei_burst_len = s.ei_burst_len;  j.ei_period = s.ei_period;  j.ei_asm_bits = s.ei_asm_bits;
    j.lnk_ctrl = link_word();
    j.gr_sync = DEFAULT(GR__SYNC, ASM_TOL) | DEFAULT(GR__SYNC, FLYWHEEL);
    j.gr_xfer = GR_XFER;
    j.gr_addr = GR_ADDR;
    j.gr_capacity = GR_CAPACITY;
    j.link_timeout_ms = 1000;
    j.repeat = s.repeat;
    j.net_ctrl = s.upload ? NET_UPLOAD : 0;
    post_job(&j);
}

/* prbs SECONDS: PRBS-31 over the link (SW-04, VER-06) */
static void cmd_prbs(int argc, char **argv)
{
    job_t j;
    long t = argc == 2 ? strtol(argv[1], NULL, 0) : 0;

    if (t <= 0 || t > 86400) {
        xil_printf("prbs SECONDS   (1 .. 86400)\r\n");
        return;
    }
    memset(&j, 0, sizeof(j));
    j.mode = MODE_PRBS;
    j.lnk_ctrl = link_word();
    j.prbs_seconds = (u32)t;
    j.link_timeout_ms = 1000;
    post_job(&j);
}

/* ---------------------------------------------------------------- settings */

static int on_off(const char *a, u32 *v)
{
    if (!strcmp(a, "on"))  { *v = 1; return 0; }
    if (!strcmp(a, "off")) { *v = 0; return 0; }
    xil_printf("on or off\r\n");
    return -1;
}

/* ei off | ber X | burst LEN PERIOD | asm BITS PERIOD (error injector, SW-04) */
static void cmd_ei(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "off")) {
        s.ei_ctrl = 0;  s.ei_threshold = 0;  s.ei_burst_len = 0;  s.ei_period = 1;  s.ei_asm_bits = 0;
    } else if (argc == 3 && !strcmp(argv[1], "ber")) {
        double ber = strtod(argv[2], NULL);
        double t = ber * 4294967296.0;
        if (!(ber > 0.0) || ber > 0.5) {
            xil_printf("ber: 0 < BER <= 0.5, e.g. 1e-3\r\n");
            return;
        }
        s.ei_ctrl = 1;  s.ei_threshold = t >= 4294967295.0 ? 0xFFFFFFFFu : (u32)(t + 0.5);
        s.ei_burst_len = 0;  s.ei_period = 1;  s.ei_asm_bits = 0;
    } else if (argc == 4 && !strcmp(argv[1], "burst")) {
        s.ei_ctrl = 2;  s.ei_threshold = 0;  s.ei_burst_len = (u32)strtoul(argv[2], NULL, 0);
        s.ei_period = (u32)strtoul(argv[3], NULL, 0);  s.ei_asm_bits = 0;
    } else if (argc == 4 && !strcmp(argv[1], "asm")) {
        s.ei_ctrl = 3;  s.ei_threshold = 0;  s.ei_burst_len = 0;
        s.ei_asm_bits = (u32)strtoul(argv[2], NULL, 0);  s.ei_period = (u32)strtoul(argv[3], NULL, 0);
    } else {
        xil_printf("ei off | ber X | burst LEN_BITS PERIOD_BYTES | asm BITS PERIOD_CADUS\r\n");
        return;
    }
    if (s.ei_period == 0)
        s.ei_period = 1;
}

/* link [sfp1|sfp0] [none|pcs|pma|fabric]: receiving cage, loopback */
static void cmd_link(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "sfp1"))        s.rx_sel = 0;
        else if (!strcmp(argv[i], "sfp0"))   s.rx_sel = 1;
        else if (!strcmp(argv[i], "none"))   { s.gt_loopback = 0; s.fabric = 0; }
        else if (!strcmp(argv[i], "pcs"))    { s.gt_loopback = 1; s.fabric = 0; }
        else if (!strcmp(argv[i], "pma"))    { s.gt_loopback = 2; s.fabric = 0; }
        else if (!strcmp(argv[i], "fabric")) { s.gt_loopback = 0; s.fabric = 1; }
        else {
            xil_printf("link [sfp1|sfp0] [none|pcs|pma|fabric]\r\n");
            return;
        }
    }
}

static void cmd_show(void)
{
    static const char *const ei[4] = {"off", "random", "burst", "asm"};
    static const char *const lb[4] = {"none", "pcs", "pma", "?"};

    if (s.scenes)
        xil_printf("scenes      %d x %dx%dx%d D=%d (%d bytes)\r\n", (int)s.scenes, (int)s.nx, (int)s.ny,
                   (int)s.nz, (int)s.depth, (int)s.raw_bytes);
    else
        xil_printf("scenes      none (load FILE.hpdp ...)\r\n");
    if (s.cal_bytes)
        xil_printf("table       %dx%d\r\n", (int)s.cal_nx, (int)s.cal_nz);
    else
        xil_printf("table       none (cal FILE.cal)\r\n");
    xil_printf("mode        %s%s%s, raw %s, correction %s, upload %s\r\n",
               (s.mode & MODE_CAPTURE) ? "capture" : "", s.mode == (MODE_CAPTURE | MODE_DOWNLINK) ? "+" : "",
               (s.mode & MODE_DOWNLINK) ? "downlink" : "", s.raw ? "on" : "off", s.rc ? "on" : "off",
               s.upload ? "on" : "off");
    xil_printf("line period %d%s, repeat %d\r\n", (int)(s.period ? s.period : s.nx * s.nz + 1),
               s.period ? "" : " (Nx*Nz+1)", (int)s.repeat);
    xil_printf("error inj.  %s, threshold 0x%08x, burst %d, period %d, asm bits %d, seed %d\r\n",
               ei[s.ei_ctrl & 3], (unsigned)s.ei_threshold, (int)s.ei_burst_len, (int)s.ei_period,
               (int)s.ei_asm_bits, (int)s.ei_seed);
    xil_printf("link        rx %s, loopback %s\r\n", s.rx_sel ? "sfp0" : "sfp1",
               s.fabric ? "fabric" : lb[s.gt_loopback & 3]);
}

static void cmd_help(void)
{
    xil_printf(
        "ls                              files on the SD card\r\n"
        "load FILE.hpdp [FILE.hpdp ...]  raw scenes from the SD card (BIL, one size)\r\n"
        "cal FILE.cal                    calibration table from the SD card\r\n"
        "mode capture|downlink|both      where the compressed scenes go\r\n"
        "raw on|off                      compressor bypass (RAW mode)\r\n"
        "correction on|off               radiometric correction\r\n"
        "period CYCLES                   line period in clk_sensor cycles (0: Nx*Nz+1)\r\n"
        "repeat N                        stream the scenes N times (downlink)\r\n"
        "ei off|ber X|burst L P|asm B P  error injector\r\n"
        "seed N                          error injector seed\r\n"
        "link [sfp1|sfp0] [none|pcs|pma|fabric]   receiving cage, loopback\r\n"
        "upload on|off                   serve the ground records over TCP (SW-05)\r\n"
        "run                             run the loaded scenes\r\n"
        "prbs SECONDS                    PRBS-31 link test\r\n"
        "status                          status and counters\r\n"
        "show                            current settings\r\n");
}

/* ---------------------------------------------------------------- line input */

static void execute(void)
{
    char *argv[MAX_ARGS];
    int argc = 0;
    char *p = line;

    while (*p && argc < MAX_ARGS) {
        while (*p == ' ' || *p == '\t')
            *p++ = 0;
        if (!*p)
            break;
        argv[argc++] = p;
        while (*p && *p != ' ' && *p != '\t')
            p++;
    }
    if (argc == 0)
        return;
    if (!strcmp(argv[0], "help") || !strcmp(argv[0], "?"))  cmd_help();
    else if (!strcmp(argv[0], "ls"))         cmd_ls();
    else if (!strcmp(argv[0], "load"))       cmd_load(argc, argv);
    else if (!strcmp(argv[0], "cal"))        cmd_cal(argc, argv);
    else if (!strcmp(argv[0], "run"))        cmd_run();
    else if (!strcmp(argv[0], "prbs"))       cmd_prbs(argc, argv);
    else if (!strcmp(argv[0], "status"))     hpdp_status();
    else if (!strcmp(argv[0], "show"))       cmd_show();
    else if (!strcmp(argv[0], "ei"))         cmd_ei(argc, argv);
    else if (!strcmp(argv[0], "link"))       cmd_link(argc, argv);
    else if (!strcmp(argv[0], "mode") && argc == 2) {
        if (!strcmp(argv[1], "capture"))       s.mode = MODE_CAPTURE;
        else if (!strcmp(argv[1], "downlink")) s.mode = MODE_DOWNLINK;
        else if (!strcmp(argv[1], "both"))     s.mode = MODE_CAPTURE | MODE_DOWNLINK;
        else xil_printf("mode capture|downlink|both\r\n");
    }
    else if (!strcmp(argv[0], "raw") && argc == 2)        on_off(argv[1], &s.raw);
    else if (!strcmp(argv[0], "correction") && argc == 2) on_off(argv[1], &s.rc);
    else if (!strcmp(argv[0], "upload") && argc == 2)     on_off(argv[1], &s.upload);
    else if (!strcmp(argv[0], "period") && argc == 2)     s.period = (u32)strtoul(argv[1], NULL, 0);
    else if (!strcmp(argv[0], "repeat") && argc == 2) {
        u32 n = (u32)strtoul(argv[1], NULL, 0);
        s.repeat = n ? n : 1;
    }
    else if (!strcmp(argv[0], "seed") && argc == 2)       s.ei_seed = (u32)strtoul(argv[1], NULL, 0);
    else xil_printf("unknown command: %s (help)\r\n", argv[0]);
}

void console_prompt(void)
{
    xil_printf("hpdp> ");
}

void console_init(void)
{
    xil_printf("\r\nconsole: type help (SD card scenes: ls, load, cal; then run)\r\n");
    console_prompt();
}

void console_poll(void)
{
    while (XUartPs_IsReceiveData(STDIN_BASEADDRESS)) {
        char c = (char)XUartPs_RecvByte(STDIN_BASEADDRESS);
        if (c == '\r' || c == '\n') {
            if (c == '\n' && len == 0)
                continue;                          /* the LF of a CR LF */
            xil_printf("\r\n");
            line[len] = 0;
            len = 0;
            execute();
            console_prompt();
        } else if (c == 0x08 || c == 0x7F) {      /* backspace */
            if (len) {
                len--;
                xil_printf("\b \b");
            }
        } else if (c >= ' ' && len < LINE_LEN - 1) {
            line[len++] = c;
            xil_printf("%c", c);
        }
    }
}
