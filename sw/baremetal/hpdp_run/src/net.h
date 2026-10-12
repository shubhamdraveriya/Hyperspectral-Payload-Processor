/*
 * Ground record upload over the PS Gigabit Ethernet (SPEC.md SW-05): lwIP raw
 * API, no OS. A TCP server sends each job's ground records (1024-byte capture
 * records, back to back, as in a .cap file) to one client, live while the
 * ground DMA fills the buffer and in full afterwards, then closes the
 * connection. The ground station (sw/ground/hpdp_gs.py decode --tcp) connects
 * to it, so the PC needs no open port.
 *
 * Address: a fixed one from the job, or DHCP (15 s) and else the link-local
 * address 169.254.77.10/16. The state goes to a status block in DDR (NET_ADDR)
 * for the host, and to the UART.
 */
#ifndef HPDP_NET_H
#define HPDP_NET_H

#include "xil_types.h"

#define NET_ADDR        0x10002000u     /* status block, see net_status_t */
#define NET_MAGIC       0x54454E48u     /* "HNET" */
#define NET_PORT        5001u

enum { NET_DOWN = 0, NET_DHCP, NET_UP, NET_FAILED };
enum { UPLOAD_NONE = 0, UPLOAD_COMPLETE, UPLOAD_NO_CLIENT, UPLOAD_INCOMPLETE };

typedef struct {
    u32 magic, state, ip, port;         /* ip: a.b.c.d in bits 31..0 */
    u32 client;                         /* 1 while a client is connected */
    u32 stream_bytes, sent_bytes, acked_bytes;
} net_status_t;

/* Brings the interface up (once; later calls return the first result).
   ip = 0: DHCP, then link-local. Returns 0 when the TCP server is listening. */
int net_start(u32 ip, u32 mask, u32 port);

/* Timers, received packets, and sending; call often (it never blocks long). */
void net_poll(void);

/* A new stream: the bytes at base, of which *avail are ready (grows as the
   ground DMA completes transfers). Sent from the start to the connected client
   (or to the next one that connects). */
void net_stream(const u8 *base, const volatile u32 *avail);

/* No more data for this stream: once everything is acknowledged the
   connection is closed. */
void net_stream_end(void);

/* After net_stream_end: wait for the client to receive everything, polling the
   network, at most timeout_ms without progress. Returns an UPLOAD_* status. */
int net_finish(u32 timeout_ms);

u32 net_ip(void);
u32 net_acked(void);

#endif
