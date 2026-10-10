`timescale 1ns / 1ps

// HPDP programmable-logic core (M4, CAPTURE_CMP mode):
//
//   s_axis_dma (MM2S) --+--> sensor_frontend --> c123_core --> m_axis_cmp (S2MM)
//                       '--> calibration table          '--> discarded (MODE.capture = 0)
//
// Registers (regs/hpdp.rdl, PeakRDL) sit on clk_ctrl behind s_axil. The
// configuration reaches clk_sys and clk_sensor, and status comes back, through
// cdc_bus snapshots; events (counter clears, table commands) travel as
// sequence counts inside those snapshots. Each domain echoes the count of
// register writes it has received, so SYS.STATUS.cfg_pending tells software
// when a configuration has arrived everywhere. SYS.CTRL.soft_reset holds the
// data path of both clocks in reset (not the registers or the crossings).
module hpdp_core
    import frontend_pkg::*;
    import c123_pkg::*;
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

    // AXI4-Lite control (clk_ctrl), 512 KB window
    input  logic         s_axil_awvalid,
    output logic         s_axil_awready,
    input  logic [18:0]  s_axil_awaddr,
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
    input  logic [18:0]  s_axil_araddr,
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

    output logic         irq                // level, clk_ctrl
);
    // ---------------------------------------------------------------------
    // Snapshot buses between the clock domains
    // ---------------------------------------------------------------------
    typedef struct packed {
        logic        soft_reset;
        logic        capture;
        logic        to_table;
        fe_cfg_t     fe;
        cfg_t        cmp;
        logic [7:0]  wr_seq;                    // register write count
        logic [7:0]  ct_clr_seq, cmp_clr_seq;
        logic [7:0]  tbl_seq;                   // table command count
        logic        tbl_write;
        logic [17:0] tbl_addr;
        logic [32:0] tbl_wdata;
    } sys_cfg_t;

    typedef struct packed {
        logic        soft_reset;
        fe_cfg_t     fe;
        logic [7:0]  wr_seq;
        logic [7:0]  se_clr_seq;
    } sen_cfg_t;

    typedef struct packed {
        se_status_t  se;
        logic [7:0]  wr_seq_echo;
    } sen_stat_t;

    typedef struct packed {
        fe_status_t  fe;
        logic [7:0]  wr_seq_echo;
        logic [32:0] tbl_rdata;
        logic [7:0]  tbl_done_seq;              // count of the last completed table command
        logic        cmp_busy, cmp_cfg_error;
        logic [31:0] scenes_done, samples_in, bytes_out, last_bytes, last_cycles, out_stalls;
    } sys_stat_t;

    // ---------------------------------------------------------------------
    // Registers (clk_ctrl)
    // ---------------------------------------------------------------------
    wire rst_ctrl = !rst_ctrl_n;

    hpdp_regs_pkg::hpdp_regs__in_t  hwif_in;
    hpdp_regs_pkg::hpdp_regs__out_t hwif_out;

    hpdp_regs u_regs (
        .clk (clk_ctrl),
        .rst (rst_ctrl),
        .s_axil_awready, .s_axil_awvalid, .s_axil_awaddr, .s_axil_awprot,
        .s_axil_wready, .s_axil_wvalid, .s_axil_wdata, .s_axil_wstrb,
        .s_axil_bready, .s_axil_bvalid, .s_axil_bresp,
        .s_axil_arready, .s_axil_arvalid, .s_axil_araddr, .s_axil_arprot,
        .s_axil_rready, .s_axil_rvalid, .s_axil_rdata, .s_axil_rresp,
        .hwif_in, .hwif_out
    );

    // Event counts (clk_ctrl)
    logic [7:0] wr_seq, se_clr_seq, ct_clr_seq, cmp_clr_seq, tbl_seq;
    logic       tbl_write;

    always_ff @(posedge clk_ctrl) begin
        if (rst_ctrl) begin
            {wr_seq, se_clr_seq, ct_clr_seq, cmp_clr_seq, tbl_seq} <= '0;
            tbl_write <= 1'b0;
        end else begin
            if (s_axil_bvalid && s_axil_bready) wr_seq <= wr_seq + 8'd1;
            if (hwif_out.se.CNT_CLEAR.clear.value)  se_clr_seq  <= se_clr_seq + 8'd1;
            if (hwif_out.ct.CNT_CLEAR.clear.value)  ct_clr_seq  <= ct_clr_seq + 8'd1;
            if (hwif_out.cmp.CNT_CLEAR.clear.value) cmp_clr_seq <= cmp_clr_seq + 8'd1;
            if (hwif_out.rc.TBL_CMD.write.value || hwif_out.rc.TBL_CMD.read.value) begin
                tbl_seq   <= tbl_seq + 8'd1;
                tbl_write <= hwif_out.rc.TBL_CMD.write.value;
            end
        end
    end

    fe_cfg_t fe_ctrl;
    cfg_t    cmp_ctrl;
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
    end

    sys_cfg_t  sys_cfg_src, sys_cfg;        // clk_ctrl -> clk_sys
    sen_cfg_t  sen_cfg_src, sen_cfg;        // clk_ctrl -> clk_sensor
    sys_stat_t sys_stat_src, sys_stat;      // clk_sys -> clk_ctrl
    sen_stat_t sen_stat_src, sen_stat;      // clk_sensor -> clk_ctrl
    se_status_t se_stat;
    assign se_stat = sen_stat.se;

    always_comb begin
        sys_cfg_src.soft_reset  = hwif_out.sys.CTRL.soft_reset.value;
        sys_cfg_src.capture     = hwif_out.sys.MODE.capture.value;
        sys_cfg_src.to_table    = hwif_out.sys.DMA_ROUTE.to_table.value;
        sys_cfg_src.fe          = fe_ctrl;
        sys_cfg_src.cmp         = cmp_ctrl;
        sys_cfg_src.wr_seq      = wr_seq;
        sys_cfg_src.ct_clr_seq  = ct_clr_seq;
        sys_cfg_src.cmp_clr_seq = cmp_clr_seq;
        sys_cfg_src.tbl_seq     = tbl_seq;
        sys_cfg_src.tbl_write   = tbl_write;
        sys_cfg_src.tbl_addr    = hwif_out.rc.TBL_ADDR.addr.value;
        sys_cfg_src.tbl_wdata   = {hwif_out.rc.TBL_WDATA_HI.bad.value,
                                   hwif_out.rc.TBL_WDATA_LO.gain.value,
                                   hwif_out.rc.TBL_WDATA_LO.dark.value};

        sen_cfg_src.soft_reset  = hwif_out.sys.CTRL.soft_reset.value;
        sen_cfg_src.fe          = fe_ctrl;
        sen_cfg_src.wr_seq      = wr_seq;
        sen_cfg_src.se_clr_seq  = se_clr_seq;
    end

    wire rst_sys_hard    = !rst_sys_n;
    wire rst_sensor_hard = !rst_sensor_n;

    cdc_bus #(.WIDTH($bits(sys_cfg_t))) u_cdc_sys_cfg (
        .src_clk (clk_ctrl), .src_rst (rst_ctrl), .src_data (sys_cfg_src),
        .dest_clk (clk_sys), .dest_rst (rst_sys_hard), .dest_data (sys_cfg));

    cdc_bus #(.WIDTH($bits(sen_cfg_t))) u_cdc_sen_cfg (
        .src_clk (clk_ctrl), .src_rst (rst_ctrl), .src_data (sen_cfg_src),
        .dest_clk (clk_sensor), .dest_rst (rst_sensor_hard), .dest_data (sen_cfg));

    cdc_bus #(.WIDTH($bits(sys_stat_t))) u_cdc_sys_stat (
        .src_clk (clk_sys), .src_rst (rst_sys_hard), .src_data (sys_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (sys_stat));

    cdc_bus #(.WIDTH($bits(sen_stat_t))) u_cdc_sen_stat (
        .src_clk (clk_sensor), .src_rst (rst_sensor_hard), .src_data (sen_stat_src),
        .dest_clk (clk_ctrl), .dest_rst (rst_ctrl), .dest_data (sen_stat));

    logic locked_ctrl;
    xpm_cdc_single #(.DEST_SYNC_FF(2), .SRC_INPUT_REG(0)) u_cdc_locked (
        .src_clk (1'b0), .src_in (sensor_locked), .dest_clk (clk_ctrl), .dest_out (locked_ctrl));

    // ---------------------------------------------------------------------
    // Data path resets: hard reset or SYS.CTRL.soft_reset
    // ---------------------------------------------------------------------
    logic rst_sys, rst_sensor;
    always_ff @(posedge clk_sys)    rst_sys    <= rst_sys_hard || sys_cfg.soft_reset;
    always_ff @(posedge clk_sensor) rst_sensor <= rst_sensor_hard || sen_cfg.soft_reset;

    // Event counts arriving in the data path clocks -> one-cycle pulses
    logic [7:0] se_clr_seen, ct_clr_seen, cmp_clr_seen, tbl_seen;
    logic       se_clr, ct_clr, cmp_clr;

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
    // Calibration table commands (clk_sys)
    // ---------------------------------------------------------------------
    tbl_cmd_t    tbl_cmd;
    logic [32:0] tbl_rdata;
    logic        tbl_done;
    logic [7:0]  tbl_done_seq;

    always_ff @(posedge clk_sys) begin
        if (rst_sys_hard) begin
            {ct_clr_seen, cmp_clr_seen, tbl_seen, tbl_done_seq} <= '0;
            {ct_clr, cmp_clr} <= '0;
            tbl_cmd.valid <= 1'b0;
        end else begin
            ct_clr       <= (sys_cfg.ct_clr_seq != ct_clr_seen);
            ct_clr_seen  <= sys_cfg.ct_clr_seq;
            cmp_clr      <= (sys_cfg.cmp_clr_seq != cmp_clr_seen);
            cmp_clr_seen <= sys_cfg.cmp_clr_seq;

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
        .m_axis_tdata   (fe_tdata),
        .m_axis_tuser   (fe_tuser),
        .m_axis_tlast   (fe_tlast),
        .m_axis_tvalid  (fe_tvalid),
        .m_axis_tready  (fe_tready),
        .se_status      (sen_stat_src.se),
        .fe_status      (fe_stat)
    );

    // ---------------------------------------------------------------------
    // Compressor and its statistics (CMP-09)
    // ---------------------------------------------------------------------
    logic [63:0] c_tdata;
    logic [7:0]  c_tkeep;
    logic        c_tlast, c_tvalid, c_tready, cfg_error;
    wire         capture = sys_cfg.capture;

    c123_core #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_cmp (
        .clk           (clk_sys),
        .rst           (rst_sys),
        .cfg           (sys_cfg.cmp),
        .s_axis_tdata  (fe_tdata),
        .s_axis_tuser  (fe_tuser[0]),
        .s_axis_tvalid (fe_tvalid),
        .s_axis_tready (fe_tready),
        .m_axis_tdata  (c_tdata),
        .m_axis_tkeep  (c_tkeep),
        .m_axis_tlast  (c_tlast),
        .m_axis_tvalid (c_tvalid),
        .m_axis_tready (c_tready),
        .cfg_error     (cfg_error)
    );

    assign m_axis_cmp_tdata  = c_tdata;
    assign m_axis_cmp_tkeep  = c_tkeep;
    assign m_axis_cmp_tlast  = c_tlast;
    assign m_axis_cmp_tvalid = c_tvalid && capture;
    assign c_tready          = capture ? m_axis_cmp_tready : 1'b1;

    wire in_fire  = fe_tvalid && fe_tready;
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
                if (fe_tuser[0]) begin
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
                    if (!(in_fire && fe_tuser[0]) && in_idx == !out_idx) cmp_busy <= 1'b0;
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

    always_comb begin
        sys_stat_src.fe            = fe_stat;
        sys_stat_src.wr_seq_echo   = sys_cfg.wr_seq;
        sys_stat_src.tbl_rdata     = tbl_rdata;
        sys_stat_src.tbl_done_seq  = tbl_done_seq;
        sys_stat_src.cmp_busy      = cmp_busy;
        sys_stat_src.cmp_cfg_error = cfg_error;
        sys_stat_src.scenes_done   = scenes_done;
        sys_stat_src.samples_in    = samples_in;
        sys_stat_src.bytes_out     = bytes_out;
        sys_stat_src.last_bytes    = last_bytes;
        sys_stat_src.last_cycles   = last_cycles;
        sys_stat_src.out_stalls    = out_stalls;
    end

    // ---------------------------------------------------------------------
    // Status registers and interrupts (clk_ctrl)
    // ---------------------------------------------------------------------
    logic [31:0] scenes_prev, errors_prev;
    wire  [31:0] errors_now = se_stat.underrun_samples | se_stat.period_overruns |
                              se_stat.dropped_samples | sys_stat.fe.dropped_lines;
    wire  [33:0] error_sum  = 34'(se_stat.underrun_samples) + 34'(se_stat.period_overruns) +
                              34'(se_stat.dropped_samples) + 34'(sys_stat.fe.dropped_lines);
    logic [33:0] error_sum_prev;

    always_ff @(posedge clk_ctrl) begin
        if (rst_ctrl) begin
            scenes_prev    <= '0;
            error_sum_prev <= '0;
        end else begin
            scenes_prev    <= sys_stat.scenes_done;
            error_sum_prev <= error_sum;
        end
    end

    always_comb begin
        hwif_in.sys.STATUS.busy.next          = se_stat.busy;
        hwif_in.sys.STATUS.error.next         = errors_now != 32'd0;
        hwif_in.sys.STATUS.sensor_locked.next = locked_ctrl;
        hwif_in.sys.STATUS.cfg_pending.next   = wr_seq != sys_stat.wr_seq_echo ||
                                                wr_seq != sen_stat.wr_seq_echo;
        hwif_in.sys.IRQ_STATUS.scene_done.hwset = sys_stat.scenes_done > scenes_prev;
        hwif_in.sys.IRQ_STATUS.error.hwset      = error_sum > error_sum_prev;
        hwif_in.sys.BUILD_ID.build_id.next    = BUILD_ID;

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

        hwif_in.cmp.STATUS.busy.next            = sys_stat.cmp_busy;
        hwif_in.cmp.STATUS.cfg_error.next       = sys_stat.cmp_cfg_error;
        hwif_in.cmp.SCENES_DONE.count.next      = sys_stat.scenes_done;
        hwif_in.cmp.SAMPLES_IN.count.next       = sys_stat.samples_in;
        hwif_in.cmp.BYTES_OUT.count.next        = sys_stat.bytes_out;
        hwif_in.cmp.LAST_BYTES.count.next       = sys_stat.last_bytes;
        hwif_in.cmp.LAST_CYCLES.count.next      = sys_stat.last_cycles;
        hwif_in.cmp.OUT_STALLS.count.next       = sys_stat.out_stalls;
    end

    always_ff @(posedge clk_ctrl) begin
        irq <= !rst_ctrl &&
               ((hwif_out.sys.IRQ_STATUS.scene_done.value && hwif_out.sys.IRQ_EN.scene_done.value) ||
                (hwif_out.sys.IRQ_STATUS.error.value && hwif_out.sys.IRQ_EN.error.value));
    end
endmodule
