`timescale 1ns / 1ps

// HPDP programmable-logic core:
//
//   s_axis_dma (MM2S) --+--> sensor_frontend --> scene_guard --> c123_core --+--> m_axis_cmp (S2MM, CAPTURE_CMP)
//                       '--> calibration table                |
//                                                             '--> space_packetizer --> aos_framer
//       --> rs_encoder --> cadu_framer --> error_injector --> link_layer TX --> GTH (gt_tx*)
//   GTH (gt_rx*) --> link_layer RX --> frame_sync --> gr_capture --> m_axis_gr (ground S2MM)
//
// SYS.MODE selects the compressor outputs (capture, downlink, both or none).
//
// Registers (regs/hpdp.rdl, PeakRDL) sit on clk_ctrl behind s_axil. The
// configuration reaches clk_sys, clk_sensor and clk_link, and status comes
// back, through cdc_bus snapshots; events (counter clears, table commands,
// seed loads) travel as sequence counts inside those snapshots. Each domain
// echoes the count of register writes it has received, so SYS.STATUS.cfg_pending
// tells software when a configuration has arrived everywhere. SYS.CTRL.soft_reset
// holds the clk_sys and clk_sensor data paths in reset (not the registers, the
// crossings or the link domain).
//
// clk_link is the GTH TX user clock (62.5 MHz; the RX data of both channels is
// on it too, through their elastic buffers). It runs only while the
// transceivers' TX is up: the link domain and the link side of link_layer are
// held in reset until gt_tx_done, and the link domain counts in cfg_pending only
// then. Both channels transmit the same words; LNK.CTRL.rx_sel picks the
// receiving channel. Index 0 = SFP0 (GTH X0Y10), 1 = SFP1 (X0Y11).
module hpdp_core
    import frontend_pkg::*;
    import c123_pkg::*;
    import ecc_pkg::*;
#(
    parameter int          NX_MAX   = 512,
    parameter int          NZ_MAX   = 256,
    parameter logic [31:0] BUILD_ID = 32'h0
) (
    input  logic         clk_ctrl,
    input  logic         rst_ctrl_n,
    input  logic         clk_sys,
    input  logic         rst_sys_n,
    input  logic         clk_sensor,
    input  logic         rst_sensor_n,
    input  logic         sensor_locked,     // clk_sensor MMCM locked (asynchronous)

    // AXI4-Lite control (clk_ctrl), 1 MB window
    input  logic         s_axil_awvalid,
    output logic         s_axil_awready,
    input  logic [19:0]  s_axil_awaddr,
    input  logic [2:0]   s_axil_awprot,
    input  logic         s_axil_wvalid,
    output logic         s_axil_wready,
    input  logic [31:0]  s_axil_wdata,
    input  logic [3:0]   s_axil_wstrb,
    output logic         s_axil_bvalid,
    input  logic         s_axil_bready,
    output logic [1:0]   s_axil_bresp,
    input  logic         s_axil_arvalid,
    output logic         s_axil_arready,
    input  logic [19:0]  s_axil_araddr,
    input  logic [2:0]   s_axil_arprot,
    output logic         s_axil_rvalid,
    input  logic         s_axil_rready,
    output logic [31:0]  s_axil_rdata,
    output logic [1:0]   s_axil_rresp,

    // DMA MM2S stream (clk_sys): raw samples or calibration records (SYS.DMA_ROUTE)
    input  logic [127:0] s_axis_dma_tdata,
    input  logic         s_axis_dma_tlast,
    input  logic         s_axis_dma_tvalid,
    output logic         s_axis_dma_tready,

    // Compressed output to DMA S2MM (clk_sys), one packet per scene
    output logic [63:0]  m_axis_cmp_tdata,
    output logic [7:0]   m_axis_cmp_tkeep,
    output logic         m_axis_cmp_tlast,
    output logic         m_axis_cmp_tvalid,
    input  logic         m_axis_cmp_tready,

    // Ground capture to the ground DMA S2MM (clk_sys), 1024-byte records
    output logic [63:0]  m_axis_gr_tdata,
    output logic [7:0]   m_axis_gr_tkeep,
    output logic         m_axis_gr_tlast,
    output logic         m_axis_gr_tvalid,
    input  logic         m_axis_gr_tready,

    // Transceivers: control and status outside the user clock
    input  logic         gt_tx_done,        // reset sequences done (asynchronous)
    input  logic         gt_rx_done,
    input  logic [1:0]   gt_cplllock,       // asynchronous
    input  logic [1:0]   gt_powergood,
    output logic         gt_reset_all,      // clk_ctrl, levels (the wizard synchronizes)
    output logic         gt_reset_rx,
    output logic [1:0]   sfp_tx_enable,     // clk_ctrl, static: TX_DISABLE_B

    // Transceivers: user interface (clk_link)
    input  logic         clk_link,
    output logic [15:0]  gt_txdata,         // both channels
    output logic [1:0]   gt_txcharisk,
    output logic [3:0]   gt_prbs_sel,       // both channels, TX and RX
    output logic         gt_prbs_force_err,
    output logic         gt_prbs_cnt_reset,
    output logic [2:0]   gt_loopback,
    output logic         gt_txpolarity,
    output logic         gt_rxpolarity,
    output logic [1:0]   gt_rxbufreset,
    input  logic [31:0]  gt_rxdata,         // per channel
    input  logic [3:0]   gt_rxcharisk,
    input  logic [3:0]   gt_rxdisperr,
    input  logic [3:0]   gt_rxnotintable,
    input  logic [1:0]   gt_rxbyteisaligned,
    input  logic [1:0]   gt_rxprbserr,
    input  logic [1:0]   gt_rxprbslocked,
    input  logic [3:0]   gt_rxclkcorcnt,
    input  logic [5:0]   gt_rxbufstatus,

    // Soft error mitigation controller (RAD-01, sem_support in hpdp_top), clk_sem
    input  logic         clk_sem,
    input  logic [9:0]   sem_status,        // heartbeat, initialization, observation,
                                            // correction, classification, injection,
                                            // diagnostic scan, detect only, essential,
                                            // uncorrectable
    input  logic         sem_cmd_busy,
    input  logic         sem_icap_avail,
    input  logic [7:0]   sem_mon_data,      // monitor interface: status report bytes
    input  logic         sem_mon_write,
    output logic         sem_cmd_strobe,
    output logic [43:0]  sem_cmd_code,
    output logic         sem_grant,

    output logic         irq                // level, clk_ctrl
);
    // ---------------------------------------------------------------------
    // Snapshot buses between the clock domains
    // ---------------------------------------------------------------------
    typedef struct packed {
        logic        downlink;
        logic [10:0] apid;
        logic [11:0] pkt_data;
        logic        idle_en;
        logic [7:0]  scid;
        logic [5:0]  vcid;
        logic [31:0] frame_gap;
        logic [1:0]  ei_mode;
        logic [31:0] ei_threshold, ei_seed, ei_period;
        logic [15:0] ei_burst_len;
        logic [5:0]  ei_asm_bits;
        logic [5:0]  asm_tol;
        logic [3:0]  flywheel;
        logic        gr_capture, gr_skip_idle;
        logic [15:0] gr_records;
    } dl_cfg_t;

    typedef struct packed {
        logic        soft_reset;
        logic        capture;
        logic        to_table;
        fe_cfg_t     fe;
        cfg_t        cmp;
        dl_cfg_t     dl;
        logic [7:0]  wr_seq;                    // register write count
        logic [7:0]  ct_clr_seq, cmp_clr_seq;
        logic [7:0]  pkt_clr_seq, aos_clr_seq, ei_clr_seq, gr_clr_seq, ei_seed_seq;
        logic [7:0]  tbl_seq;                   // table command count
        logic        tbl_write;
        logic [17:0] tbl_addr;
        logic [32:0] tbl_wdata;
        logic [7:0]  rad_clr_seq, inj_seq;      // ECC: counter clears, injections
        logic [1:0]  inj_mem;
        logic        inj_dbe;
        logic [19:0] inj_addr;
        logic [31:0] wd_timeout;                // pipeline watchdogs (RAD-05)
    } sys_cfg_t;

    typedef struct packed {
        logic        soft_reset;
        fe_cfg_t     fe;
        logic [7:0]  wr_seq;
        logic [7:0]  se_clr_seq;
    } sen_cfg_t;

    typedef struct packed {
        logic        fabric_lb, rx_sel;
        logic [2:0]  loopback;
        logic        txpol, rxpol;
        logic [3:0]  prbs_sel;
        logic [15:0] cc_interval;
        logic [7:0]  wr_seq, clr_seq, inj_seq;
    } lnk_cfg_t;

    typedef struct packed {
        se_status_t  se;
        logic [7:0]  wr_seq_echo;
    } sen_stat_t;

    typedef struct packed {
        logic [31:0] packets, scenes, ts_overflows;
        logic [31:0] science_frames, idle_frames, cadus;
        logic [31:0] injected_bits;
        logic [1:0]  sync_state;
        logic [31:0] gr_cadus, sync_losses, asm_bit_errors, dropped_records, skipped, captured;
    } dl_stat_t;

    typedef struct packed {
        fe_status_t  fe;
        logic [7:0]  wr_seq_echo;
        logic [32:0] tbl_rdata;
        logic [7:0]  tbl_done_seq;              // count of the last completed table command
        logic        cmp_busy, cmp_cfg_error;
        logic [31:0] scenes_done, samples_in, bytes_out, last_bytes, last_cycles, out_stalls;
        logic [31:0] scenes_padded, samples_padded;
        dl_stat_t    dl;
    } sys_stat_t;

    typedef struct packed {                     // memory ECC (RAD-02), clk_sys
        logic [2:0]  inj_pending;               // table, corner turn, line buffer
        logic [2:0][1:0][31:0] ecc_count;       // [memory][sbe, dbe]
        logic [2:0]  wd_stalled;                // compressor, downlink, ground capture
        logic [2:0][31:0] wd_count;
        logic [31:0] w_sbe, w_dbe, tmr_cmp;      // compressor state (RAD-03)
        logic [31:0] tmr_fsm;                    // control state machines (RAD-04)
    } rad_stat_t;

    typedef struct packed {
        logic [7:0]  wr_seq_echo;
        logic        locked, aligned, prbs_locked;
        logic [31:0] code_errors, lock_losses, rx_words, tx_data_words, tx_idle_words, rx_overflows;
        logic [31:0] clk_corrections, buf_errors, prbs_errors;
        logic [47:0] prbs_words;
    } lnk_stat_t;

    // ---------------------------------------------------------------------
    // Registers (clk_ctrl)
    // ---------------------------------------------------------------------
    wire rst_ctrl = !rst_ctrl_n;

    hpdp_regs_pkg::hpdp_regs__in_t  hwif_in;
    hpdp_regs_pkg::hpdp_regs__out_t hwif_out;

    // three voted copies (RAD-03); regs_mm while they disagree
    logic        regs_mm, regs_mm_d;
    logic [31:0] tmr_regs_count;

    hpdp_regs_tmr u_regs (
        .clk (clk_ctrl),
        .rst (rst_ctrl),
        .s_axil_awready, .s_axil_awvalid, .s_axil_awaddr, .s_axil_awprot,
        .s_axil_wready, .s_axil_wvalid, .s_axil_wdata, .s_axil_wstrb,
        .s_axil_bready, .s_axil_bvalid, .s_axil_bresp,
        .s_axil_arready, .s_axil_arvalid, .s_axil_araddr, .s_axil_arprot,
        .s_axil_rready, .s_axil_rvalid, .s_axil_rdata, .s_axil_rresp,
        .hwif_in, .hwif_out,
        .mismatch (regs_mm)
    );

    always_ff @(posedge clk_ctrl) begin
        if (rst_ctrl || hwif_out.rad.CNT_CLEAR.clear.value) begin
            regs_mm_d      <= 1'b0;
            tmr_regs_count <= '0;
        end else begin
            regs_mm_d <= regs_mm;
            if (regs_mm && !regs_mm_d) tmr_regs_count <= sat_inc(tmr_regs_count);
        end
    end

    // Event counts (clk_ctrl)
    logic [7:0] wr_seq, se_clr_seq, ct_clr_seq, cmp_clr_seq, tbl_seq;
    logic [7:0] pkt_clr_seq, aos_clr_seq, ei_clr_seq, gr_clr_seq, ei_seed_seq, lnk_clr_seq, lnk_inj_seq;
    logic [7:0] rad_clr_seq, inj_seq;
    logic       tbl_write;

    always_ff @(posedge clk_ctrl) begin
        if (rst_ctrl) begin
            {wr_seq, se_clr_seq, ct_clr_seq, cmp_clr_seq, tbl_seq} <= '0;
            {pkt_clr_seq, aos_clr_seq, ei_clr_seq, gr_clr_seq, ei_seed_seq, lnk_clr_seq, lnk_inj_seq} <= '0;
            {rad_clr_seq, inj_seq} <= '0;
            tbl_write <= 1'b0;
        end else begin
            if (s_axil_bvalid && s_axil_bready) wr_seq <= wr_seq + 8'd1;
            if (hwif_out.se.CNT_CLEAR.clear.value)  se_clr_seq  <= se_clr_seq + 8'd1;
            if (hwif_out.ct.CNT_CLEAR.clear.value)  ct_clr_seq  <= ct_clr_seq + 8'd1;
            if (hwif_out.cmp.CNT_CLEAR.clear.value) cmp_clr_seq <= cmp_clr_seq + 8'd1;
            if (hwif_out.pkt.CNT_CLEAR.clear.value) pkt_clr_seq <= pkt_clr_seq + 8'd1;
            if (hwif_out.aos.CNT_CLEAR.clear.value) aos_clr_seq <= aos_clr_seq + 8'd1;
            if (hwif_out.ei.CNT_CLEAR.clear.value)  ei_clr_seq  <= ei_clr_seq + 8'd1;
            if (hwif_out.gr.CNT_CLEAR.clear.value)  gr_clr_seq  <= gr_clr_seq + 8'd1;
            if (hwif_out.lnk.CNT_CLEAR.clear.value) lnk_clr_seq <= lnk_clr_seq + 8'd1;
            if (hwif_out.ei.SEED_LOAD.load.value)   ei_seed_seq <= ei_seed_seq + 8'd1;
            if (hwif_out.lnk.PRBS_INJECT.inject.value) lnk_inj_seq <= lnk_inj_seq + 8'd1;
            if (hwif_out.rad.CNT_CLEAR.clear.value) rad_clr_seq <= rad_clr_seq + 8'd1;
            if (hwif_out.rad.ECC_INJECT.go.value)   inj_seq     <= inj_seq + 8'd1;
            if (hwif_out.rc.TBL_CMD.write.value || hwif_out.rc.TBL_CMD.read.value) begin
                tbl_seq   <= tbl_seq + 8'd1;
                tbl_write <= hwif_out.rc.TBL_CMD.write.value;
            end
        end
    end

    fe_cfg_t fe_ctrl;
    cfg_t    cmp_ctrl;
    dl_cfg_t dl_ctrl;
    always_comb begin
        fe_ctrl.nx          = hwif_out.sys.NX.value.value;
        fe_ctrl.ny          = hwif_out.sys.NY.value.value;
        fe_ctrl.nz          = hwif_out.sys.NZ.value.value;
        fe_ctrl.depth       = hwif_out.sys.DEPTH.depth.value;
        fe_ctrl.line_period = hwif_out.se.LINE_PERIOD.cycles.value;
        fe_ctrl.lines       = hwif_out.se.LINES.lines.value;
        fe_ctrl.source      = hwif_out.se.SOURCE.source.value;
        fe_ctrl.run         = hwif_out.se.CTRL.run.value;
        fe_ctrl.rc_bypass   = !hwif_out.rc.CTRL.enable.value;

        cmp_ctrl.nx         = hwif_out.sys.NX.value.value;
        cmp_ctrl.ny         = hwif_out.sys.NY.value.value;
        cmp_ctrl.nz         = hwif_out.sys.NZ.value.value;
        cmp_ctrl.depth      = hwif_out.sys.DEPTH.depth.value;
        cmp_ctrl.bands      = hwif_out.cmp.PRED.bands.value;
        cmp_ctrl.full       = hwif_out.cmp.PRED.full.value;
        cmp_ctrl.local_sum  = hwif_out.cmp.PRED.local_sum.value;
        cmp_ctrl.omega      = hwif_out.cmp.PRED.omega.value;
        cmp_ctrl.reg_size   = hwif_out.cmp.PRED.reg_size.value;
        cmp_ctrl.v_min      = hwif_out.cmp.WUPD.v_min.value;
        cmp_ctrl.v_max      = hwif_out.cmp.WUPD.v_max.value;
        cmp_ctrl.t_inc_log2 = hwif_out.cmp.WUPD.t_inc_log2.value;
        cmp_ctrl.u_max      = hwif_out.cmp.CODER.u_max.value;
        cmp_ctrl.gamma0     = hwif_out.cmp.CODER.gamma0.value;
        cmp_ctrl.gamma_star = hwif_out.cmp.CODER.gamma_star.value;
        cmp_ctrl.k_init     = hwif_out.cmp.CODER.k_init.value;
        cmp_ctrl.word_bytes = hwif_out.cmp.CODER.word_bytes.value;
        cmp_ctrl.raw        = hwif_out.cmp.OPTS.raw.value;
        cmp_ctrl.user_data  = hwif_out.cmp.OPTS.user_data.value;

        dl_ctrl.downlink     = hwif_out.sys.MODE.downlink.value;
        dl_ctrl.apid         = hwif_out.pkt.APID.apid.value;
        dl_ctrl.pkt_data     = hwif_out.pkt.PKT_DATA.bytes.value;
        dl_ctrl.idle_en      = hwif_out.aos.CTRL.idle_en.value;
        dl_ctrl.scid         = hwif_out.aos.FRAME_HDR.scid.value;
        dl_ctrl.vcid         = hwif_out.aos.FRAME_HDR.vcid.value;
        dl_ctrl.frame_gap    = hwif_out.aos.FRAME_GAP.cycles.value;
        dl_ctrl.ei_mode      = hwif_out.ei.CTRL.mode.value;
        dl_ctrl.ei_threshold = hwif_out.ei.THRESHOLD.value.value;
        dl_ctrl.ei_seed      = hwif_out.ei.SEED.value.value;
        dl_ctrl.ei_period    = hwif_out.ei.PERIOD.value.value;
        dl_ctrl.ei_burst_len = hwif_out.ei.BURST_LEN.bits.value;
        dl_ctrl.ei_asm_bits  = hwif_out.ei.ASM_BITS.bits.value;
        dl_ctrl.asm_tol      = hwif_out.gr.SYNC.asm_tol.value;
        dl_ctrl.flywheel     = hwif_out.gr.SYNC.flywheel.value;
        dl_ctrl.gr_capture   = hwif_out.gr.CTRL.capture.value;
        dl_ctrl.gr_skip_idle = hwif_out.gr.CTRL.skip_idle.value;
        dl_ctrl.gr_records   = hwif_out.gr.XFER.records.value;
    end

    sys_cfg_t  sys_cfg_src, sys_cfg;        // clk_ctrl -> clk_sys
    sen_cfg_t  sen_cfg_src, sen_cfg;        // clk_ctrl -> clk_sensor
    lnk_cfg_t  lnk_cfg_src, lnk_cfg;        // clk_ctrl -> clk_link
    sys_stat_t sys_stat_src, sys_stat;      // clk_sys -> clk_ctrl
    rad_stat_t rad_stat_src, rad_stat;      // clk_sys -> clk_ctrl
    sen_stat_t sen_stat_src, sen_stat;      // clk_sensor -> clk_ctrl
    lnk_stat_t lnk_stat_src, lnk_stat;      // clk_link -> clk_ctrl
    se_status_t se_stat;
    dl_stat_t   dl_stat;
    assign se_stat = sen_stat.se;
    assign dl_stat = sys_stat.dl;

    always_comb begin
        sys_cfg_src.soft_reset  = hwif_out.sys.CTRL.soft_reset.value;
        sys_cfg_src.capture     = hwif_out.sys.MODE.capture.value;
        sys_cfg_src.to_table    = hwif_out.sys.DMA_ROUTE.to_table.value;
        sys_cfg_src.fe          = fe_ctrl;
        sys_cfg_src.cmp         = cmp_ctrl;
        sys_cfg_src.dl          = dl_ctrl;
        sys_cfg_src.wr_seq      = wr_seq;
        sys_cfg_src.ct_clr_seq  = ct_clr_seq;
        sys_cfg_src.cmp_clr_seq = cmp_clr_seq;
        sys_cfg_src.pkt_clr_seq = pkt_clr_seq;
        sys_cfg_src.aos_clr_seq = aos_clr_seq;
        sys_cfg_src.ei_clr_seq  = ei_clr_seq;
        sys_cfg_src.gr_clr_seq  = gr_clr_seq;
        sys_cfg_src.ei_seed_seq = ei_seed_seq;
        sys_cfg_src.tbl_seq     = tbl_seq;
        sys_cfg_src.tbl_write   = tbl_write;
        sys_cfg_src.tbl_addr    = hwif_out.rc.TBL_ADDR.addr.value;
        sys_cfg_src.tbl_wdata   = {hwif_out.rc.TBL_WDATA_HI.bad.value,
                                   hwif_out.rc.TBL_WDATA_LO.gain.value,
                                   hwif_out.rc.TBL_WDATA_LO.dark.value};
        sys_cfg_src.rad_clr_seq = rad_clr_seq;
        sys_cfg_src.inj_seq     = inj_seq;
        sys_cfg_src.inj_mem     = hwif_out.rad.ECC_INJECT.memory.value;
        sys_cfg_src.inj_dbe     = hwif_out.rad.ECC_INJECT.dbe.value;
        sys_cfg_src.inj_addr    = hwif_out.rad.ECC_INJECT_ADDR.addr.value;
        sys_cfg_src.wd_timeout  = hwif_out.rad.WD_TIMEOUT.cycles.value;

        sen_cfg_src.soft_reset  = hwif_out.sys.CTRL.soft_reset.value;
        sen_cfg_src.fe          = fe_ctrl;
        sen_cfg_src.wr_seq      = wr_seq;
        sen_cfg_src.se_clr_seq  = se_clr_seq;

        lnk_cfg_src.fabric_lb   = hwif_out.lnk.CTRL.fabric_loopback.value;
        lnk_cfg_src.rx_sel      = hwif_out.lnk.CTRL.rx_sel.value;
        lnk_cfg_src.loopback    = hwif_out.lnk.CTRL.gt_loopback.value;
        lnk_cfg_src.txpol       = hwif_out.lnk.CTRL.tx_polarity.value;
        lnk_cfg_src.rxpol       = hwif_out.lnk.CTRL.rx_polarity.value;
        lnk_cfg_src.prbs_sel    = hwif_out.lnk.CTRL.prbs_sel.value;
        lnk_cfg_src.cc_interval = hwif_out.lnk.CC_INTERVAL.words.value;
        lnk_cfg_src.wr_seq      = wr_seq;
        lnk_cfg_src.clr_seq     = lnk_clr_seq;
        lnk_cfg_src.inj_seq     = lnk_inj_seq;
    end

    wire rst_sys_hard    = !rst_sys_n;
    wire rst_sensor_hard = !rst_sensor_n;

    // Transceiver state in clk_ctrl; the link domain is in reset while TX is not up
    logic       tx_done_ctrl, rx_done_ctrl;
    logic [1:0] cpll_ctrl, pgood_ctrl;
    logic       lnk_down;                   // clk_ctrl register: source of the link resets
    logic       rst_link, lnk_down_sys;

    xpm_cdc_array_single #(.DEST_SYNC_FF(2), .SRC_INPUT_REG(0), .WIDTH(6)) u_cdc_gt_status (
        .src_clk (1'b0), .src_in ({gt_tx_done, gt_rx_done, gt_cplllock, gt_powergood}),
        .dest_clk (clk_ctrl), .dest_out ({tx_done_ctrl, rx_done_ctrl, cpll_ctrl, pgood_ctrl}));

    always_ff @(posedge clk_ctrl) lnk_down <= rst_ctrl || !tx_done_ctrl;

    xpm_cdc_async_rst #(.DEST_SYNC_FF(4), .RST_ACTIVE_HIGH(1)) u_cdc_rst_link (
        .src_arst (lnk_down), .dest_clk (clk_link), .dest_arst (rst_link));

    xpm_cdc_single #(.DEST_SYNC_FF(2), .SRC_INPUT_REG(0)) u_cdc_lnk_down (
        .src_clk (1'b0), .src_in (lnk_down), .dest_clk (clk_sys), .dest_out (lnk_down_sys));

    cdc_bus #(.WIDTH($bits(sys_cfg_t))) u_cdc_sys_cfg (
        .src_clk (clk_ctrl), .src_rst (rst_ctrl), .src_data (sys_cfg_src),
        .dest_clk (clk_sys), .dest_rst (rst_sys_hard), .dest_data (sys_cfg));

    cdc_bus #(.WIDTH($bits(sen_cfg_t))) u_cdc_sen_cfg (
        .src_clk (clk_ctrl), .src_rst (rst_ctrl), .src_data (sen_cfg_src),
        .dest_clk (clk_sensor), .dest_rst (rst_sensor_hard), .dest_data (sen_cfg));

    cdc_bus #(.WIDTH($bits(lnk_cfg_t))) u_cdc_lnk_cfg (
        .src_clk (clk_ctrl), .src_rst (rst_ctrl), .src_data (lnk_cfg_src),
        .dest_clk (clk_link), .dest_rst (rst_link), .dest_data (lnk_cfg));

    cdc_bus #(.WIDTH($bits(sys_stat_t))) u_cdc_sys_stat (
        .src_clk (clk_sys), .src_rst (rst_sys_hard), .src_data (sys_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (sys_stat));

    cdc_bus #(.WIDTH($bits(rad_stat_t))) u_cdc_rad_stat (
        .src_clk (clk_sys), .src_rst (rst_sys_hard), .src_data (rad_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (rad_stat));

    cdc_bus #(.WIDTH($bits(sen_stat_t))) u_cdc_sen_stat (
        .src_clk (clk_sensor), .src_rst (rst_sensor_hard), .src_data (sen_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (sen_stat));

    cdc_bus #(.WIDTH($bits(lnk_stat_t))) u_cdc_lnk_stat (
        .src_clk (clk_link), .src_rst (rst_link), .src_data (lnk_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (lnk_stat));

    logic locked_ctrl;
    xpm_cdc_single #(.DEST_SYNC_FF(2), .SRC_INPUT_REG(0)) u_cdc_locked (
        .src_clk (1'b0), .src_in (sensor_locked), .dest_clk (clk_ctrl), .dest_out (locked_ctrl));

    // ---------------------------------------------------------------------
    // Data path resets: hard reset or SYS.CTRL.soft_reset
    // ---------------------------------------------------------------------
    logic rst_sys, rst_sensor, rst_link_sys;
    always_ff @(posedge clk_sys)    rst_sys      <= rst_sys_hard || sys_cfg.soft_reset;
    always_ff @(posedge clk_sys)    rst_link_sys <= rst_sys_hard || sys_cfg.soft_reset || lnk_down_sys;
    always_ff @(posedge clk_sensor) rst_sensor   <= rst_sensor_hard || sen_cfg.soft_reset;

    // Event counts arriving in the data path clocks -> one-cycle pulses
    logic [7:0] se_clr_seen, ct_clr_seen, cmp_clr_seen, tbl_seen;
    logic [7:0] pkt_clr_seen, aos_clr_seen, ei_clr_seen, gr_clr_seen, ei_seed_seen;
    logic [7:0] rad_clr_seen, inj_seen;
    logic       se_clr, ct_clr, cmp_clr, pkt_clr, aos_clr, ei_clr, gr_clr, ei_seed, rad_clr;
    ecc_inj_t   inj [3];                    // ECC injection: table, corner turn, line buffer

    assign sen_stat_src.wr_seq_echo = sen_cfg.wr_seq;

    always_ff @(posedge clk_sensor) begin
        if (rst_sensor_hard) begin
            se_clr_seen <= '0;
            se_clr      <= 1'b0;
        end else begin
            se_clr      <= (sen_cfg.se_clr_seq != se_clr_seen);
            se_clr_seen <= sen_cfg.se_clr_seq;
        end
    end

    // ---------------------------------------------------------------------
    // Calibration table commands and clk_sys events
    // ---------------------------------------------------------------------
    tbl_cmd_t    tbl_cmd;
    logic [32:0] tbl_rdata;
    logic        tbl_done;
    logic [7:0]  tbl_done_seq;

    always_ff @(posedge clk_sys) begin
        if (rst_sys_hard) begin
            {ct_clr_seen, cmp_clr_seen, tbl_seen, tbl_done_seq} <= '0;
            {pkt_clr_seen, aos_clr_seen, ei_clr_seen, gr_clr_seen, ei_seed_seen} <= '0;
            {ct_clr, cmp_clr, pkt_clr, aos_clr, ei_clr, gr_clr, ei_seed} <= '0;
            {rad_clr_seen, inj_seen, rad_clr} <= '0;
            for (int m = 0; m < 3; m++) inj[m] <= '0;
            tbl_cmd.valid <= 1'b0;
        end else begin
            ct_clr       <= (sys_cfg.ct_clr_seq != ct_clr_seen);
            ct_clr_seen  <= sys_cfg.ct_clr_seq;
            cmp_clr      <= (sys_cfg.cmp_clr_seq != cmp_clr_seen);
            cmp_clr_seen <= sys_cfg.cmp_clr_seq;
            pkt_clr      <= (sys_cfg.pkt_clr_seq != pkt_clr_seen);
            pkt_clr_seen <= sys_cfg.pkt_clr_seq;
            aos_clr      <= (sys_cfg.aos_clr_seq != aos_clr_seen);
            aos_clr_seen <= sys_cfg.aos_clr_seq;
            ei_clr       <= (sys_cfg.ei_clr_seq != ei_clr_seen);
            ei_clr_seen  <= sys_cfg.ei_clr_seq;
            gr_clr       <= (sys_cfg.gr_clr_seq != gr_clr_seen);
            gr_clr_seen  <= sys_cfg.gr_clr_seq;
            ei_seed      <= (sys_cfg.ei_seed_seq != ei_seed_seen);
            ei_seed_seen <= sys_cfg.ei_seed_seq;
            rad_clr      <= (sys_cfg.rad_clr_seq != rad_clr_seen);
            rad_clr_seen <= sys_cfg.rad_clr_seq;
            for (int m = 0; m < 3; m++)
                inj[m] <= '{arm:  sys_cfg.inj_seq != inj_seen && sys_cfg.inj_mem == 2'(m),
                            dbe:  sys_cfg.inj_dbe,
                            addr: sys_cfg.inj_addr};
            inj_seen     <= sys_cfg.inj_seq;

            tbl_cmd.valid <= (sys_cfg.tbl_seq != tbl_seen);
            tbl_cmd.write <= sys_cfg.tbl_write;
            tbl_cmd.addr  <= sys_cfg.tbl_addr;
            tbl_cmd.wdata <= sys_cfg.tbl_wdata;
            tbl_seen      <= sys_cfg.tbl_seq;
            if (tbl_done) tbl_done_seq <= tbl_seen;
        end
    end

    // ---------------------------------------------------------------------
    // Sensor front end
    // ---------------------------------------------------------------------
    wire to_table = sys_cfg.to_table;

    logic        se_dma_tready;
    logic [15:0] fe_tdata;
    logic [1:0]  fe_tuser;
    logic        fe_tlast, fe_tvalid, fe_tready;
    fe_status_t  fe_stat;
    logic [23:0] fe_scene_ts;
    logic        fe_ue, fe_fill;
    ecc_evt_t    ecc [3];                   // ECC events: table, corner turn, line buffer
    logic [4:0]  fsm_mm;                    // RAD-04: corner turn, scene guard, packer, packetizer, AOS

    assign s_axis_dma_tready = to_table ? 1'b1 : se_dma_tready;

    sensor_frontend #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_fe (
        .clk_sys        (clk_sys),
        .rst_sys        (rst_sys),
        .cfg_sys        (sys_cfg.fe),
        .cnt_clr_sys    (ct_clr),
        .clk_sensor     (clk_sensor),
        .rst_sensor     (rst_sensor),
        .cfg_sensor     (sen_cfg.fe),
        .cnt_clr_sensor (se_clr),
        .s_dma_tdata    (s_axis_dma_tdata),
        .s_dma_tvalid   (s_axis_dma_tvalid && !to_table),
        .s_dma_tready   (se_dma_tready),
        .s_tbl_tdata    (s_axis_dma_tdata),
        .s_tbl_tlast    (s_axis_dma_tlast),
        .s_tbl_tvalid   (s_axis_dma_tvalid && to_table),
        .s_tbl_tready   (),
        .tbl_cmd, .tbl_rdata, .tbl_done,
        .tbl_inj        (inj[0]),
        .tbl_ecc        (ecc[0]),
        .ct_inj         (inj[1]),
        .ct_ecc         (ecc[1]),
        .ct_tmr_mismatch (fsm_mm[0]),
        .m_axis_tdata   (fe_tdata),
        .m_axis_tuser   (fe_tuser),
        .m_axis_tlast   (fe_tlast),
        .m_axis_tvalid  (fe_tvalid),
        .m_axis_tready  (fe_tready),
        .se_status      (sen_stat_src.se),
        .fe_status      (fe_stat),
        .m_axis_scene_ts (fe_scene_ts),
        .m_axis_ue      (fe_ue),
        .m_axis_fill    (fe_fill)
    );

    // ---------------------------------------------------------------------
    // Scene guard: a scene cut short by unfilled lines is completed with
    // zero samples, so the compressor ends it and the framing recovers
    // ---------------------------------------------------------------------
    logic [15:0] sg_tdata;
    logic [1:0]  sg_tuser;
    logic [23:0] sg_scene_ts;
    logic        sg_ue, sg_fill;
    logic        sg_tlast, sg_tvalid, sg_tready;
    logic [31:0] scenes_padded, samples_padded;

    scene_guard u_guard (
        .clk (clk_sys), .rst (rst_sys),
        .nx (sys_cfg.fe.nx), .ny (sys_cfg.fe.ny), .nz (sys_cfg.fe.nz), .cnt_clr (ct_clr),
        .s_tdata (fe_tdata), .s_tuser (fe_tuser), .s_scene_ts (fe_scene_ts), .s_ue (fe_ue),
        .s_fill (fe_fill), .s_tlast (fe_tlast), .s_tvalid (fe_tvalid), .s_tready (fe_tready),
        .m_tdata (sg_tdata), .m_tuser (sg_tuser), .m_scene_ts (sg_scene_ts), .m_ue (sg_ue),
        .m_fill (sg_fill), .m_tlast (sg_tlast),
        .m_tvalid (sg_tvalid), .m_tready (sg_tready),
        .padded_scenes (scenes_padded), .padded_samples (samples_padded),
        .tmr_mismatch (fsm_mm[1])
    );

    // ---------------------------------------------------------------------
    // Compressor and its statistics (CMP-09)
    // ---------------------------------------------------------------------
    logic [63:0] c_tdata;
    logic [7:0]  c_tkeep;
    logic        c_tlast, c_tvalid, c_tready, cfg_error;

    logic w_sbe, w_dbe, cmp_tmr_mm;         // compressor state events (RAD-03)

    c123_core #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_cmp (
        .clk           (clk_sys),
        .rst           (rst_sys),
        .cfg           (sys_cfg.cmp),
        .s_axis_tdata  (sg_tdata),
        .s_axis_tuser  (sg_tuser[0]),
        .s_axis_tvalid (sg_tvalid),
        .s_axis_tready (sg_tready),
        .m_axis_tdata  (c_tdata),
        .m_axis_tkeep  (c_tkeep),
        .m_axis_tlast  (c_tlast),
        .m_axis_tvalid (c_tvalid),
        .m_axis_tready (c_tready),
        .cfg_error     (cfg_error),
        .lb_inj        (inj[2]),
        .lb_ecc        (ecc[2]),
        .w_sbe         (w_sbe),
        .w_dbe         (w_dbe),
        .tmr_mismatch  (cmp_tmr_mm),
        .fsm_mismatch  (fsm_mm[2])
    );

    // ---------------------------------------------------------------------
    // Memory ECC counters (RAD-02): reads that returned a corrected word,
    // reads that returned an uncorrectable word
    // ---------------------------------------------------------------------
    logic [2:0][1:0][31:0] ecc_count;
    logic [31:0] w_sbe_count, w_dbe_count, tmr_cmp_count;      // compressor state (RAD-03)
    logic [31:0] tmr_fsm_count;                                // control state machines (RAD-04)
    logic        fsm_mm_d;

    always_ff @(posedge clk_sys) begin
        if (rst_sys || rad_clr) begin
            {tmr_fsm_count, fsm_mm_d} <= '0;
        end else begin
            fsm_mm_d <= |fsm_mm;
            if (|fsm_mm && !fsm_mm_d) tmr_fsm_count <= sat_inc(tmr_fsm_count);
        end
    end

    always_ff @(posedge clk_sys) begin
        if (rst_sys || rad_clr) begin
            {w_sbe_count, w_dbe_count, tmr_cmp_count} <= '0;
        end else begin
            if (w_sbe)      w_sbe_count   <= sat_inc(w_sbe_count);
            if (w_dbe)      w_dbe_count   <= sat_inc(w_dbe_count);
            if (cmp_tmr_mm) tmr_cmp_count <= sat_inc(tmr_cmp_count);
        end
    end

    always_ff @(posedge clk_sys) begin
        if (rst_sys || rad_clr) begin
            ecc_count <= '0;
        end else begin
            for (int m = 0; m < 3; m++) begin
                if (ecc[m].sbe) ecc_count[m][0] <= sat_inc(ecc_count[m][0]);
                if (ecc[m].dbe) ecc_count[m][1] <= sat_inc(ecc_count[m][1]);
            end
        end
    end

    // Fork to the capture DMA and the downlink chain: each enabled output takes
    // every word; the compressor moves on when both have it
    wire  en_cap = sys_cfg.capture;
    wire  en_dl  = sys_cfg.dl.downlink;
    logic got_cap, got_dl, pk_tready;
    wire  v_cap  = c_tvalid && en_cap && !got_cap;
    wire  v_dl   = c_tvalid && en_dl && !got_dl;

    assign c_tready = (!en_cap || got_cap || m_axis_cmp_tready) && (!en_dl || got_dl || pk_tready);

    always_ff @(posedge clk_sys) begin
        if (rst_sys || (c_tvalid && c_tready)) begin
            got_cap <= 1'b0;
            got_dl  <= 1'b0;
        end else begin
            if (v_cap && m_axis_cmp_tready) got_cap <= 1'b1;
            if (v_dl && pk_tready)          got_dl  <= 1'b1;
        end
    end

    assign m_axis_cmp_tdata  = c_tdata;
    assign m_axis_cmp_tkeep  = c_tkeep;
    assign m_axis_cmp_tlast  = c_tlast;
    assign m_axis_cmp_tvalid = v_cap;

    wire in_fire  = sg_tvalid && sg_tready;
    wire out_fire = c_tvalid && c_tready;

    logic [31:0] cycle;                     // free-running
    logic [31:0] start [2];                 // input start cycle of the scenes in flight
    logic        in_idx, out_idx;
    logic [31:0] scene_bytes;
    logic [3:0]  keep_bytes;
    logic        cmp_busy;
    logic [31:0] scenes_done, samples_in, bytes_out, last_bytes, last_cycles, out_stalls;

    always_comb begin
        keep_bytes = '0;
        for (int b = 0; b < 8; b++) keep_bytes += 4'(c_tkeep[b]);
    end

    always_ff @(posedge clk_sys) begin
        if (rst_sys) begin
            cycle <= '0;
            {in_idx, out_idx, cmp_busy} <= '0;
            scene_bytes <= '0;
            {scenes_done, samples_in, bytes_out, last_bytes, last_cycles, out_stalls} <= '0;
        end else begin
            cycle <= cycle + 32'd1;
            if (in_fire) begin
                samples_in <= sat_inc(samples_in);
                if (sg_tuser[0]) begin
                    start[in_idx] <= cycle;
                    in_idx        <= !in_idx;
                    cmp_busy      <= 1'b1;
                end
            end
            if (out_fire) begin
                bytes_out <= (bytes_out > 32'hFFFF_FFF7) ? 32'hFFFF_FFFF : bytes_out + 32'(keep_bytes);
                if (c_tlast) begin
                    scenes_done <= sat_inc(scenes_done);
                    last_bytes  <= scene_bytes + 32'(keep_bytes);
                    last_cycles <= cycle - start[out_idx] + 32'd1;
                    out_idx     <= !out_idx;
                    scene_bytes <= '0;
                    if (!(in_fire && sg_tuser[0]) && in_idx == !out_idx) cmp_busy <= 1'b0;
                end else begin
                    scene_bytes <= scene_bytes + 32'(keep_bytes);
                end
            end
            if (c_tvalid && !c_tready) out_stalls <= sat_inc(out_stalls);
            if (cmp_clr) begin
                {scenes_done, samples_in, bytes_out, last_bytes, last_cycles, out_stalls} <= '0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Downlink chain (clk_sys): packets, AOS frames, RS, randomizer + ASM,
    // error injector, link
    // ---------------------------------------------------------------------
    dl_cfg_t  dl;
    dl_stat_t dl_src;                       // downlink and ground counters (clk_sys)
    assign dl = sys_cfg.dl;

    // Scene start timestamps for the packetizer, queued as each scene enters the
    // compressor (each line brings its scene's timestamp through the corner turn,
    // so a scene lost whole takes its timestamp with it). Scenes are numbered
    // here as the packetizer's scene IDs (counted while the downlink is on).
    wire         sg_first  = in_fire && sg_tuser[0] && dl.downlink;
    wire         meta_push = sg_first;
    logic [23:0] meta_ts;
    logic        meta_empty, meta_full, meta_rd;
    logic [31:0] ts_overflows;

    sync_fifo #(.WIDTH(24), .DEPTH(16)) u_meta (
        .clk (clk_sys), .rst (rst_sys),
        .din (sg_scene_ts), .wr_en (meta_push && !meta_full),
        .rd_en (meta_rd), .dout (meta_ts), .empty (meta_empty), .full (meta_full), .count ()
    );

    always_ff @(posedge clk_sys) begin
        if (rst_sys || pkt_clr)
            ts_overflows <= '0;
        else if (meta_push && meta_full)
            ts_overflows <= sat_inc(ts_overflows);
    end
    assign dl_src.ts_overflows = ts_overflows;

    logic [7:0]  p_tdata, a_tdata, r_tdata, k_tdata, e_tdata;
    logic [1:0]  p_tuser;
    logic        k_tuser, e_tuser;
    logic        p_tlast, p_tvalid, p_tready;
    logic        a_tlast, a_tvalid, a_tready;
    logic        r_tlast, r_tvalid, r_tready;
    logic        k_tlast, k_tvalid, k_tready;
    logic        e_tlast, e_tvalid, e_tready;

    // Scene flags for the packet headers (SPEC 6.5), per scene from the
    // compressor input to the end of its packetization: uncorrectable memory
    // error (RAD-06) and lines dropped. Calibration table and corner-turn errors
    // come marked on the samples they hit, fill lines and scene-guard padding as
    // fill; line-buffer and weight errors happen inside the compressor and flag
    // every scene between its input and the packetizer. 32 flags cover the
    // scenes in flight.
    logic [15:0] cmp_scenes, pk_scene;      // scenes into the compressor; being packetized
    logic [31:0] ue_flag, ld_flag;
    wire  [4:0]  cur      = sg_first ? cmp_scenes[4:0] : cmp_scenes[4:0] - 5'd1;
    wire  [5:0]  inflight = 6'(cmp_scenes - pk_scene);

    always_ff @(posedge clk_sys) begin
        if (rst_sys) begin
            cmp_scenes <= '0;
            ue_flag    <= '0;
            ld_flag    <= '0;
        end else begin
            if (ecc[2].dbe || w_dbe)
                for (int j = 0; j < 32; j++)
                    if (6'(5'(j) - pk_scene[4:0]) < inflight) ue_flag[j] <= 1'b1;
            if (sg_first) begin
                ue_flag[cur] <= sg_ue;
                ld_flag[cur] <= sg_fill;
                cmp_scenes   <= cmp_scenes + 16'd1;
            end else if (in_fire) begin
                if (sg_ue)   ue_flag[cur] <= 1'b1;
                if (sg_fill) ld_flag[cur] <= 1'b1;
            end
        end
    end

    space_packetizer #(.PKT_MAX(2048)) u_pkt (
        .clk (clk_sys), .rst (rst_sys),
        .apid (dl.apid), .pkt_data (dl.pkt_data), .raw_mode (sys_cfg.cmp.raw),
        .scene_ld (ld_flag[pk_scene[4:0]]), .scene_ue (ue_flag[pk_scene[4:0]]), .cnt_clr (pkt_clr),
        .s_axis_tdata (c_tdata), .s_axis_tkeep (c_tkeep), .s_axis_tlast (c_tlast),
        .s_axis_tvalid (v_dl), .s_axis_tready (pk_tready),
        .s_meta_tdata (meta_ts), .s_meta_tvalid (!meta_empty), .s_meta_tready (meta_rd),
        .m_axis_tdata (p_tdata), .m_axis_tuser (p_tuser), .m_axis_tlast (p_tlast),
        .m_axis_tvalid (p_tvalid), .m_axis_tready (p_tready),
        .packets (dl_src.packets), .scenes (dl_src.scenes), .scene_id (pk_scene),
        .tmr_mismatch (fsm_mm[3])
    );

    aos_framer u_aos (
        .clk (clk_sys), .rst (rst_sys),
        .scid (dl.scid), .vcid (dl.vcid), .idle_en (dl.idle_en), .frame_gap (dl.frame_gap),
        .cnt_clr (aos_clr),
        .s_axis_tdata (p_tdata), .s_axis_tuser (p_tuser), .s_axis_tlast (p_tlast),
        .s_axis_tvalid (p_tvalid), .s_axis_tready (p_tready),
        .m_axis_tdata (a_tdata), .m_axis_tlast (a_tlast), .m_axis_tvalid (a_tvalid),
        .m_axis_tready (a_tready),
        .science_frames (dl_src.science_frames), .idle_frames (dl_src.idle_frames),
        .tmr_mismatch (fsm_mm[4])
    );

    rs_encoder u_rs (
        .clk (clk_sys), .rst (rst_sys),
        .s_axis_tdata (a_tdata), .s_axis_tlast (a_tlast), .s_axis_tvalid (a_tvalid),
        .s_axis_tready (a_tready),
        .m_axis_tdata (r_tdata), .m_axis_tlast (r_tlast), .m_axis_tvalid (r_tvalid),
        .m_axis_tready (r_tready)
    );

    cadu_framer u_cadu (
        .clk (clk_sys), .rst (rst_sys), .cnt_clr (aos_clr),
        .s_axis_tdata (r_tdata), .s_axis_tlast (r_tlast), .s_axis_tvalid (r_tvalid),
        .s_axis_tready (r_tready),
        .m_axis_tdata (k_tdata), .m_axis_tuser (k_tuser), .m_axis_tlast (k_tlast),
        .m_axis_tvalid (k_tvalid), .m_axis_tready (k_tready),
        .cadus (dl_src.cadus)
    );

    error_injector u_ei (
        .clk (clk_sys), .rst (rst_sys),
        .mode (dl.ei_mode), .threshold (dl.ei_threshold), .seed (dl.ei_seed), .seed_load (ei_seed),
        .burst_len (dl.ei_burst_len), .period (dl.ei_period), .asm_bits (dl.ei_asm_bits),
        .cnt_clr (ei_clr),
        .s_axis_tdata (k_tdata), .s_axis_tuser (k_tuser), .s_axis_tlast (k_tlast),
        .s_axis_tvalid (k_tvalid), .s_axis_tready (k_tready),
        .m_axis_tdata (e_tdata), .m_axis_tuser (e_tuser), .m_axis_tlast (e_tlast),
        .m_axis_tvalid (e_tvalid), .m_axis_tready (e_tready),
        .injected_bits (dl_src.injected_bits)
    );

    // ---------------------------------------------------------------------
    // Link layer (clk_sys <-> clk_link) and the transceiver interface
    // ---------------------------------------------------------------------
    logic [7:0]  l_tdata;
    logic        l_tvalid;
    logic [15:0] lk_rxdata, tx_d1;
    logic [1:0]  lk_rxcharisk, lk_rxdisperr, lk_rxnotintable, tx_k1;
    logic        lk_aligned;
    logic        lnk_clr, lnk_inj;
    logic        lk_locked;
    logic [31:0] lk_code_errors, lk_lock_losses, lk_rx_words, lk_tx_data, lk_tx_idle, lk_rx_overflows;
    logic [31:0] clk_corrections, buf_errors, prbs_errors;
    logic [47:0] prbs_words;

    link_layer u_link (
        .clk_sys (clk_sys), .rst_sys (rst_link_sys),
        .clk_link (clk_link), .rst_link (rst_link),
        .cc_interval (lnk_cfg.cc_interval), .cnt_clr (lnk_clr),
        .s_axis_tdata (e_tdata), .s_axis_tvalid (e_tvalid), .s_axis_tready (e_tready),
        .m_tdata (l_tdata), .m_tvalid (l_tvalid),
        .txdata (gt_txdata), .txcharisk (gt_txcharisk),
        .rxdata (lk_rxdata), .rxcharisk (lk_rxcharisk), .rxdisperr (lk_rxdisperr),
        .rxnotintable (lk_rxnotintable), .rxbyteisaligned (lk_aligned),
        .locked (lk_locked), .code_errors (lk_code_errors), .lock_losses (lk_lock_losses),
        .rx_words (lk_rx_words), .tx_data_words (lk_tx_data),
        .tx_idle_words (lk_tx_idle), .rx_overflows (lk_rx_overflows)
    );

    // clk_link: transceiver outputs registered once, the receiving channel
    // selected, or the transmitted words looped back inside the FPGA
    logic        rx_ready_lnk;
    logic [7:0]  lnk_clr_seen, lnk_inj_seen;
    logic [15:0] g_rxdata  [2];
    logic [1:0]  g_rxk     [2], g_rxde [2], g_rxnt [2], g_corcnt [2];
    logic [2:0]  g_bufst   [2];
    logic [1:0]  g_aligned, g_prbserr, g_prbslocked;
    logic [1:0]  corcnt_prev;
    logic [2:0]  bufreset_cnt [2];

    xpm_cdc_single #(.DEST_SYNC_FF(2), .SRC_INPUT_REG(0)) u_cdc_rx_done (
        .src_clk (1'b0), .src_in (gt_rx_done), .dest_clk (clk_link), .dest_out (rx_ready_lnk));

    wire ch = !lnk_cfg.rx_sel;              // rx_sel 0: SFP1 (channel 1)

    always_ff @(posedge clk_link) begin
        for (int c = 0; c < 2; c++) begin
            g_rxdata[c] <= gt_rxdata[16 * c +: 16];
            g_rxk[c]    <= gt_rxcharisk[2 * c +: 2];
            g_rxde[c]   <= gt_rxdisperr[2 * c +: 2];
            g_rxnt[c]   <= gt_rxnotintable[2 * c +: 2];
            g_corcnt[c] <= gt_rxclkcorcnt[2 * c +: 2];
            g_bufst[c]  <= gt_rxbufstatus[3 * c +: 3];
        end
        g_aligned     <= gt_rxbyteisaligned;
        g_prbserr     <= gt_rxprbserr;
        g_prbslocked  <= gt_rxprbslocked;
        tx_d1         <= gt_txdata;
        tx_k1         <= gt_txcharisk;

        if (lnk_cfg.fabric_lb) begin
            lk_rxdata       <= tx_d1;
            lk_rxcharisk    <= tx_k1;
            lk_rxdisperr    <= 2'b00;
            lk_rxnotintable <= 2'b00;
            lk_aligned      <= 1'b1;
        end else begin
            lk_rxdata       <= g_rxdata[ch];
            lk_rxcharisk    <= g_rxk[ch];
            lk_rxdisperr    <= g_rxde[ch];
            lk_rxnotintable <= g_rxnt[ch];
            lk_aligned      <= g_aligned[ch] && rx_ready_lnk;
        end
    end

    assign gt_prbs_sel   = lnk_cfg.prbs_sel;
    assign gt_loopback   = lnk_cfg.loopback;
    assign gt_txpolarity = lnk_cfg.txpol;
    assign gt_rxpolarity = lnk_cfg.rxpol;

    always_ff @(posedge clk_link) begin
        if (rst_link) begin
            {lnk_clr_seen, lnk_inj_seen} <= '0;
            {lnk_clr, lnk_inj, gt_prbs_cnt_reset, gt_prbs_force_err} <= '0;
            corcnt_prev <= '0;
            {clk_corrections, buf_errors, prbs_errors, prbs_words} <= '0;
            for (int c = 0; c < 2; c++) bufreset_cnt[c] <= '0;
            gt_rxbufreset <= '0;
        end else begin
            lnk_clr           <= (lnk_cfg.clr_seq != lnk_clr_seen);
            lnk_clr_seen      <= lnk_cfg.clr_seq;
            lnk_inj           <= (lnk_cfg.inj_seq != lnk_inj_seen);
            lnk_inj_seen      <= lnk_cfg.inj_seq;
            gt_prbs_cnt_reset <= lnk_clr;
            gt_prbs_force_err <= lnk_inj;

            // clock corrections (RXCLKCORCNT holds its value: count changes)
            corcnt_prev <= g_corcnt[ch];
            if (g_corcnt[ch] != 2'b00 && g_corcnt[ch] != corcnt_prev)
                clk_corrections <= sat_inc(clk_corrections);

            // elastic buffer under/overflow: count, and reset that buffer
            for (int c = 0; c < 2; c++) begin
                if (!rx_ready_lnk)
                    bufreset_cnt[c] <= '0;
                else if (g_bufst[c][2] && bufreset_cnt[c] == 3'd0)
                    bufreset_cnt[c] <= 3'd7;
                else if (bufreset_cnt[c] != 3'd0)
                    bufreset_cnt[c] <= bufreset_cnt[c] - 3'd1;
                gt_rxbufreset[c] <= (bufreset_cnt[c] > 3'd3);
            end
            if (rx_ready_lnk && !lnk_cfg.fabric_lb && g_bufst[ch][2] && bufreset_cnt[ch] == 3'd0)
                buf_errors <= sat_inc(buf_errors);

            // PRBS mode (LNK-06)
            if (lnk_cfg.prbs_sel != 4'd0) begin
                if (g_prbserr[ch])    prbs_errors <= sat_inc(prbs_errors);
                if (g_prbslocked[ch]) prbs_words  <= prbs_words + 48'd1;
            end

            if (lnk_clr) {clk_corrections, buf_errors, prbs_errors, prbs_words} <= '0;
        end
    end

    always_comb begin
        lnk_stat_src.wr_seq_echo     = lnk_cfg.wr_seq;
        lnk_stat_src.locked          = lk_locked;
        lnk_stat_src.aligned         = lnk_cfg.fabric_lb || (g_aligned[ch] && rx_ready_lnk);
        lnk_stat_src.prbs_locked     = g_prbslocked[ch];
        lnk_stat_src.code_errors     = lk_code_errors;
        lnk_stat_src.lock_losses     = lk_lock_losses;
        lnk_stat_src.rx_words        = lk_rx_words;
        lnk_stat_src.tx_data_words   = lk_tx_data;
        lnk_stat_src.tx_idle_words   = lk_tx_idle;
        lnk_stat_src.rx_overflows    = lk_rx_overflows;
        lnk_stat_src.clk_corrections = clk_corrections;
        lnk_stat_src.buf_errors      = buf_errors;
        lnk_stat_src.prbs_errors     = prbs_errors;
        lnk_stat_src.prbs_words      = prbs_words;
    end

    // ---------------------------------------------------------------------
    // Ground receiver (clk_sys): frame sync, derandomizer, capture
    // ---------------------------------------------------------------------
    logic [7:0] g_tdata;
    logic       g_tlast, g_tvalid, g_tready;

    frame_sync u_fs (
        .clk (clk_sys), .rst (rst_sys),
        .asm_tol (dl.asm_tol), .flywheel (dl.flywheel), .cnt_clr (gr_clr),
        .s_tdata (l_tdata), .s_tvalid (l_tvalid),
        .m_axis_tdata (g_tdata), .m_axis_tlast (g_tlast), .m_axis_tvalid (g_tvalid),
        .m_axis_tready (g_tready),
        .sync_state (dl_src.sync_state), .cadus (dl_src.gr_cadus),
        .sync_losses (dl_src.sync_losses), .asm_bit_errors (dl_src.asm_bit_errors),
        .dropped_records (dl_src.dropped_records)
    );

    gr_capture u_grc (
        .clk (clk_sys), .rst (rst_sys),
        .capture (dl.gr_capture), .skip_idle (dl.gr_skip_idle), .sci_vcid (dl.vcid),
        .records_per_xfer (dl.gr_records),
        .cnt_clr (gr_clr),
        .s_axis_tdata (g_tdata), .s_axis_tlast (g_tlast), .s_axis_tvalid (g_tvalid),
        .s_axis_tready (g_tready),
        .m_axis_tdata (m_axis_gr_tdata), .m_axis_tkeep (m_axis_gr_tkeep),
        .m_axis_tlast (m_axis_gr_tlast), .m_axis_tvalid (m_axis_gr_tvalid),
        .m_axis_tready (m_axis_gr_tready),
        .captured (dl_src.captured), .skipped (dl_src.skipped)
    );

    // ---------------------------------------------------------------------
    // Pipeline watchdogs (RAD-05): a stage with work pending that moves nothing
    // for RAD.WD_TIMEOUT cycles is stalled (counted, status, interrupt)
    // ---------------------------------------------------------------------
    logic [2:0]       wd_stalled, wd_stall;
    logic [2:0][31:0] wd_count;

    stall_watchdog u_wd_cmp (
        .clk (clk_sys), .rst (rst_sys), .timeout (sys_cfg.wd_timeout),
        .pending (sg_tvalid || c_tvalid), .progress (in_fire || out_fire),
        .stalled (wd_stalled[0]), .stall (wd_stall[0]));

    stall_watchdog u_wd_dl (
        .clk (clk_sys), .rst (rst_sys), .timeout (sys_cfg.wd_timeout),
        .pending (p_tvalid), .progress (p_tvalid && p_tready),
        .stalled (wd_stalled[1]), .stall (wd_stall[1]));

    stall_watchdog u_wd_gr (
        .clk (clk_sys), .rst (rst_sys), .timeout (sys_cfg.wd_timeout),
        .pending (m_axis_gr_tvalid), .progress (m_axis_gr_tvalid && m_axis_gr_tready),
        .stalled (wd_stalled[2]), .stall (wd_stall[2]));

    always_ff @(posedge clk_sys) begin
        if (rst_sys || rad_clr)
            wd_count <= '0;
        else
            for (int s = 0; s < 3; s++)
                if (wd_stall[s]) wd_count[s] <= sat_inc(wd_count[s]);
    end

    always_comb begin
        sys_stat_src.fe              = fe_stat;
        sys_stat_src.wr_seq_echo     = sys_cfg.wr_seq;
        sys_stat_src.tbl_rdata       = tbl_rdata;
        sys_stat_src.tbl_done_seq    = tbl_done_seq;
        sys_stat_src.cmp_busy        = cmp_busy;
        sys_stat_src.cmp_cfg_error   = cfg_error;
        sys_stat_src.scenes_done     = scenes_done;
        sys_stat_src.samples_in      = samples_in;
        sys_stat_src.bytes_out       = bytes_out;
        sys_stat_src.last_bytes      = last_bytes;
        sys_stat_src.last_cycles     = last_cycles;
        sys_stat_src.out_stalls      = out_stalls;
        sys_stat_src.scenes_padded   = scenes_padded;
        sys_stat_src.samples_padded  = samples_padded;
        rad_stat_src.inj_pending     = {ecc[2].pending, ecc[1].pending, ecc[0].pending};
        rad_stat_src.ecc_count       = ecc_count;
        rad_stat_src.wd_stalled      = wd_stalled;
        rad_stat_src.wd_count        = wd_count;
        rad_stat_src.w_sbe           = w_sbe_count;
        rad_stat_src.w_dbe           = w_dbe_count;
        rad_stat_src.tmr_cmp         = tmr_cmp_count;
        rad_stat_src.tmr_fsm         = tmr_fsm_count;
        sys_stat_src.dl              = dl_src;
    end

    // ---------------------------------------------------------------------
    // Soft error mitigation controller (RAD-01). It runs on clk_sem (in
    // hpdp_top); commands go over as a sequence count in a snapshot, status and
    // counters come back the same way, and the monitor bytes (its status
    // reports) through an asynchronous FIFO that never holds it up (bytes that
    // find the FIFO full are counted).
    // ---------------------------------------------------------------------
    typedef struct packed {
        logic        grant;
        logic [7:0]  cmd_seq, clr_seq;
        logic [43:0] cmd_code;
    } sem_cfg_t;

    typedef struct packed {
        logic [9:0]  status;
        logic        busy, avail;
        logic [7:0]  cmd_done;                  // sequence count of the last command given
        logic [31:0] corrections, uncorrectable, heartbeats, mon_dropped;
    } sem_stat_t;

    logic [7:0] sem_cmd_seq;
    always_ff @(posedge clk_ctrl) begin
        if (rst_ctrl)
            sem_cmd_seq <= '0;
        else if (hwif_out.rad.SEM_CMD.go.value)
            sem_cmd_seq <= sem_cmd_seq + 8'd1;
    end

    sem_cfg_t  sem_cfg_src, sem_cfg;
    sem_stat_t sem_stat_src, sem_stat;
    logic      rst_sem;

    always_comb begin
        sem_cfg_src.grant    = hwif_out.rad.SEM_CTRL.grant.value;
        sem_cfg_src.cmd_seq  = sem_cmd_seq;
        sem_cfg_src.clr_seq  = rad_clr_seq;
        // PG187 command codes (UltraScale+): op in [43:40]; error injection
        // {4'hC, 8'h00, slr 2'b00, linear frame address, word, bit}
        sem_cfg_src.cmd_code = {hwif_out.rad.SEM_CMD.op.value,
                                hwif_out.rad.SEM_CMD.op.value == 4'hC ?
                                {10'd0, hwif_out.rad.SEM_INJ_ADDR.lfa.value, hwif_out.rad.SEM_INJ_ADDR.word.value,
                                 hwif_out.rad.SEM_INJ_ADDR.bitn.value} : 40'd0};
    end

    xpm_cdc_async_rst #(.DEST_SYNC_FF(4), .RST_ACTIVE_HIGH(1)) u_cdc_rst_sem (
        .src_arst (rst_ctrl), .dest_clk (clk_sem), .dest_arst (rst_sem));

    cdc_bus #(.WIDTH($bits(sem_cfg_t))) u_cdc_sem_cfg (
        .src_clk (clk_ctrl), .src_rst (rst_ctrl), .src_data (sem_cfg_src),
        .dest_clk (clk_sem), .dest_rst (rst_sem), .dest_data (sem_cfg));

    cdc_bus #(.WIDTH($bits(sem_stat_t))) u_cdc_sem_stat (
        .src_clk (clk_sem), .src_rst (rst_sem), .src_data (sem_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (sem_stat));

    // clk_sem: commands (one strobe per new sequence count, when not busy),
    // counters of correction, uncorrectable and heartbeat events
    logic [7:0] sem_cmd_seen, sem_clr_seen;
    logic [9:0] sem_status_d;
    logic       sem_wait;                       // a command was given: wait for busy to drop
    logic [31:0] sem_corr, sem_unc, sem_hb, sem_drop;
    logic        mon_full;

    always_ff @(posedge clk_sem) begin
        if (rst_sem) begin
            sem_cmd_seen   <= '0;
            sem_clr_seen   <= '0;
            sem_cmd_strobe <= 1'b0;
            sem_wait       <= 1'b0;
            sem_status_d   <= '0;
            {sem_corr, sem_unc, sem_hb, sem_drop} <= '0;
        end else begin
            sem_cmd_strobe <= 1'b0;
            sem_status_d   <= sem_status;
            if (sem_wait) begin
                if (!sem_cmd_strobe && !sem_cmd_busy) sem_wait <= 1'b0;
            end else if (sem_cfg.cmd_seq != sem_cmd_seen && !sem_cmd_busy) begin
                sem_cmd_strobe <= 1'b1;
                sem_cmd_code   <= sem_cfg.cmd_code;
                sem_cmd_seen   <= sem_cfg.cmd_seq;
                sem_wait       <= 1'b1;
            end
            if (sem_status[3] && !sem_status_d[3]) sem_corr <= sat_inc(sem_corr);
            if (sem_status[9] && !sem_status_d[9]) sem_unc  <= sat_inc(sem_unc);
            if (sem_status[0] && !sem_status_d[0]) sem_hb   <= sat_inc(sem_hb);
            if (sem_mon_write && mon_full)         sem_drop <= sat_inc(sem_drop);
            if (sem_cfg.clr_seq != sem_clr_seen) begin
                {sem_corr, sem_unc, sem_hb, sem_drop} <= '0;
                sem_clr_seen <= sem_cfg.clr_seq;
            end
        end
    end
    assign sem_grant = sem_cfg.grant;

    always_comb begin
        sem_stat_src.status        = sem_status;
        sem_stat_src.busy          = sem_cmd_busy || sem_wait;
        sem_stat_src.avail         = sem_icap_avail;
        sem_stat_src.cmd_done      = sem_cmd_seen;
        sem_stat_src.corrections   = sem_corr;
        sem_stat_src.uncorrectable = sem_unc;
        sem_stat_src.heartbeats    = sem_hb;
        sem_stat_src.mon_dropped   = sem_drop;
    end

    // monitor bytes -> clk_ctrl; RAD.SEM_MON reads the oldest byte and removes it
    logic [7:0] mon_dout;
    logic       mon_empty, mon_rd;

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE ("block"),
        .FIFO_WRITE_DEPTH (2048),
        .WRITE_DATA_WIDTH (8),
        .READ_DATA_WIDTH  (8),
        .READ_MODE        ("fwft"),
        .FIFO_READ_LATENCY (0),
        .CDC_SYNC_STAGES  (2),
        .USE_ADV_FEATURES ("0000")
    ) u_sem_mon (
        .rst (rst_sem), .wr_clk (clk_sem), .wr_en (sem_mon_write && !mon_full), .din (sem_mon_data),
        .full (mon_full), .rd_clk (clk_ctrl), .rd_en (mon_rd), .dout (mon_dout), .empty (mon_empty),
        .sleep (1'b0), .injectsbiterr (1'b0), .injectdbiterr (1'b0),
        .overflow (), .wr_rst_busy (), .almost_full (), .wr_ack (), .prog_full (), .wr_data_count (),
        .underflow (), .rd_rst_busy (), .almost_empty (), .data_valid (), .prog_empty (),
        .rd_data_count (), .sbiterr (), .dbiterr ());

    assign mon_rd = hwif_out.rad.SEM_MON.data.swacc && !mon_empty;

    // ---------------------------------------------------------------------
    // Status registers and interrupts (clk_ctrl)
    // ---------------------------------------------------------------------
    logic [31:0] scenes_prev, lock_losses_prev, sync_losses_prev;
    logic [33:0] dbe_prev, wd_prev;
    logic [31:0] sem_unc_prev;
    wire  [33:0] wd_sum  = 34'(rad_stat.wd_count[0]) + 34'(rad_stat.wd_count[1]) + 34'(rad_stat.wd_count[2]);
    wire  [33:0] dbe_sum = 34'(rad_stat.ecc_count[0][1]) + 34'(rad_stat.ecc_count[1][1]) +
                           34'(rad_stat.ecc_count[2][1]) + 34'(rad_stat.w_dbe);
    wire  [31:0] errors_now = se_stat.underrun_samples | se_stat.period_overruns |
                              se_stat.dropped_samples | sys_stat.fe.dropped_lines |
                              dl_stat.ts_overflows | dl_stat.dropped_records | lnk_stat.rx_overflows;
    wire  [34:0] error_sum  = 35'(se_stat.underrun_samples) + 35'(se_stat.period_overruns) +
                              35'(se_stat.dropped_samples) + 35'(sys_stat.fe.dropped_lines) +
                              35'(dl_stat.ts_overflows) + 35'(dl_stat.dropped_records) +
                              35'(lnk_stat.rx_overflows);
    logic [34:0] error_sum_prev;
    wire         lnk_up = tx_done_ctrl && !lnk_down;

    always_ff @(posedge clk_ctrl) begin
        if (rst_ctrl) begin
            scenes_prev      <= '0;
            error_sum_prev   <= '0;
            lock_losses_prev <= '0;
            sync_losses_prev <= '0;
            dbe_prev         <= '0;
            wd_prev          <= '0;
            sem_unc_prev     <= '0;
        end else begin
            scenes_prev      <= sys_stat.scenes_done;
            error_sum_prev   <= error_sum;
            lock_losses_prev <= lnk_stat.lock_losses;
            sync_losses_prev <= dl_stat.sync_losses;
            dbe_prev         <= dbe_sum;
            wd_prev          <= wd_sum;
            sem_unc_prev     <= sem_stat.uncorrectable;
        end
    end

    always_comb begin
        hwif_in.sys.STATUS.busy.next          = se_stat.busy;
        hwif_in.sys.STATUS.error.next         = errors_now != 32'd0;
        hwif_in.sys.STATUS.sensor_locked.next = locked_ctrl;
        hwif_in.sys.STATUS.cfg_pending.next   = wr_seq != sys_stat.wr_seq_echo ||
                                                wr_seq != sen_stat.wr_seq_echo ||
                                                (lnk_up && wr_seq != lnk_stat.wr_seq_echo);
        hwif_in.sys.IRQ_STATUS.scene_done.hwset     = sys_stat.scenes_done > scenes_prev;
        hwif_in.sys.IRQ_STATUS.error.hwset          = error_sum > error_sum_prev;
        hwif_in.sys.IRQ_STATUS.link_lock_loss.hwset = lnk_up && lnk_stat.lock_losses > lock_losses_prev;
        hwif_in.sys.IRQ_STATUS.sync_loss.hwset      = dl_stat.sync_losses > sync_losses_prev;
        hwif_in.sys.IRQ_STATUS.ecc_uncorrectable.hwset = dbe_sum > dbe_prev;
        hwif_in.sys.IRQ_STATUS.stall.hwset             = wd_sum > wd_prev;
        hwif_in.sys.IRQ_STATUS.sem_uncorrectable.hwset = sem_stat.uncorrectable > sem_unc_prev;
        hwif_in.sys.BUILD_ID.build_id.next    = BUILD_ID;
        hwif_in.sys.LIMITS.nx_max.next        = 16'(NX_MAX);
        hwif_in.sys.LIMITS.nz_max.next        = 16'(NZ_MAX);

        hwif_in.se.STATUS.busy.next             = se_stat.busy;
        hwif_in.se.STATUS.done.next             = se_stat.done;
        hwif_in.se.LINE_TIMESTAMP.value.next    = se_stat.line_timestamp;
        hwif_in.se.SCENE_TIMESTAMP.value.next   = se_stat.scene_timestamp;
        hwif_in.se.LINES_EMITTED.count.next     = se_stat.lines_emitted;
        hwif_in.se.SCENES_STARTED.count.next    = se_stat.scenes_started;
        hwif_in.se.UNDERRUN_SAMPLES.count.next  = se_stat.underrun_samples;
        hwif_in.se.PERIOD_OVERRUNS.count.next   = se_stat.period_overruns;
        hwif_in.se.DROPPED_SAMPLES.count.next   = se_stat.dropped_samples;

        hwif_in.rc.STATUS.tbl_busy.next         = tbl_seq != sys_stat.tbl_done_seq;
        hwif_in.rc.SCENE_CLAMPED.count.next     = sys_stat.fe.scene_clamped;
        hwif_in.rc.SCENE_REPLACED.count.next    = sys_stat.fe.scene_replaced;
        hwif_in.rc.TBL_WORDS.words.next         = sys_stat.fe.tbl_words;
        hwif_in.rc.TBL_RDATA_LO.dark.next       = sys_stat.tbl_rdata[15:0];
        hwif_in.rc.TBL_RDATA_LO.gain.next       = sys_stat.tbl_rdata[31:16];
        hwif_in.rc.TBL_RDATA_HI.bad.next        = sys_stat.tbl_rdata[32];

        hwif_in.ct.LINES_OUT.count.next         = sys_stat.fe.lines_out;
        hwif_in.ct.DROPPED_LINES.count.next     = sys_stat.fe.dropped_lines;
        hwif_in.ct.UNFILLED_LINES.count.next    = sys_stat.fe.unfilled_lines;
        hwif_in.ct.SCENES_PADDED.count.next     = sys_stat.scenes_padded;
        hwif_in.ct.SAMPLES_PADDED.count.next    = sys_stat.samples_padded;

        hwif_in.rad.STATUS.inj_pending.next     = rad_stat.inj_pending;
        hwif_in.rad.STATUS.wd_stalled.next      = rad_stat.wd_stalled;
        hwif_in.rad.WD_CMP.count.next           = rad_stat.wd_count[0];
        hwif_in.rad.WD_DL.count.next            = rad_stat.wd_count[1];
        hwif_in.rad.WD_GR.count.next            = rad_stat.wd_count[2];
        hwif_in.rad.W_SBE.count.next            = rad_stat.w_sbe;
        hwif_in.rad.W_DBE.count.next            = rad_stat.w_dbe;
        hwif_in.rad.TMR_CMP.count.next          = rad_stat.tmr_cmp;
        hwif_in.rad.TMR_REGS.count.next         = tmr_regs_count;
        hwif_in.rad.TMR_FSM.count.next          = rad_stat.tmr_fsm;
        hwif_in.rad.SEM_STATUS.state.next       = sem_stat.status;
        hwif_in.rad.SEM_STATUS.busy.next        = sem_stat.busy || sem_stat.cmd_done != sem_cmd_seq;
        hwif_in.rad.SEM_STATUS.icap_avail.next  = sem_stat.avail;
        hwif_in.rad.SEM_MON.data.next           = mon_dout;
        hwif_in.rad.SEM_MON.valid.next          = !mon_empty;
        hwif_in.rad.SEM_CORRECTIONS.count.next  = sem_stat.corrections;
        hwif_in.rad.SEM_UNCORRECTABLE.count.next = sem_stat.uncorrectable;
        hwif_in.rad.SEM_HEARTBEATS.count.next   = sem_stat.heartbeats;
        hwif_in.rad.SEM_MON_DROPPED.count.next  = sem_stat.mon_dropped;
        hwif_in.rad.CAL_SBE.count.next          = rad_stat.ecc_count[0][0];
        hwif_in.rad.CAL_DBE.count.next          = rad_stat.ecc_count[0][1];
        hwif_in.rad.CT_SBE.count.next           = rad_stat.ecc_count[1][0];
        hwif_in.rad.CT_DBE.count.next           = rad_stat.ecc_count[1][1];
        hwif_in.rad.LB_SBE.count.next           = rad_stat.ecc_count[2][0];
        hwif_in.rad.LB_DBE.count.next           = rad_stat.ecc_count[2][1];

        hwif_in.cmp.STATUS.busy.next            = sys_stat.cmp_busy;
        hwif_in.cmp.STATUS.cfg_error.next       = sys_stat.cmp_cfg_error;
        hwif_in.cmp.SCENES_DONE.count.next      = sys_stat.scenes_done;
        hwif_in.cmp.SAMPLES_IN.count.next       = sys_stat.samples_in;
        hwif_in.cmp.BYTES_OUT.count.next        = sys_stat.bytes_out;
        hwif_in.cmp.LAST_BYTES.count.next       = sys_stat.last_bytes;
        hwif_in.cmp.LAST_CYCLES.count.next      = sys_stat.last_cycles;
        hwif_in.cmp.OUT_STALLS.count.next       = sys_stat.out_stalls;

        hwif_in.pkt.PACKETS.count.next          = dl_stat.packets;
        hwif_in.pkt.SCENES.count.next           = dl_stat.scenes;
        hwif_in.pkt.TS_OVERFLOWS.count.next     = dl_stat.ts_overflows;

        hwif_in.aos.SCIENCE_FRAMES.count.next   = dl_stat.science_frames;
        hwif_in.aos.IDLE_FRAMES.count.next      = dl_stat.idle_frames;
        hwif_in.aos.CADUS.count.next            = dl_stat.cadus;

        hwif_in.ei.INJECTED_BITS.count.next     = dl_stat.injected_bits;

        hwif_in.lnk.STATUS.tx_ready.next        = tx_done_ctrl;
        hwif_in.lnk.STATUS.rx_ready.next        = rx_done_ctrl;
        hwif_in.lnk.STATUS.aligned.next         = lnk_up && lnk_stat.aligned;
        hwif_in.lnk.STATUS.locked.next          = lnk_up && lnk_stat.locked;
        hwif_in.lnk.STATUS.prbs_locked.next     = lnk_up && lnk_stat.prbs_locked;
        hwif_in.lnk.STATUS.cpll_locked.next     = cpll_ctrl;
        hwif_in.lnk.STATUS.powergood.next       = pgood_ctrl;
        hwif_in.lnk.CODE_ERRORS.count.next      = lnk_stat.code_errors;
        hwif_in.lnk.LOCK_LOSSES.count.next      = lnk_stat.lock_losses;
        hwif_in.lnk.RX_WORDS.count.next         = lnk_stat.rx_words;
        hwif_in.lnk.TX_DATA_WORDS.count.next    = lnk_stat.tx_data_words;
        hwif_in.lnk.TX_IDLE_WORDS.count.next    = lnk_stat.tx_idle_words;
        hwif_in.lnk.RX_OVERFLOWS.count.next     = lnk_stat.rx_overflows;
        hwif_in.lnk.CLK_CORRECTIONS.count.next  = lnk_stat.clk_corrections;
        hwif_in.lnk.BUF_ERRORS.count.next       = lnk_stat.buf_errors;
        hwif_in.lnk.PRBS_ERRORS.count.next      = lnk_stat.prbs_errors;
        hwif_in.lnk.PRBS_WORDS_LO.count.next    = lnk_stat.prbs_words[31:0];
        hwif_in.lnk.PRBS_WORDS_HI.count.next    = lnk_stat.prbs_words[47:32];

        hwif_in.gr.STATUS.sync_state.next       = dl_stat.sync_state;
        hwif_in.gr.CADUS.count.next             = dl_stat.gr_cadus;
        hwif_in.gr.SYNC_LOSSES.count.next       = dl_stat.sync_losses;
        hwif_in.gr.ASM_BIT_ERRORS.count.next    = dl_stat.asm_bit_errors;
        hwif_in.gr.DROPPED_RECORDS.count.next   = dl_stat.dropped_records;
        hwif_in.gr.SKIPPED_IDLE.count.next      = dl_stat.skipped;
        hwif_in.gr.CAPTURED.count.next          = dl_stat.captured;
    end

    always_ff @(posedge clk_ctrl) begin
        irq <= !rst_ctrl &&
               ((hwif_out.sys.IRQ_STATUS.scene_done.value     && hwif_out.sys.IRQ_EN.scene_done.value) ||
                (hwif_out.sys.IRQ_STATUS.error.value          && hwif_out.sys.IRQ_EN.error.value) ||
                (hwif_out.sys.IRQ_STATUS.link_lock_loss.value && hwif_out.sys.IRQ_EN.link_lock_loss.value) ||
                (hwif_out.sys.IRQ_STATUS.sync_loss.value      && hwif_out.sys.IRQ_EN.sync_loss.value) ||
                (hwif_out.sys.IRQ_STATUS.ecc_uncorrectable.value && hwif_out.sys.IRQ_EN.ecc_uncorrectable.value) ||
                (hwif_out.sys.IRQ_STATUS.stall.value          && hwif_out.sys.IRQ_EN.stall.value) ||
                (hwif_out.sys.IRQ_STATUS.sem_uncorrectable.value && hwif_out.sys.IRQ_EN.sem_uncorrectable.value));
        gt_reset_all  <= rst_ctrl || hwif_out.lnk.CTRL.gt_reset.value;
        gt_reset_rx   <= hwif_out.lnk.CTRL.rx_reset.value;
        sfp_tx_enable <= {2{hwif_out.lnk.CTRL.tx_enable.value && !rst_ctrl}};
    end
endmodule
