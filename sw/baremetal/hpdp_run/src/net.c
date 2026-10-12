/*
 * Ground record upload over the PS Gigabit Ethernet (SW-05), see net.h.
 *
 * lwIP raw API without an OS, as AMD's lwip_tcp_perf_server: a 50 ms
 * interval timer (xiltimer) only sets flags; the lwIP timers, the received
 * packets and the sending all run in net_poll(), from the main loop, so lwIP
 * is never entered from an interrupt.
 */
#include <string.h>
#include "xparameters.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "lwipopts.h"
#include "lwip/init.h"
#include "lwip/tcp.h"
#include "lwip/priv/tcp_priv.h"
#include "lwip/dhcp.h"
#include "lwip/ip_addr.h"
#include "netif/xadapter.h"
#if LWIP_DHCP_DOES_ACD_CHECK
#include "lwip/acd.h"
#endif
#ifndef SDT
#error "net.c needs the SDT flow (xiltimer interval timer for the lwIP timers)"
#endif
#include "xiltimer.h"
#include "xinterrupt_wrap.h"
#include "net.h"

#ifdef XPAR_XEMACPS_0_BASEADDR
#define EMAC_BASEADDR   XPAR_XEMACPS_0_BASEADDR
#else
#define EMAC_BASEADDR   XPAR_GEM3_BASEADDR          /* ZCU106: GEM3, RGMII to the DP83867 */
#endif
#define DHCP_TIMEOUT_MS 15000u
#define CHUNK           16384u                      /* bytes per tcp_write */

static struct netif nif;
static struct tcp_pcb *listener, *client;
static volatile net_status_t *const status = (net_status_t *)NET_ADDR;
static int started, start_result;

static volatile u32 ticks;                          /* 50 ms */
static volatile int fast_due, slow_due, dhcp_fine_due, dhcp_coarse_due, acd_due;

/* the stream */
static const u8 *base;
static const volatile u32 *avail;
static u32 sent, acked;
static int ending, closing, delivered;

/* ------------------------------------------------------------------------ */
static u64 ms_now(void)
{
    XTime t;
    XTime_GetTime(&t);
    return (u64)t * 1000u / COUNTS_PER_SECOND;
}

static void tick(void *ref, u32 counter)
{
    (void)ref;
    (void)counter;
    ticks++;
    if (ticks % 5 == 0)
        fast_due = 1;                               /* tcp_fasttmr: 250 ms */
    if (ticks % 10 == 0) {
        slow_due = 1;                               /* tcp_slowtmr: 500 ms */
        dhcp_fine_due = 1;                          /* dhcp_fine_tmr: 500 ms */
    }
    if (ticks % 1200 == 0)
        dhcp_coarse_due = 1;                        /* dhcp_coarse_tmr: 60 s */
    if (ticks % 2 == 0)
        acd_due = 1;                                /* acd_tmr: 100 ms */
}

static void publish(void)
{
    status->magic = NET_MAGIC;
    status->client = client != NULL;
    status->stream_bytes = avail ? *avail : 0;
    status->sent_bytes = sent;
    status->acked_bytes = acked;
    Xil_DCacheFlushRange((UINTPTR)status, sizeof(net_status_t));
}

static void set_state(u32 state)
{
    status->state = state;
    status->ip = lwip_ntohl(ip4_addr_get_u32(netif_ip4_addr(&nif)));
    publish();
}

static void print_ip(const char *what, u32 ip)
{
    xil_printf("%s%d.%d.%d.%d\r\n", what, (int)(ip >> 24), (int)(ip >> 16 & 255), (int)(ip >> 8 & 255),
               (int)(ip & 255));
}

/* ------------------------------------------------------------------------ */
/* Detach from the client's pcb and close it (lwIP finishes the close) */
static void close_client(void)
{
    struct tcp_pcb *pcb = client;

    client = NULL;
    closing = 0;
    if (pcb) {
        tcp_arg(pcb, NULL);
        tcp_sent(pcb, NULL);
        tcp_recv(pcb, NULL);
        tcp_err(pcb, NULL);
        if (tcp_close(pcb) != ERR_OK)
            tcp_abort(pcb);
    }
    publish();
}

static err_t on_sent(void *arg, struct tcp_pcb *pcb, u16_t len)
{
    (void)arg;
    (void)pcb;
    acked += len;
    return ERR_OK;
}

static err_t on_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err)
{
    (void)arg;
    (void)err;
    if (p == NULL) {                                /* the client closed */
        if (pcb == client)
            close_client();
        else
            tcp_close(pcb);
        return ERR_OK;
    }
    tcp_recved(pcb, p->tot_len);                    /* nothing is expected from the client */
    pbuf_free(p);
    return ERR_OK;
}

static void on_err(void *arg, err_t err)
{
    (void)arg;
    xil_printf("net: connection lost (%d)\r\n", (int)err);
    client = NULL;                                  /* lwIP has freed the pcb */
    closing = 0;
    publish();
}

static err_t on_accept(void *arg, struct tcp_pcb *pcb, err_t err)
{
    (void)arg;
    if (err != ERR_OK || pcb == NULL)
        return ERR_VAL;
    if (client != NULL) {                           /* one client at a time */
        tcp_abort(pcb);
        return ERR_ABRT;
    }
    client = pcb;
    tcp_nagle_disable(pcb);
    tcp_sent(pcb, on_sent);
    tcp_recv(pcb, on_recv);
    tcp_err(pcb, on_err);
    sent = acked = 0;                               /* each client gets the stream from the start */
    closing = 0;
    print_ip("net: client ", lwip_ntohl(ip4_addr_get_u32(ip_2_ip4(&pcb->remote_ip))));
    publish();
    return ERR_OK;
}

static void push(void)
{
    u32 ready;
    int wrote = 0;

    if (client == NULL || base == NULL || closing)
        return;
    ready = *avail;
    while (sent < ready) {
        u32 n = ready - sent, room = tcp_sndbuf(client);
        if (room < 1024u)
            break;
        if (n > room)
            n = room;
        if (n > CHUNK)
            n = CHUNK;
        if (n > 64u)
            n &= ~63u;                              /* whole cache lines */
        /* the ground DMA wrote this memory: drop any stale cache lines */
        Xil_DCacheInvalidateRange((UINTPTR)(base + sent), n);
        if (tcp_write(client, base + sent, (u16_t)n, TCP_WRITE_FLAG_COPY) != ERR_OK)
            break;
        sent += n;
        wrote = 1;
    }
    if (wrote)
        tcp_output(client);
    if (ending && sent == ready && acked >= ready) {
        /* everything delivered: closing tells the client the stream is complete */
        xil_printf("net: stream of %d bytes delivered, connection closed\r\n", (int)ready);
        delivered = 1;
        close_client();
    }
}

/* ------------------------------------------------------------------------ */
int net_start(u32 ip, u32 mask, u32 port)
{
    /* locally administered MAC address ("HPDP") */
    static unsigned char mac[6] = {0x02, 0x48, 0x50, 0x44, 0x50, 0x01};
    ip4_addr_t a, m, g;
    u64 t0;

    if (started)
        return start_result;
    started = 1;
    start_result = -1;
    memset((void *)status, 0, sizeof(net_status_t));
    status->port = port;
    set_state(NET_DOWN);

    XTimer_SetInterval(50);
    XTimer_SetHandler(tick, 0, XINTERRUPT_DEFAULT_PRIORITY);
    lwip_init();
    if (!xemac_add(&nif, NULL, NULL, NULL, mac, EMAC_BASEADDR)) {
        xil_printf("net: Ethernet interface not added\r\n");
        set_state(NET_FAILED);
        return -1;
    }
    netif_set_default(&nif);
    netif_set_up(&nif);

    if (ip == 0) {
#if LWIP_DHCP
        xil_printf("net: DHCP ...\r\n");
        set_state(NET_DHCP);
        dhcp_start(&nif);
        t0 = ms_now();
        while (ip4_addr_get_u32(netif_ip4_addr(&nif)) == 0 && ms_now() - t0 < DHCP_TIMEOUT_MS)
            net_poll();
        if (ip4_addr_get_u32(netif_ip4_addr(&nif)) == 0) {
            dhcp_stop(&nif);
            xil_printf("net: no DHCP answer, using the link-local address\r\n");
        }
#endif
        if (ip4_addr_get_u32(netif_ip4_addr(&nif)) == 0) {
            IP4_ADDR(&a, 169, 254, 77, 10);
            IP4_ADDR(&m, 255, 255, 0, 0);
            IP4_ADDR(&g, 0, 0, 0, 0);
            netif_set_addr(&nif, &a, &m, &g);
        }
    } else {
        ip4_addr_set_u32(&a, lwip_htonl(ip));
        ip4_addr_set_u32(&m, lwip_htonl(mask ? mask : 0xFFFFFF00u));
        IP4_ADDR(&g, 0, 0, 0, 0);
        netif_set_addr(&nif, &a, &m, &g);
    }

    listener = tcp_new_ip_type(IPADDR_TYPE_V4);
    if (listener == NULL || tcp_bind(listener, IP_ADDR_ANY, (u16_t)port) != ERR_OK) {
        xil_printf("net: cannot listen on port %d\r\n", (int)port);
        set_state(NET_FAILED);
        return -1;
    }
    listener = tcp_listen(listener);
    tcp_accept(listener, on_accept);
    set_state(NET_UP);
    print_ip("net: listening on ", status->ip);
    xil_printf("net: port %d\r\n", (int)port);
    start_result = 0;
    return 0;
}

void net_poll(void)
{
    static u64 last_publish;

    if (!started || status->state == NET_FAILED)
        return;
    if (fast_due) {
        fast_due = 0;
        tcp_fasttmr();
    }
    if (slow_due) {
        slow_due = 0;
        tcp_slowtmr();
    }
#if LWIP_DHCP
    if (dhcp_fine_due) {
        dhcp_fine_due = 0;
        dhcp_fine_tmr();
    }
    if (dhcp_coarse_due) {
        dhcp_coarse_due = 0;
        dhcp_coarse_tmr();
    }
#endif
#if LWIP_DHCP_DOES_ACD_CHECK
    if (acd_due) {
        acd_due = 0;
        acd_tmr();
    }
#endif
    xemacif_input(&nif);
    push();
    if (ms_now() - last_publish >= 100u) {
        last_publish = ms_now();
        publish();
    }
}

void net_stream(const u8 *b, const volatile u32 *a)
{
    base = b;
    avail = a;
    sent = acked = 0;
    ending = closing = delivered = 0;
    publish();
}

void net_stream_end(void)
{
    ending = 1;
}

int net_finish(u32 timeout_ms)
{
    u64 t = ms_now();
    u32 last = acked;

    if (!started || base == NULL)
        return UPLOAD_NONE;
    for (;;) {
        net_poll();
        if (delivered)
            return UPLOAD_COMPLETE;
        if (client == NULL)                         /* nobody connected, or the client left */
            return UPLOAD_NO_CLIENT;
        if (acked != last) {
            last = acked;
            t = ms_now();
        } else if (ms_now() - t > timeout_ms) {
            return UPLOAD_INCOMPLETE;
        }
    }
}

u32 net_ip(void)
{
    return started ? status->ip : 0;
}

u32 net_acked(void)
{
    return acked;
}
