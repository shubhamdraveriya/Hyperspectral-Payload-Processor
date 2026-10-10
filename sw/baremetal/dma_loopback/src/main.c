/*
 * M0 board test: AXI DMA loopback through the programmable logic.
 *
 * MM2S reads a buffer from PS DDR, the PL loops it back through an AXI-Stream
 * FIFO, and S2MM writes it into a second buffer. Every byte is checked and the
 * throughput reported. The last line printed on the UART (115200 8N1) is
 * "PASS: dma_loopback ..." or "FAIL: dma_loopback ...".
 */
#include <string.h>
#include "xparameters.h"
#include "xaxidma.h"
#include "xil_cache.h"
#include "xil_printf.h"
#ifdef SDT
#include "xiltimer.h"           /* Vitis 2023.2+ (system device tree flow) */
#else
#include "xtime_l.h"
#endif

#define TEST_BYTES  (4u * 1024u * 1024u)    /* per transfer; DMA length register is 26 bits */
#define ITERATIONS  16
#define TIMEOUT_US  1000000u

static u8 tx_buf[TEST_BYTES] __attribute__((aligned(64)));
static u8 rx_buf[TEST_BYTES] __attribute__((aligned(64)));

static XAxiDma dma;

/* Channel registers (PG021): MM2S at +0x00, S2MM at +0x30 */
#define MM2S_REG(off)  XAxiDma_ReadReg(dma.RegBase + XAXIDMA_TX_OFFSET, (off))
#define S2MM_REG(off)  XAxiDma_ReadReg(dma.RegBase + XAXIDMA_RX_OFFSET, (off))

/* xorshift32: a different pattern every iteration, so stale data cannot pass */
static void fill_pattern(u8 *buf, u32 len, u32 seed)
{
    u32 x = seed ? seed : 1;
    for (u32 i = 0; i < len; i += 4) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        memcpy(&buf[i], &x, 4);
    }
}

static int wait_idle(int direction)
{
    XTime start, now;
    XTime_GetTime(&start);
    while (XAxiDma_Busy(&dma, direction)) {
        XTime_GetTime(&now);
        if ((now - start) > (XTime)COUNTS_PER_SECOND * TIMEOUT_US / 1000000u)
            return XST_FAILURE;
    }
    return XST_SUCCESS;
}

static int init_dma(void)
{
    XAxiDma_Config *cfg;

#ifndef SDT
    cfg = XAxiDma_LookupConfig(XPAR_AXIDMA_0_DEVICE_ID);
#else
    cfg = XAxiDma_LookupConfig(XPAR_XAXIDMA_0_BASEADDR);
#endif
    if (!cfg) {
        xil_printf("no DMA configuration found\r\n");
        return XST_FAILURE;
    }
    if (XAxiDma_CfgInitialize(&dma, cfg) != XST_SUCCESS) {
        xil_printf("DMA initialization failed\r\n");
        return XST_FAILURE;
    }
    if (XAxiDma_HasSg(&dma)) {
        xil_printf("DMA is in scatter-gather mode, expected simple mode\r\n");
        return XST_FAILURE;
    }
    /* Polled operation */
    XAxiDma_IntrDisable(&dma, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DEVICE_TO_DMA);
    XAxiDma_IntrDisable(&dma, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DMA_TO_DEVICE);
    return XST_SUCCESS;
}

static int run_once(u32 seed, XTime *elapsed)
{
    XTime t0, t1;
    u32 rx_len, errors = 0;

    fill_pattern(tx_buf, TEST_BYTES, seed);
    memset(rx_buf, 0xA5, TEST_BYTES);
    Xil_DCacheFlushRange((UINTPTR)tx_buf, TEST_BYTES);
    Xil_DCacheFlushRange((UINTPTR)rx_buf, TEST_BYTES);

    XTime_GetTime(&t0);
    /* Receive side first, so the stream never waits on it */
    if (XAxiDma_SimpleTransfer(&dma, (UINTPTR)rx_buf, TEST_BYTES, XAXIDMA_DEVICE_TO_DMA) != XST_SUCCESS ||
        XAxiDma_SimpleTransfer(&dma, (UINTPTR)tx_buf, TEST_BYTES, XAXIDMA_DMA_TO_DEVICE) != XST_SUCCESS) {
        xil_printf("  transfer could not be started\r\n");
        return XST_FAILURE;
    }
    if (wait_idle(XAXIDMA_DMA_TO_DEVICE) != XST_SUCCESS || wait_idle(XAXIDMA_DEVICE_TO_DMA) != XST_SUCCESS) {
        xil_printf("  timeout waiting for the DMA\r\n");
        return XST_FAILURE;
    }
    XTime_GetTime(&t1);
    *elapsed = t1 - t0;

    if ((MM2S_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK) ||
        (S2MM_REG(XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK)) {
        xil_printf("  DMA reported an error (MM2S SR 0x%08x, S2MM SR 0x%08x)\r\n",
                   MM2S_REG(XAXIDMA_SR_OFFSET), S2MM_REG(XAXIDMA_SR_OFFSET));
        return XST_FAILURE;
    }
    rx_len = S2MM_REG(XAXIDMA_BUFFLEN_OFFSET);
    if (rx_len != TEST_BYTES) {
        xil_printf("  received %d bytes, expected %d\r\n", (int)rx_len, (int)TEST_BYTES);
        return XST_FAILURE;
    }

    Xil_DCacheInvalidateRange((UINTPTR)rx_buf, TEST_BYTES);
    for (u32 i = 0; i < TEST_BYTES; i++) {
        if (rx_buf[i] != tx_buf[i]) {
            if (errors < 8)
                xil_printf("  byte %d: got 0x%02x, expected 0x%02x\r\n", (int)i, rx_buf[i], tx_buf[i]);
            errors++;
        }
    }
    if (errors) {
        xil_printf("  %d mismatched bytes\r\n", (int)errors);
        return XST_FAILURE;
    }
    return XST_SUCCESS;
}

int main(void)
{
    XTime total = 0, elapsed;
    u64 bytes = 0;

    xil_printf("\r\nHPDP M0: AXI DMA loopback, %d x %d KB\r\n", ITERATIONS, (int)(TEST_BYTES / 1024));

    if (init_dma() != XST_SUCCESS) {
        xil_printf("FAIL: dma_loopback (initialization)\r\n");
        return 1;
    }

    for (int i = 0; i < ITERATIONS; i++) {
        if (run_once(0x1234567u + 7919u * (u32)i, &elapsed) != XST_SUCCESS) {
            xil_printf("FAIL: dma_loopback (iteration %d)\r\n", i);
            return 1;
        }
        total += elapsed;
        bytes += TEST_BYTES;
    }

    /* Each byte crosses the HP port twice (read and write) */
    u64 us = total * 1000000u / COUNTS_PER_SECOND;
    u32 mb_per_s = (u32)(bytes / (us ? us : 1));
    xil_printf("PASS: dma_loopback, %d MB looped back, %d MB/s per direction\r\n",
               (int)(bytes >> 20), (int)mb_per_s);
    return 0;
}
