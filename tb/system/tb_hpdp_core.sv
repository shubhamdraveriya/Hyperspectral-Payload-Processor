`timescale 1ns / 1ps

// System test of hpdp_core through its register interface, following the
// sequence the bare-metal application uses (sw/baremetal/hpdp_run):
//
//   identify -> configure -> load the calibration table by "DMA" -> check it
//   through the table debug registers -> stream the raw scenes -> run the
//   sensor -> capture the compressed scenes -> check counters, interrupt,
//   counter clears -> run the acquisition a second time -> soft reset.
//
// Vectors come from tb/frontend/gen_vectors.py (+DIR=<test directory>).
// clk_ctrl 100 MHz, clk_sys 250 MHz, clk_sensor 100 MHz (unrelated phase).
// The DMA streams have random gaps, the S2MM side random back-pressure.
module tb_hpdp_core;
    import hpdp_regs_addr_pkg::*;

    localparam int          NX_MAX   = 64;
    localparam int          NZ_MAX   = 64;
    localparam logic [31:0] BUILD_ID = 32'hC0DE_1234;
    localparam int          PASSES   = 2;

    logic clk_ctrl = 1'b0, clk_sys = 1'b0, clk_sensor = 1'b0;
    logic rst_n = 1'b0;
    always #5 clk_ctrl = ~clk_ctrl;
    always #2 clk_sys  = ~clk_sys;
    initial begin
        #1.7;
        forever #5 clk_sensor = ~clk_sensor;
    end

    // AXI4-Lite
    logic        awvalid = 1'b0, wvalid = 1'b0, arvalid = 1'b0;
    logic        awready, wready, bvalid, arready, rvalid;
    logic [18:0] awaddr = '0, araddr = '0;
    logic [31:0] wdata = '0, rdata;
    logic [1:0]  bresp, rresp;

    // streams
    logic [127:0] dma_tdata = '0;
    logic         dma_tlast = 1'b0, dma_tvalid = 1'b0, dma_tready;
    logic [63:0]  cmp_tdata;
    logic [7:0]   cmp_tkeep;
    logic         cmp_tlast, cmp_tvalid, cmp_tready = 1'b0;
    logic         irq;

    hpdp_core #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX), .BUILD_ID(BUILD_ID)) dut (
        .clk_ctrl, .rst_ctrl_n (rst_n),
        .clk_sys, .rst_sys_n (rst_n),
        .clk_sensor, .rst_sensor_n (rst_n),
        .sensor_locked (1'b1),
        .s_axil_awvalid (awvalid), .s_axil_awready (awready), .s_axil_awaddr (awaddr),
        .s_axil_awprot  (3'b000),
        .s_axil_wvalid  (wvalid), .s_axil_wready (wready), .s_axil_wdata (wdata),
        .s_axil_wstrb   (4'hF),
        .s_axil_bvalid  (bvalid), .s_axil_bready (1'b1), .s_axil_bresp (bresp),
        .s_axil_arvalid (arvalid), .s_axil_arready (arready), .s_axil_araddr (araddr),
        .s_axil_arprot  (3'b000),
        .s_axil_rvalid  (rvalid), .s_axil_rready (1'b1), .s_axil_rdata (rdata),
        .s_axil_rresp   (rresp),
        .s_axis_dma_tdata (dma_tdata), .s_axis_dma_tlast (dma_tlast),
        .s_axis_dma_tvalid (dma_tvalid), .s_axis_dma_tready (dma_tready),
        .m_axis_cmp_tdata (cmp_tdata), .m_axis_cmp_tkeep (cmp_tkeep), .m_axis_cmp_tlast (cmp_tlast),
        .m_axis_cmp_tvalid (cmp_tvalid), .m_axis_cmp_tready (cmp_tready),
        .irq
    );

    // -----------------------------------------------------------------
    // AXI4-Lite master
    // -----------------------------------------------------------------
    int bus_err = 0;

    task automatic wr(logic [31:0] addr, logic [31:0] data);
        @(posedge clk_ctrl);
        awvalid <= 1'b1;  awaddr <= addr[18:0];
        wvalid  <= 1'b1;  wdata  <= data;
        fork
            begin @(posedge clk_ctrl iff awready); awvalid <= 1'b0; end
            begin @(posedge clk_ctrl iff wready);  wvalid  <= 1'b0; end
        join
        @(posedge clk_ctrl iff bvalid);
        if (bresp != 2'b00) begin
            bus_err++;
            $display("  write %05h: bresp %0d", addr, bresp);
        end
    endtask

    task automatic rd(logic [31:0] addr, output logic [31:0] data);
        @(posedge clk_ctrl);
        arvalid <= 1'b1;  araddr <= addr[18:0];
        @(posedge clk_ctrl iff arready);
        arvalid <= 1'b0;
        @(posedge clk_ctrl iff rvalid);
        data = rdata;
        if (rresp != 2'b00) begin
            bus_err++;
            $display("  read %05h: rresp %0d", addr, rresp);
        end
    endtask

    int errors = 0;

    task automatic expect_reg(string name, logic [31:0] addr, logic [31:0] exp);
        logic [31:0] v;
        rd(addr, v);
        if (v !== exp) begin
            errors++;
            $display("  %s = %0d (%08h), expected %0d (%08h)", name, v, v, exp, exp);
        end
    endtask

    // wait until (register & mask) == value; the crossings take ~1 us
    task automatic poll(string name, logic [31:0] addr, logic [31:0] mask, logic [31:0] value, int timeout_us);
        logic [31:0] v;
        realtime t0;
        t0 = $realtime;
        forever begin
            rd(addr, v);
            if ((v & mask) == value) return;
            if ($realtime - t0 > timeout_us * 1000.0) begin
                errors++;
                $display("  timeout waiting for %s: %08h", name, v);
                return;
            end
            repeat (20) @(posedge clk_ctrl);
        end
    endtask

    // -----------------------------------------------------------------
    // Vectors
    // -----------------------------------------------------------------
    string        dir;
    int           nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk, wb, rw, ud;
    int           source, period, bypass, scenes, dma_pct, out_pct, drop_t, overrun_t, limit_t;
    int           exp_clamped, exp_replaced;
    logic [7:0]   expected [$];
    logic [127:0] tbl [$], dma [$];
    int           sizes [$];

    task automatic load_vectors();
        int fd, v, n;
        logic [127:0] w;
        fd = $fopen({dir, "/cfg.txt"}, "r");
        if (fd == 0) $fatal(1, "cannot open %s/cfg.txt", dir);
        n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                    nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk,
                    wb, rw, ud, source, period, bypass, scenes, dma_pct, out_pct, drop_t, overrun_t, limit_t);
        if (n != 28) $fatal(1, "bad cfg.txt (%0d fields)", n);
        $fclose(fd);
        // (xsim pushes X when push_back is the bare body of the while loop: keep begin/end)
        fd = $fopen({dir, "/expected.hex"}, "r");
        while ($fscanf(fd, "%h", v) == 1) begin
            expected.push_back(8'(v));
        end
        $fclose(fd);
        fd = $fopen({dir, "/tbl.hex"}, "r");
        while ($fscanf(fd, "%h", w) == 1) begin
            tbl.push_back(w);
        end
        $fclose(fd);
        fd = $fopen({dir, "/dma.hex"}, "r");
        while ($fscanf(fd, "%h", w) == 1) begin
            dma.push_back(w);
        end
        $fclose(fd);
        fd = $fopen({dir, "/sizes.txt"}, "r");
        while ($fscanf(fd, "%d", v) == 1) begin
            sizes.push_back(v);
        end
        $fclose(fd);
        fd = $fopen({dir, "/stats.txt"}, "r");
        v = $fscanf(fd, "%d %d", exp_clamped, exp_replaced);
        $fclose(fd);
        if (source != 0) $fatal(1, "tb_hpdp_core needs a DMA-source test");
    endtask

    function automatic logic [32:0] tbl_entry(int i);
        return tbl[i / 2][64 * (i % 2) +: 33];
    endfunction

    // -----------------------------------------------------------------
    // DMA MM2S model: sends mm2s_q, tlast on the last beat of each transfer
    // -----------------------------------------------------------------
    typedef struct packed { logic last; logic [127:0] data; } beat_t;
    beat_t mm2s_q [$];

    always @(posedge clk_sys) begin
        if (!rst_n) begin
            dma_tvalid <= 1'b0;
        end else if (!dma_tvalid || dma_tready) begin
            if (mm2s_q.size() > 0 && $urandom_range(0, 99) < dma_pct) begin
                beat_t b;
                b = mm2s_q.pop_front();
                dma_tvalid <= 1'b1;
                dma_tdata  <= b.data;
                dma_tlast  <= b.last;
            end else begin
                dma_tvalid <= 1'b0;
            end
        end
    end

    task automatic mm2s(ref logic [127:0] beats [$]);
        foreach (beats[i]) mm2s_q.push_back('{last: i == beats.size() - 1, data: beats[i]});
    endtask

    task automatic mm2s_wait();
        wait (mm2s_q.size() == 0);
        @(posedge clk_sys iff !dma_tvalid || dma_tready);
    endtask

    // -----------------------------------------------------------------
    // S2MM capture: every byte against the model, tlast per scene
    // -----------------------------------------------------------------
    int pos = 0, byte_err = 0, lasts = 0, last_err = 0, scene_end = 0, pass_base = 0;
    logic [7:0] got_b, exp_b;
    always @(posedge clk_sys) begin
        if (rst_n && cmp_tvalid && cmp_tready) begin
            for (int b = 0; b < 8; b++) begin
                if (cmp_tkeep[b]) begin
                    got_b = cmp_tdata[8 * b +: 8];
                    exp_b = (pos - pass_base < expected.size()) ? expected[pos - pass_base] : 8'hxx;
                    if (pos - pass_base >= expected.size() || got_b !== exp_b) begin
                        byte_err++;
                        if (byte_err <= 5) $display("  byte %0d: got %02h, expected %02h", pos, got_b, exp_b);
                    end
                    pos++;
                end
            end
            if (cmp_tlast) begin
                scene_end += sizes[lasts % scenes];
                if (pos != scene_end) begin
                    last_err++;
                    $display("  tlast after byte %0d, expected after %0d", pos, scene_end);
                end
                lasts++;
                if (lasts % scenes == 0) pass_base = pos;
            end
        end
        cmp_tready <= ($urandom_range(0, 99) < out_pct);
    end

    // -----------------------------------------------------------------
    // Test sequence
    // -----------------------------------------------------------------
    task automatic configure();
        wr(SYS_NX, nx);  wr(SYS_NY, ny);  wr(SYS_NZ, nz);  wr(SYS_DEPTH, depth);
        wr(SYS_MODE, 1);                                    // capture
        wr(SYS_IRQ_EN, 32'h3);
        wr(SE_LINE_PERIOD, period);
        wr(SE_LINES, scenes * ny);
        wr(SE_SOURCE, source);
        wr(RC_CTRL, !bypass);
        wr(CMP_PRED, bands | (full << 4) | (ls << 5) | (omega << 8) | (r << 16));
        wr(CMP_WUPD, (vmin & 32'h1F) | ((vmax & 32'h1F) << 8) | (tinc << 16));
        wr(CMP_CODER, umax | (g0 << 8) | (gs << 12) | (kk << 16) | (wb << 24));
        wr(CMP_OPTS, rw | (ud << 8));
    endtask

    // every register write applied in all clock domains
    task automatic sync_cfg();
        poll("cfg_pending", SYS_STATUS, 32'h8, 32'h0, 5);
    endtask

    task automatic table_access(logic write, int addr, logic [32:0] wdata, output logic [32:0] rdata);
        logic [31:0] lo, hi;
        wr(RC_TBL_ADDR, addr);
        if (write) begin
            wr(RC_TBL_WDATA_LO, wdata[31:0]);
            wr(RC_TBL_WDATA_HI, wdata[32]);
            wr(RC_TBL_CMD, 32'h1);
        end else begin
            wr(RC_TBL_CMD, 32'h2);
        end
        poll("RC table command", RC_STATUS, 32'h1, 32'h0, 20);
        rd(RC_TBL_RDATA_LO, lo);
        rd(RC_TBL_RDATA_HI, hi);
        rdata = {hi[0], lo};
    endtask

    task automatic check_table();
        int n, probe [3];
        logic [32:0] got, orig, alt;
        n = nx * nz;
        probe = '{0, n / 2, n - 1};
        foreach (probe[k]) begin
            table_access(1'b0, probe[k], '0, got);
            if (got !== tbl_entry(probe[k])) begin
                errors++;
                $display("  table entry %0d: read %09h, loaded %09h", probe[k], got, tbl_entry(probe[k]));
            end
        end
        orig = tbl_entry(n - 1);
        alt  = orig ^ 33'h1_0F0F_F0F0;
        table_access(1'b1, n - 1, alt, got);
        table_access(1'b0, n - 1, '0, got);
        if (got !== alt) begin
            errors++;
            $display("  table write: read back %09h, wrote %09h", got, alt);
        end
        table_access(1'b1, n - 1, orig, got);
    endtask

    task automatic acquire(int pass);
        int timeout_us;
        timeout_us = 20 + (scenes * ny * period / 100 + expected.size() / 2) * 2 * 100 / (out_pct + 1);
        wr(SYS_DMA_ROUTE, 0);
        sync_cfg();
        mm2s(dma);
        repeat (400) @(posedge clk_sys);               // prefill the emulator's buffer
        wr(SE_CTRL, 1);
        poll("SE done", SE_STATUS, 32'h3, 32'h2, timeout_us);
        poll("scenes compressed", CMP_SCENES_DONE, 32'hFFFF_FFFF, (pass + 1) * scenes, timeout_us);
        wr(SE_CTRL, 0);
        mm2s_wait();
    endtask

    initial begin
        logic [31:0] v;
        int total, last_size;
        if (!$value$plusargs("DIR=%s", dir)) $fatal(1, "+DIR=<test directory> missing");
        load_vectors();
        total = nx * ny * nz * scenes;
        last_size = sizes[scenes - 1];
        $display("tb_hpdp_core: %s, %0dx%0dx%0d, %0d scenes, %0d passes, dma %0d%%, out %0d%%",
                 dir, nx, ny, nz, scenes, PASSES, dma_pct, out_pct);

        repeat (30) @(posedge clk_sensor);
        rst_n <= 1'b1;
        repeat (50) @(posedge clk_ctrl);

        // identification and reset values
        expect_reg("SYS.ID", SYS_ID, 32'h4850_0100);
        expect_reg("SE.ID", SE_ID, 32'h0001_0100);
        expect_reg("RC.ID", RC_ID, 32'h0002_0100);
        expect_reg("CT.ID", CT_ID, 32'h0003_0100);
        expect_reg("CMP.ID", CMP_ID, 32'h0004_0100);
        expect_reg("SYS.BUILD_ID", SYS_BUILD_ID, BUILD_ID);
        expect_reg("SE.LINE_PERIOD reset", SE_LINE_PERIOD, 1000);
        poll("sensor clock locked", SYS_STATUS, 32'h4, 32'h4, 5);

        configure();

        // calibration table by DMA, then through the debug registers
        wr(SYS_DMA_ROUTE, 1);
        sync_cfg();
        mm2s(tbl);
        mm2s_wait();
        poll("table load", RC_TBL_WORDS, 32'hFFFF_FFFF, tbl.size(), 10);
        check_table();

        // two acquisitions, the second without reset
        for (int pass = 0; pass < PASSES; pass++) begin
            acquire(pass);
            expect_reg("SE.LINES_EMITTED", SE_LINES_EMITTED, (pass + 1) * scenes * ny);
            expect_reg("SE.SCENES_STARTED", SE_SCENES_STARTED, (pass + 1) * scenes);
            expect_reg("SE.UNDERRUN_SAMPLES", SE_UNDERRUN_SAMPLES, 0);
            expect_reg("SE.PERIOD_OVERRUNS", SE_PERIOD_OVERRUNS, 0);
            expect_reg("SE.DROPPED_SAMPLES", SE_DROPPED_SAMPLES, 0);
            expect_reg("SE.SCENE_TIMESTAMP", SE_SCENE_TIMESTAMP, ((pass + 1) * scenes - 1) * ny);
            expect_reg("RC.SCENE_CLAMPED", RC_SCENE_CLAMPED, exp_clamped);
            expect_reg("RC.SCENE_REPLACED", RC_SCENE_REPLACED, exp_replaced);
            expect_reg("CT.LINES_OUT", CT_LINES_OUT, (pass + 1) * scenes * ny);
            expect_reg("CT.DROPPED_LINES", CT_DROPPED_LINES, 0);
            expect_reg("CMP.SAMPLES_IN", CMP_SAMPLES_IN, (pass + 1) * total);
            expect_reg("CMP.BYTES_OUT", CMP_BYTES_OUT, (pass + 1) * expected.size());
            expect_reg("CMP.LAST_BYTES", CMP_LAST_BYTES, last_size);
            expect_reg("CMP.STATUS", CMP_STATUS, 0);
            expect_reg("SYS.STATUS", SYS_STATUS, 32'h4);     // locked, not busy, no error
        end
        rd(CMP_LAST_CYCLES, v);
        if (v < nx * ny * nz || v > 4 * nx * ny * nz * period / (nx * nz) + 10000) begin
            errors++;
            $display("  CMP.LAST_CYCLES = %0d, implausible", v);
        end

        // interrupt: scene done raised, error not; write 1 to clear
        expect_reg("SYS.IRQ_STATUS", SYS_IRQ_STATUS, 32'h1);
        if (irq !== 1'b1) begin errors++; $display("  irq not raised"); end
        wr(SYS_IRQ_STATUS, 32'h1);
        expect_reg("SYS.IRQ_STATUS after clear", SYS_IRQ_STATUS, 32'h0);
        repeat (2) @(posedge clk_ctrl);
        if (irq !== 1'b0) begin errors++; $display("  irq still raised"); end

        // counter clears
        wr(SE_CNT_CLEAR, 1);
        wr(CT_CNT_CLEAR, 1);
        wr(CMP_CNT_CLEAR, 1);
        poll("SE counters cleared", SE_LINES_EMITTED, 32'hFFFF_FFFF, 0, 5);
        poll("CT counters cleared", CT_LINES_OUT, 32'hFFFF_FFFF, 0, 5);
        poll("CMP counters cleared", CMP_SCENES_DONE, 32'hFFFF_FFFF, 0, 5);
        expect_reg("CMP.BYTES_OUT after clear", CMP_BYTES_OUT, 0);

        // soft reset: data path state cleared, registers kept
        wr(SYS_CTRL, 32'h2);
        repeat (100) @(posedge clk_ctrl);
        wr(SYS_CTRL, 32'h0);
        poll("RC.TBL_WORDS reset", RC_TBL_WORDS, 32'hFFFF_FFFF, 0, 5);
        expect_reg("SE.LINE_PERIOD kept", SE_LINE_PERIOD, period);
        expect_reg("SE.LINE_TIMESTAMP reset", SE_LINE_TIMESTAMP, 0);

        repeat (100) @(posedge clk_sys);
        errors += bus_err + byte_err + last_err;
        if (pos != PASSES * expected.size()) begin
            errors++;
            $display("  %0d bytes captured, expected %0d", pos, PASSES * expected.size());
        end
        if (errors == 0)
            $display("PASS: tb_hpdp_core %s, %0d passes, %0d bytes, %0d scenes, last scene %0d cycles",
                     dir, PASSES, pos, lasts, v);
        else
            $display("FAIL: tb_hpdp_core %s: %0d errors (bytes %0d, tlast %0d, bus %0d)",
                     dir, errors, byte_err, last_err, bus_err);
        $finish;
    end

    initial begin
        #50ms;
        $display("FAIL: tb_hpdp_core timeout (%0d bytes)", pos);
        $finish;
    end
endmodule
