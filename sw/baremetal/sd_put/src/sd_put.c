/*
 * sd_put: write files from DDR to the SD card, for boards without a card reader
 * at hand (sw/host/sd_put.py loads the files and a manifest over JTAG, runs this,
 * and reads the result back).
 *
 * The manifest at MANIFEST_ADDR lists up to MAX_FILES files (name, DDR address,
 * size). Each file is written to the card's root (replacing a file of the same
 * name), then read back and compared with its DDR copy. Nothing else on the card
 * is touched. A separate tool from hpdp_run, whose FatFs is read-only.
 */

#include <string.h>

#include "ff.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xil_types.h"

#define MANIFEST_ADDR 0x10000000u
#define RESULT_ADDR   0x1000F000u
#define MANIFEST_MAGIC 0x54504453u      /* "SDPT" */
#define RESULT_MAGIC   0x4B4F4453u      /* "SDOK" */
#define MAX_FILES     16
#define CHUNK         (1u << 20)        /* write and verify in 1 MB pieces */

typedef struct {
    char name[56];
    u32  addr, size;
} entry_t;

typedef struct {
    u32     magic, count;
    entry_t e[MAX_FILES];
} manifest_t;

typedef struct {
    u32 magic, status, files_ok, count;    /* status: 0 ok, 1 manifest, 2 mount, 3 a file */
    u32 fresult[MAX_FILES];                /* per file: FatFs result of the failing call, or 0 */
    u32 verified[MAX_FILES];               /* per file: 1 if the read-back matched */
} result_t;

static FATFS fs;
static FIL   fil;
static u8    rbuf[CHUNK] __attribute__((aligned(64)));

static int put(const entry_t *e, u32 *fres)
{
    const u8 *src = (const u8 *)(UINTPTR)e->addr;
    UINT n;
    FRESULT r;

    Xil_DCacheInvalidateRange((UINTPTR)e->addr, e->size);
    r = f_open(&fil, e->name, FA_CREATE_ALWAYS | FA_WRITE);
    if (r != FR_OK) { *fres = r; return 0; }
    for (u32 off = 0; off < e->size; off += CHUNK) {
        u32 len = e->size - off < CHUNK ? e->size - off : CHUNK;
        r = f_write(&fil, src + off, len, &n);
        if (r != FR_OK || n != len) { *fres = r ? r : 100; f_close(&fil); return 0; }
    }
    r = f_close(&fil);
    if (r != FR_OK) { *fres = r; return 0; }

    /* read back and compare */
    r = f_open(&fil, e->name, FA_READ);
    if (r != FR_OK) { *fres = r; return 0; }
    if (f_size(&fil) != e->size) { *fres = 101; f_close(&fil); return 0; }
    for (u32 off = 0; off < e->size; off += CHUNK) {
        u32 len = e->size - off < CHUNK ? e->size - off : CHUNK;
        r = f_read(&fil, rbuf, len, &n);
        if (r != FR_OK || n != len) { *fres = r ? r : 102; f_close(&fil); return 0; }
        if (memcmp(rbuf, src + off, len) != 0) { *fres = 103; f_close(&fil); return 0; }
    }
    f_close(&fil);
    return 1;
}

int main(void)
{
    manifest_t *m = (manifest_t *)MANIFEST_ADDR;
    result_t   *res = (result_t *)RESULT_ADDR;
    FRESULT     r;

    Xil_DCacheInvalidateRange(MANIFEST_ADDR, sizeof(*m));
    memset(res, 0, sizeof(*res));
    xil_printf("\r\nsd_put: writing files from DDR to the SD card\r\n");

    if (m->magic != MANIFEST_MAGIC || m->count == 0 || m->count > MAX_FILES) {
        xil_printf("sd_put: no valid manifest at 0x%08x\r\n", MANIFEST_ADDR);
        res->status = 1;
    } else if ((r = f_mount(&fs, "0:/", 1)) != FR_OK) {
        xil_printf("sd_put: SD card not mounted (FatFs error %d)\r\n", (int)r);
        res->status = 2;
    } else {
        res->count = m->count;
        for (u32 i = 0; i < m->count; i++) {
            m->e[i].name[sizeof(m->e[i].name) - 1] = '\0';
            int ok = put(&m->e[i], &res->fresult[i]);
            res->verified[i] = ok;
            res->files_ok += ok;
            xil_printf("  %s: %d bytes %s\r\n", m->e[i].name, (int)m->e[i].size,
                       ok ? "written and verified" : "FAILED");
            if (!ok)
                xil_printf("    FatFs / check error %d\r\n", (int)res->fresult[i]);
        }
        f_mount(NULL, "0:/", 0);
        res->status = res->files_ok == m->count ? 0 : 3;
    }
    res->magic = RESULT_MAGIC;
    Xil_DCacheFlushRange(RESULT_ADDR, sizeof(*res));
    xil_printf("sd_put: done, %d of %d files, status %d\r\n", (int)res->files_ok, (int)m->count,
               (int)res->status);
    for (;;)
        ;
}
