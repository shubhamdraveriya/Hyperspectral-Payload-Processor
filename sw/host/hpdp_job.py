"""Job and result blocks of hpdp_run (sw/baremetal/hpdp_run/src/hpdp_job.h), for
the host tools (run_m4.py, run_m5.py). Little-endian u32 fields in this order."""

import struct

JOB_ADDR, RESULT_ADDR = 0x1000_0000, 0x1000_1000
CAL_ADDR, RAW_ADDR, OUT_ADDR, GR_ADDR = 0x1100_0000, 0x1200_0000, 0x3000_0000, 0x4000_0000
JOB_MAGIC, RESULT_MAGIC, JOB_VERSION, MAX_SCENES = 0x424F4A48, 0x53455248, 3, 64

JOB_LOAD_TABLE, JOB_NO_IDLE = 0x1, 0x2                  # flags
MODE_CAPTURE, MODE_DOWNLINK, MODE_PRBS = 0x1, 0x2, 0x4  # mode bits
NET_UPLOAD, NET_PORT = 0x1, 5001                        # net_ctrl: ground records over TCP (net.h)
UPLOAD_STATUS = ["not requested", "complete", "no client connected (served on request)", "incomplete"]

JOB_FIELDS = ("magic version seq flags nx ny nz depth scenes line_period source rc_enable "
              "cmp_pred cmp_wupd cmp_coder cmp_opts cal_addr cal_bytes raw_addr raw_bytes "
              "out_addr out_capacity "
              "mode pkt_apid pkt_data aos_frame_hdr aos_frame_gap "
              "ei_ctrl ei_threshold ei_seed ei_burst_len ei_period ei_asm_bits "
              "lnk_ctrl gr_sync gr_xfer gr_addr gr_capacity link_timeout_ms prbs_seconds "
              "net_ctrl net_ip net_mask net_port").split()

RESULT_FIELDS = ("magic version seq status build_id scenes_done timer_hz ticks_lo ticks_hi "
                 "se_lines se_underrun se_overruns se_dropped rc_clamped rc_replaced tbl_words "
                 "tbl_mismatch ct_lines ct_dropped ct_unfilled cmp_samples cmp_bytes "
                 "cmp_last_cycles cmp_stalls "
                 "lnk_status link_tries pkt_packets pkt_scenes pkt_ts_overflows "
                 "aos_science aos_idle aos_cadus ei_bits "
                 "gr_cadus gr_sync_losses gr_asm_errors gr_dropped gr_skipped gr_captured gr_bytes "
                 "lnk_code_errors lnk_lock_losses lnk_rx_words lnk_tx_data lnk_tx_idle "
                 "lnk_rx_overflows lnk_clk_corr lnk_buf_errors "
                 "prbs_errors prbs_words_lo prbs_words_hi prbs_ticks_lo prbs_ticks_hi prbs_status "
                 "downlink_ticks_lo downlink_ticks_hi "
                 "net_ip upload_status upload_bytes").split()
RESULT_WORDS = len(RESULT_FIELDS) + 2 * MAX_SCENES      # then scene_offset[], scene_bytes[]

STATUS = ["OK", "bad job", "HPDP not found", "compressor configuration error",
          "calibration table load failed", "DMA error", "timeout", "output buffer overflow",
          "link not locked", "ground buffer full"]

# result offset of a field, for the XSDB scripts
def result_offset(name):
    return 4 * RESULT_FIELDS.index(name)


def pack_job(**values):
    """Job block bytes; fields not given are 0 (magic and version filled in)."""
    values = {"magic": JOB_MAGIC, "version": JOB_VERSION, **values}
    unknown = set(values) - set(JOB_FIELDS)
    if unknown:
        raise KeyError(f"unknown job fields: {sorted(unknown)}")
    return struct.pack(f"<{len(JOB_FIELDS)}I", *(values.get(k, 0) & 0xFFFF_FFFF for k in JOB_FIELDS))


def pack_result(values, offsets=(), sizes=()):
    """Result block bytes (for --fake-board); fields not given are 0."""
    words = [values.get(k, 0) for k in RESULT_FIELDS]
    words += (list(offsets) + [0] * MAX_SCENES)[:MAX_SCENES] + (list(sizes) + [0] * MAX_SCENES)[:MAX_SCENES]
    return struct.pack(f"<{RESULT_WORDS}I", *words)


def unpack_result(data):
    """(fields dict, scene offsets, scene sizes) from result block bytes."""
    words = struct.unpack(f"<{RESULT_WORDS}I", data[:4 * RESULT_WORDS])
    n = len(RESULT_FIELDS)
    return dict(zip(RESULT_FIELDS, words[:n])), words[n:n + MAX_SCENES], words[n + MAX_SCENES:]


def status_text(st):
    return STATUS[st] if st < len(STATUS) else f"status {st}"
