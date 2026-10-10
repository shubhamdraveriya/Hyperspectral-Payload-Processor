`timescale 1ns / 1ps

// Pushbroom sensor emulator (SPEC.md 6.1).
//
//   clk_sys    : DMA stream in (128-bit, 8 samples per beat, sample 0 in [15:0])
//                -> 32 KB prefetch FIFO
//   clk_sensor : every line_period cycles a line starts and its Nx*Nz samples
//                leave back to back in BIL order, from the prefetch FIFO or a
//                test pattern. The output never waits: a sample missing from
//                the prefetch FIFO is sent as 0 and counted (emulator underrun),
//                so lines keep their length.
//                -> output FIFO
//   clk_sys    : sample stream out (no back-pressure: the consumer runs at
//                clk_sys and always accepts)
//
// Setting cfg.run starts a run, which begins with a new scene. The run ends
// after cfg.lines lines (status.done until run is cleared) or, with cfg.lines
// = 0, when run is cleared (a started line completes).
// A line takes Nx*Nz + 1 cycles, so line_period must be at least that, or the
// next line starts late (counted in period_overruns). The output FIFO drains at
// clk_sys, faster than clk_sensor fills it, so dropped_samples stays 0 unless
// the clocks are misconfigured (SE-04). Counters saturate; cnt_clr clears them.
module sensor_emulator import frontend_pkg::*; (
    input  logic         clk_sys,
    input  logic         rst_sys,
    input  logic [127:0] s_axis_tdata,
    input  logic         s_axis_tvalid,
    output logic         s_axis_tready,

    output logic         m_valid,
    output logic [15:0]  m_data,
    output logic [1:0]   m_user,        // [0] first sample of a scene, [1] first of a line
    output logic         m_last,        // last sample of a line

    input  logic         clk_sensor,
    input  logic         rst_sensor,
    input  fe_cfg_t      cfg,
    input  logic         cnt_clr,
    output se_status_t   status
);
    // ---------------------------------------------------------------------
    // Prefetch FIFO: clk_sys -> clk_sensor
    // ---------------------------------------------------------------------
    logic         pf_full, pf_empty, pf_wr_busy, pf_rd_busy, pf_rd_en;
    logic [127:0] pf_dout;

    assign s_axis_tready = !pf_full && !pf_wr_busy;

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE    ("block"),
        .FIFO_WRITE_DEPTH    (2048),         // 32 KB
        .WRITE_DATA_WIDTH    (128),
        .READ_DATA_WIDTH     (128),
        .READ_MODE           ("fwft"),
        .FIFO_READ_LATENCY   (0),
        .CDC_SYNC_STAGES     (2),
        .USE_ADV_FEATURES    ("0000"),
        .WR_DATA_COUNT_WIDTH (12),
        .RD_DATA_COUNT_WIDTH (12),
        .PROG_FULL_THRESH    (10),
        .PROG_EMPTY_THRESH   (10),
        .DOUT_RESET_VALUE    ("0"),
        .FULL_RESET_VALUE    (0),
        .ECC_MODE            ("no_ecc"),
        .RELATED_CLOCKS      (0),
        .SIM_ASSERT_CHK      (0),
        .WAKEUP_TIME         (0)
    ) u_prefetch (
        .sleep         (1'b0),
        .rst           (rst_sys),
        .wr_clk        (clk_sys),
        .wr_en         (s_axis_tvalid && s_axis_tready),
        .din           (s_axis_tdata),
        .full          (pf_full),
        .prog_full     (),
        .wr_data_count (),
        .overflow      (),
        .wr_rst_busy   (pf_wr_busy),
        .almost_full   (),
        .wr_ack        (),
        .rd_clk        (clk_sensor),
        .rd_en         (pf_rd_en),
        .dout          (pf_dout),
        .empty         (pf_empty),
        .prog_empty    (),
        .rd_data_count (),
        .underflow     (),
        .rd_rst_busy   (pf_rd_busy),
        .almost_empty  (),
        .data_valid    (),
        .injectsbiterr (1'b0),
        .injectdbiterr (1'b0),
        .sbiterr       (),
        .dbiterr       ()
    );

    // ---------------------------------------------------------------------
    // Line timing (clk_sensor)
    // ---------------------------------------------------------------------
    logic [31:0] tmr;
    logic        active, pending;
    logic [15:0] x, z, y;
    logic [15:0] lfsr;
    logic [31:0] run_lines;             // lines started in this run

    wire limit      = (cfg.lines != 32'd0) && (run_lines == cfg.lines);
    wire tick       = cfg.run && !limit && (tmr == 32'd0);
    wire last_x     = (x == cfg.nx - 16'd1);
    wire last_z     = (z == cfg.nz - 16'd1);
    wire start_line = !active && !limit && (tick || pending);

    se_status_t st;                     // counters and timestamps (busy/done unused)
    always_comb begin
        status      = st;
        status.busy = (cfg.run && !limit) || active;
        status.done = cfg.run && limit && !active;
    end

    wire emit       = active;
    wire [15:0] mask = 16'((17'd1 << cfg.depth) - 17'd1);

    // 128 -> 16 gearbox on the prefetch FIFO output
    logic [111:0] gear;                 // samples still to send from the current word
    logic [2:0]   gear_n;               // how many
    wire          use_dma   = (cfg.source == SRC_DMA);
    wire          from_gear = (gear_n != 3'd0);
    wire          dma_avail = from_gear || (!pf_empty && !pf_rd_busy);
    assign pf_rd_en = emit && use_dma && !from_gear && !pf_empty && !pf_rd_busy;

    logic [15:0] sample;
    always_comb begin
        case (cfg.source)
            SRC_RAMP: sample = (x + z + y) & mask;
            SRC_LFSR: sample = lfsr & mask;
            default:  sample = !dma_avail ? 16'd0 : from_gear ? gear[15:0] : pf_dout[15:0];
        endcase
    end

    // output register (clk_sensor)
    logic        o_valid, o_last;
    logic [15:0] o_data;
    logic [1:0]  o_user;
    logic        of_full, of_wr_busy;

    always_ff @(posedge clk_sensor) begin
        if (rst_sensor) begin
            tmr <= '0;  active <= 1'b0;  pending <= 1'b0;
            {x, z, y} <= '0;
            run_lines <= '0;
            lfsr <= 16'hACE1;
            gear_n <= '0;
            o_valid <= 1'b0;
            st <= '0;
        end else begin
            o_valid <= 1'b0;

            // line period timer; a new run starts a new scene
            if (!cfg.run) begin
                tmr <= '0;
                pending <= 1'b0;
            end else begin
                tmr <= (tmr == 32'd0) ? cfg.line_period - 32'd1 : tmr - 32'd1;
                if (tick && active) begin
                    pending <= 1'b1;
                    st.period_overruns <= sat_inc(st.period_overruns);
                end
            end
            if (!cfg.run && !active) begin
                y         <= '0;
                run_lines <= '0;
            end

            if (start_line) begin
                active    <= 1'b1;
                pending   <= 1'b0;
                run_lines <= run_lines + 32'd1;
                x <= '0;
                z <= '0;
                st.line_timestamp <= st.line_timestamp + 24'd1;
                if (y == 16'd0) begin
                    st.scene_timestamp <= st.line_timestamp;
                    st.scenes_started  <= sat_inc(st.scenes_started);
                    lfsr <= 16'hACE1;
                end
            end

            if (emit) begin
                o_valid <= 1'b1;
                o_data  <= sample;
                o_user  <= {x == 16'd0 && z == 16'd0, x == 16'd0 && z == 16'd0 && y == 16'd0};
                o_last  <= last_x && last_z;
                if (use_dma && !dma_avail) st.underrun_samples <= sat_inc(st.underrun_samples);
                if (cfg.source == SRC_LFSR)
                    lfsr <= lfsr[0] ? (lfsr >> 1) ^ 16'hB400 : lfsr >> 1;
                // gearbox
                if (use_dma) begin
                    if (from_gear) begin
                        gear   <= gear >> 16;
                        gear_n <= gear_n - 3'd1;
                    end else if (pf_rd_en) begin
                        gear   <= pf_dout[127:16];
                        gear_n <= 3'd7;
                    end
                end
                // position
                if (!last_x) begin
                    x <= x + 16'd1;
                end else begin
                    x <= '0;
                    if (!last_z) begin
                        z <= z + 16'd1;
                    end else begin
                        active <= 1'b0;
                        st.lines_emitted <= sat_inc(st.lines_emitted);
                        y <= (y == cfg.ny - 16'd1) ? 16'd0 : y + 16'd1;
                    end
                end
            end

            if (o_valid && (of_full || of_wr_busy)) st.dropped_samples <= sat_inc(st.dropped_samples);

            if (cnt_clr) begin
                st.lines_emitted    <= '0;
                st.scenes_started   <= '0;
                st.underrun_samples <= '0;
                st.period_overruns  <= '0;
                st.dropped_samples  <= '0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Output FIFO: clk_sensor -> clk_sys (drains faster than it fills)
    // ---------------------------------------------------------------------
    logic        of_empty, of_rd_busy;
    logic [18:0] of_dout;
    wire         of_rd = !of_empty && !of_rd_busy;

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE    ("distributed"),
        .FIFO_WRITE_DEPTH    (32),
        .WRITE_DATA_WIDTH    (19),
        .READ_DATA_WIDTH     (19),
        .READ_MODE           ("fwft"),
        .FIFO_READ_LATENCY   (0),
        .CDC_SYNC_STAGES     (2),
        .USE_ADV_FEATURES    ("0000"),
        .WR_DATA_COUNT_WIDTH (6),
        .RD_DATA_COUNT_WIDTH (6),
        .PROG_FULL_THRESH    (10),
        .PROG_EMPTY_THRESH   (10),
        .DOUT_RESET_VALUE    ("0"),
        .FULL_RESET_VALUE    (0),
        .ECC_MODE            ("no_ecc"),
        .RELATED_CLOCKS      (0),
        .SIM_ASSERT_CHK      (0),
        .WAKEUP_TIME         (0)
    ) u_out (
        .sleep         (1'b0),
        .rst           (rst_sensor),
        .wr_clk        (clk_sensor),
        .wr_en         (o_valid && !of_full && !of_wr_busy),
        .din           ({o_last, o_user, o_data}),
        .full          (of_full),
        .prog_full     (),
        .wr_data_count (),
        .overflow      (),
        .wr_rst_busy   (of_wr_busy),
        .almost_full   (),
        .wr_ack        (),
        .rd_clk        (clk_sys),
        .rd_en         (of_rd),
        .dout          (of_dout),
        .empty         (of_empty),
        .prog_empty    (),
        .rd_data_count (),
        .underflow     (),
        .rd_rst_busy   (of_rd_busy),
        .almost_empty  (),
        .data_valid    (),
        .injectsbiterr (1'b0),
        .injectdbiterr (1'b0),
        .sbiterr       (),
        .dbiterr       ()
    );

    always_ff @(posedge clk_sys) begin
        if (rst_sys) begin
            m_valid <= 1'b0;
        end else begin
            m_valid <= of_rd;
            {m_last, m_user, m_data} <= of_dout;
        end
    end
endmodule
