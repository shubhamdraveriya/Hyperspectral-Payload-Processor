`timescale 1ns / 1ps

// Self-checking test of the sensor front end and compressor:
//   sensor_frontend (sensor_emulator -> radiometric_corr -> corner_turn) -> c123_core
// against vectors from the Python model (tb/frontend/gen_vectors.py).
// Plusarg +DIR=<test directory>.
//
// clk_sys 250 MHz, clk_sensor 100 MHz (unrelated phase). Loads the calibration
// table, checks the table debug read/write port, prefills the DMA stream
// (random gaps, dma_pct % of cycles), runs the sensor for all lines (with the
// emulator's line limit, or by clearing run), and checks:
//   - every sample and tag at the sensor emulator, correction and corner turn
//     outputs; the corner turn output may hold zero fill lines only in the drop
//     test, and as many as it counted
//   - every compressed byte and tlast per scene (drop test: one tlast per scene)
//   - status counters against the model
// The compressor output is back-pressured randomly (out_pct % ready).
module tb_frontend;
    import frontend_pkg::*;
    import c123_pkg::*;
    import ecc_pkg::*;

    localparam int NX_MAX = 64;
    localparam int NZ_MAX = 64;

    logic clk_sys = 1'b0, clk_sensor = 1'b0;
    logic rst_sys = 1'b1, rst_sensor = 1'b1;
    always #2 clk_sys = ~clk_sys;
    initial begin
        #1.3;
        forever #5 clk_sensor = ~clk_sensor;
    end

    fe_cfg_t fe;
    cfg_t    cc;

    logic [127:0] dma_tdata = '0, tbl_tdata = '0;
    logic         dma_tvalid = 1'b0, dma_tready;
    logic         tbl_tvalid = 1'b0, tbl_tlast = 1'b0, tbl_tready;

    logic        se_valid, se_last, rc_valid, rc_last;
    logic [15:0] se_data, rc_data, ct_tdata;
    logic [1:0]  se_user, rc_user, ct_tuser;
    logic        ct_tlast, ct_tvalid, ct_tready;
    logic [23:0] ct_scene_ts;
    logic        ct_ue, ct_fill;
    logic [23:0] line_ts, scene_ts;
    logic [31:0] lines_emitted, scenes_started, underruns, overruns, se_dropped;
    logic [31:0] clamped, replaced, ct_lines, ct_dropped, ct_unfilled;

    logic [63:0] m_tdata;
    logic [7:0]  m_tkeep;
    logic        m_tlast, m_tvalid, m_tready = 1'b0;
    logic        cfg_error;

    se_status_t se_st;
    fe_status_t fe_st;

    tbl_cmd_t    tbl_cmd = '0;
    logic [32:0] tbl_rdata;
    logic        tbl_done;
    ecc_evt_t    tbl_ecc, ct_ecc, lb_ecc;    // no ECC errors expected (none injected)
    int          ecc_err = 0;
    always @(posedge clk_sys)
        if (tbl_ecc.sbe || tbl_ecc.dbe || ct_ecc.sbe || ct_ecc.dbe || lb_ecc.sbe || lb_ecc.dbe) begin
            if (ecc_err == 0) $display("  ECC error reported without injection");
            ecc_err++;
        end

    sensor_frontend #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_fe (
        .clk_sys, .rst_sys, .clk_sensor, .rst_sensor,
        .cfg_sys        (fe),
        .cfg_sensor     (fe),
        .cnt_clr_sys    (1'b0),
        .cnt_clr_sensor (1'b0),
        .tbl_cmd, .tbl_rdata, .tbl_done,
        .tbl_inj       ('0),
        .tbl_ecc       (tbl_ecc),
        .ct_inj        ('0),
        .ct_ecc        (ct_ecc),
        .s_dma_tdata   (dma_tdata),
        .s_dma_tvalid  (dma_tvalid),
        .s_dma_tready  (dma_tready),
        .s_tbl_tdata   (tbl_tdata),
        .s_tbl_tlast   (tbl_tlast),
        .s_tbl_tvalid  (tbl_tvalid),
        .s_tbl_tready  (tbl_tready),
        .m_axis_tdata  (ct_tdata),
        .m_axis_tuser  (ct_tuser),
        .m_axis_scene_ts (ct_scene_ts),
        .m_axis_ue     (ct_ue),
        .m_axis_fill   (ct_fill),
        .m_axis_tlast  (ct_tlast),
        .m_axis_tvalid (ct_tvalid),
        .m_axis_tready (ct_tready),
        .se_status     (se_st),
        .fe_status     (fe_st)
    );

    // internal streams, checked too
    assign {se_valid, se_data, se_user, se_last} = {u_fe.se_valid, u_fe.se_data, u_fe.se_user, u_fe.se_last};
    assign {rc_valid, rc_data, rc_user, rc_last} = {u_fe.rc_valid, u_fe.rc_data, u_fe.rc_user, u_fe.rc_last};

    assign line_ts        = se_st.line_timestamp;
    assign scene_ts       = se_st.scene_timestamp;
    assign lines_emitted  = se_st.lines_emitted;
    assign scenes_started = se_st.scenes_started;
    assign underruns      = se_st.underrun_samples;
    assign overruns       = se_st.period_overruns;
    assign se_dropped     = se_st.dropped_samples;
    assign clamped        = fe_st.scene_clamped;
    assign replaced       = fe_st.scene_replaced;
    assign ct_lines       = fe_st.lines_out;
    assign ct_dropped     = fe_st.dropped_lines;
    assign ct_unfilled    = fe_st.unfilled_lines;

    c123_core #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_cmp (
        .clk (clk_sys), .rst (rst_sys), .cfg (cc),
        .s_axis_tdata  (ct_tdata),
        .s_axis_tuser  (ct_tuser[0]),
        .s_axis_tvalid (ct_tvalid),
        .s_axis_tready (ct_tready),
        .m_axis_tdata  (m_tdata),
        .m_axis_tkeep  (m_tkeep),
        .m_axis_tlast  (m_tlast),
        .m_axis_tvalid (m_tvalid),
        .m_axis_tready (m_tready),
        .cfg_error     (cfg_error),
        .lb_inj        ('0),
        .lb_ecc        (lb_ecc)
    );

    // -----------------------------------------------------------------
    // Vectors
    // -----------------------------------------------------------------
    string        dir;
    int           scenes, dma_pct, out_pct, drop_test, overrun_test, limit_test, line_len, scene_len, total;
    int           exp_clamped, exp_replaced;
    logic [15:0]  raw [$], corr [$], bip [$];
    logic [7:0]   expected [$];
    logic [127:0] tbl [$], dma [$];
    int           scene_ends [$];

    // (xsim pushes X when push_back is the bare body of the while loop: keep begin/end)
    task automatic read16(string path, ref logic [15:0] q [$]);
        int fd, v;
        logic [15:0] w;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        while ($fscanf(fd, "%h", v) == 1) begin
            w = v;
            q.push_back(w);
        end
        $fclose(fd);
    endtask

    task automatic read8(string path, ref logic [7:0] q [$]);
        int fd, v;
        logic [7:0] w;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        while ($fscanf(fd, "%h", v) == 1) begin
            w = v;
            q.push_back(w);
        end
        $fclose(fd);
    endtask

    task automatic read128(string path, ref logic [127:0] q [$]);
        int fd;
        logic [127:0] v;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        while ($fscanf(fd, "%h", v) == 1) begin
            q.push_back(v);
        end
        $fclose(fd);
    endtask

    task automatic read_cfg(string path);
        int fd, n;
        int nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk, wb, rw, ud;
        int source, period, bypass;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                    nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk,
                    wb, rw, ud, source, period, bypass, scenes, dma_pct, out_pct, drop_test, overrun_test,
                    limit_test);
        if (n != 28) $fatal(1, "bad cfg.txt (%0d fields)", n);
        $fclose(fd);
        cc.nx = nx;  cc.ny = ny;  cc.nz = nz;  cc.depth = depth;  cc.bands = bands;
        cc.full = full;  cc.local_sum = ls;  cc.omega = omega;  cc.reg_size = r;
        cc.v_min = vmin;  cc.v_max = vmax;  cc.t_inc_log2 = tinc;  cc.u_max = umax;
        cc.gamma0 = g0;  cc.gamma_star = gs;  cc.k_init = kk;  cc.word_bytes = wb;
        cc.raw = rw;  cc.user_data = ud;
        fe.nx = nx;  fe.ny = ny;  fe.nz = nz;  fe.depth = depth;
        fe.line_period = period;  fe.source = source;  fe.rc_bypass = bypass;  fe.run = 1'b0;
        fe.lines = limit_test ? scenes * ny : 0;
        line_len  = nx * nz;
        scene_len = line_len * ny;
    endtask

    // expected {last, user[1], user[0]} of sample i of a stream of whole lines
    function automatic logic [2:0] tags(int i);
        return {i % line_len == line_len - 1, i % line_len == 0, i % scene_len == 0};
    endfunction

    // -----------------------------------------------------------------
    // Stream drivers
    // -----------------------------------------------------------------
    int  dma_i = 0;
    logic dma_go = 1'b0;
    always @(posedge clk_sys) begin
        if (rst_sys) begin
            dma_tvalid <= 1'b0;
        end else if (!dma_tvalid || dma_tready) begin
            if (dma_go && dma_i < dma.size() && $urandom_range(0, 99) < dma_pct) begin
                dma_tvalid <= 1'b1;
                dma_tdata  <= dma[dma_i];
                dma_i      <= dma_i + 1;
            end else begin
                dma_tvalid <= 1'b0;
            end
        end
    end

    // -----------------------------------------------------------------
    // Checkers
    // -----------------------------------------------------------------
    int se_i = 0, se_err = 0, rc_i = 0, rc_err = 0;
    logic [15:0] e16;

    always @(posedge clk_sys) begin
        if (se_valid) begin
            e16 = (se_i < raw.size()) ? raw[se_i] : 16'hxxxx;
            if (se_i >= raw.size() || se_data !== e16 || {se_last, se_user} !== tags(se_i)) begin
                se_err++;
                if (se_err <= 5)
                    $display("  SE sample %0d: got %04h tags %03b, expected %04h tags %03b", se_i, se_data,
                             {se_last, se_user}, e16, tags(se_i));
            end
            se_i++;
        end
    end

    logic [15:0] r16;
    always @(posedge clk_sys) begin
        if (rc_valid) begin
            r16 = (rc_i < corr.size()) ? corr[rc_i] : 16'hxxxx;
            if (rc_i >= corr.size() || rc_data !== r16 || {rc_last, rc_user} !== tags(rc_i)) begin
                rc_err++;
                if (rc_err <= 5)
                    $display("  RC sample %0d (x %0d, z %0d): got %04h tags %03b, expected %04h tags %03b",
                             rc_i, rc_i % fe.nx, (rc_i % line_len) / fe.nx, rc_data,
                             {rc_last, rc_user}, r16, tags(rc_i));
            end
            rc_i++;
        end
    end

    // Corner turn output: each line must equal the model's line, or be a fill line
    int   ct_i = 0, ct_err = 0, fill_lines = 0;
    logic line_match = 1'b1, line_zero = 1'b1;
    logic [15:0] b16;
    always @(posedge clk_sys) begin
        if (ct_tvalid && ct_tready) begin
            logic m, zr;
            b16 = (ct_i < bip.size()) ? bip[ct_i] : 16'hxxxx;
            m   = line_match && ct_i < bip.size() && ct_tdata === b16;
            zr  = line_zero && ct_tdata === 16'd0;
            if ({ct_tlast, ct_tuser} !== tags(ct_i)) begin
                ct_err++;
                if (ct_err <= 5)
                    $display("  CT sample %0d: tags %03b, expected %03b", ct_i, {ct_tlast, ct_tuser}, tags(ct_i));
            end
            // the sample's scene timestamp (lines before its scene); no error mark; fill
            // marks only on the zeros of fill lines
            if (ct_scene_ts !== 24'(ct_i / scene_len * fe.ny) || ct_ue !== 1'b0 ||
                ct_fill === 1'bx || (ct_fill && (!drop_test || ct_tdata !== 16'd0))) begin
                ct_err++;
                if (ct_err <= 5)
                    $display("  CT sample %0d: scene timestamp %0d (expected %0d), ue %b, fill %b",
                             ct_i, ct_scene_ts, ct_i / scene_len * fe.ny, ct_ue, ct_fill);
            end
            if (ct_i % line_len == line_len - 1) begin
                if (!m && zr && drop_test) begin
                    fill_lines++;
                end else if (!m) begin
                    ct_err++;
                    if (ct_err <= 5) $display("  CT line %0d differs from the model", ct_i / line_len);
                end
                line_match <= 1'b1;
                line_zero  <= 1'b1;
            end else begin
                line_match <= m;
                line_zero  <= zr;
            end
            if (!m && line_match && !drop_test && ct_err < 5)
                $display("  CT sample %0d (x %0d, z %0d): got %04h, expected %04h", ct_i,
                         (ct_i % line_len) / fe.nz, ct_i % fe.nz, ct_tdata, b16);
            ct_i++;
        end
    end

    // Compressed output
    int pos = 0, byte_err = 0, lasts = 0, last_err = 0;
    logic [7:0] got_b, exp_b;
    always @(posedge clk_sys) begin
        if (!rst_sys && m_tvalid && m_tready) begin
            for (int b = 0; b < 8; b++) begin
                if (m_tkeep[b] && !drop_test) begin
                    // plain variables: xsim mis-evaluates !== between a variable part-select
                    // and a queue element
                    got_b = m_tdata[8 * b +: 8];
                    exp_b = (pos < expected.size()) ? expected[pos] : 8'hxx;
                    if (pos >= expected.size() || got_b !== exp_b) begin
                        byte_err++;
                        if (byte_err <= 5) $display("  byte %0d: got %02h, expected %02h", pos, got_b, exp_b);
                    end
                end
                if (m_tkeep[b]) pos++;
            end
            if (m_tlast) begin
                if (!drop_test && (lasts >= scene_ends.size() || pos != scene_ends[lasts])) begin
                    last_err++;
                    $display("  tlast after byte %0d", pos);
                end
                lasts++;
            end
        end
        m_tready <= ($urandom_range(0, 99) < out_pct);
    end

    // -----------------------------------------------------------------
    // Test sequence
    // -----------------------------------------------------------------
    // Calibration table entry i as loaded: {bad, gain, dark}
    function automatic logic [32:0] tbl_entry(int i);
        return tbl[i / 2][64 * (i % 2) +: 33];
    endfunction

    task automatic tbl_access(logic write, int addr, logic [32:0] wdata, output logic [32:0] rdata);
        @(posedge clk_sys);
        tbl_cmd <= '{valid: 1'b1, write: write, addr: 18'(addr), wdata: wdata};
        @(posedge clk_sys);
        tbl_cmd <= '0;
        @(posedge clk_sys iff tbl_done);
        rdata = tbl_rdata;
    endtask

    // Read back some entries, overwrite one, then restore it
    int tbl_err = 0;
    task automatic check_table_port();
        int n, probe [4];
        logic [32:0] got, orig, alt;
        n = fe.nx * fe.nz;
        probe = '{0, 1, n / 2, n - 1};
        foreach (probe[k]) begin
            tbl_access(1'b0, probe[k], '0, got);
            if (got !== tbl_entry(probe[k])) begin
                tbl_err++;
                $display("  table entry %0d: read %09h, loaded %09h", probe[k], got, tbl_entry(probe[k]));
            end
        end
        orig = tbl_entry(n - 1);
        alt  = orig ^ 33'h1_5A5A_A5A5;
        tbl_access(1'b1, n - 1, alt, got);
        tbl_access(1'b0, n - 1, '0, got);
        if (got !== alt) begin
            tbl_err++;
            $display("  table write: read back %09h, wrote %09h", got, alt);
        end
        tbl_access(1'b0, n - 2, '0, got);       // the other entry of the word is untouched
        if (got !== tbl_entry(n - 2)) begin
            tbl_err++;
            $display("  table write disturbed entry %0d: %09h", n - 2, got);
        end
        tbl_access(1'b1, n - 1, orig, got);
    endtask

    task automatic check(string what, longint got, longint exp, inout int errors);
        if (got != exp) begin
            errors++;
            $display("  %s = %0d, expected %0d", what, got, exp);
        end
    endtask

    initial begin
        int timeout, errors, fd, v, se_end;
        if (!$value$plusargs("DIR=%s", dir)) $fatal(1, "+DIR=<test directory> missing");
        read_cfg({dir, "/cfg.txt"});
        read16({dir, "/raw.hex"}, raw);
        read16({dir, "/corr.hex"}, corr);
        read16({dir, "/bip.hex"}, bip);
        read8({dir, "/expected.hex"}, expected);
        read128({dir, "/tbl.hex"}, tbl);
        read128({dir, "/dma.hex"}, dma);
        fd = $fopen({dir, "/sizes.txt"}, "r");
        se_end = 0;
        while ($fscanf(fd, "%d", v) == 1) begin
            se_end += v;
            scene_ends.push_back(se_end);
        end
        $fclose(fd);
        fd = $fopen({dir, "/stats.txt"}, "r");
        v = $fscanf(fd, "%d %d", exp_clamped, exp_replaced);
        $fclose(fd);
        total = raw.size();
        $display("tb_frontend: %s, %0dx%0dx%0d, %0d scenes, source %0d, line period %0d, bypass %0d, dma %0d%%, out %0d%%",
                 dir, fe.nx, fe.ny, fe.nz, scenes, fe.source, fe.line_period, fe.rc_bypass, dma_pct, out_pct);

        // resets (the XPM FIFOs need a few cycles of both clocks)
        repeat (20) @(posedge clk_sensor);
        @(posedge clk_sys) rst_sys <= 1'b0;
        @(posedge clk_sensor) rst_sensor <= 1'b0;
        repeat (40) @(posedge clk_sys);

        // calibration table
        foreach (tbl[i]) begin
            @(posedge clk_sys);
            tbl_tvalid <= 1'b1;
            tbl_tdata  <= tbl[i];
            tbl_tlast  <= (i == tbl.size() - 1);
        end
        @(posedge clk_sys);
        tbl_tvalid <= 1'b0;
        tbl_tlast  <= 1'b0;
        check_table_port();

        // prefill the emulator's DMA buffer, then run the sensor for all lines
        dma_go <= 1'b1;
        repeat (400) @(posedge clk_sys);
        @(posedge clk_sensor) fe.run <= 1'b1;
        if (limit_test) begin
            @(posedge clk_sensor iff se_st.done);       // run stays set
        end else begin
            @(posedge clk_sensor iff line_ts == 24'(scenes * fe.ny));
            fe.run <= 1'b0;
        end

        timeout = 30 * total * (fe.line_period + 1) / line_len + 100 * total / (out_pct + 1) + 50000;
        fork
            wait (ct_i >= total && lasts >= scenes && (drop_test || pos >= expected.size()));
            repeat (timeout) @(posedge clk_sys);
        join_any
        disable fork;
        repeat (300) @(posedge clk_sys);       // anything extra would show up here

        errors = se_err + rc_err + ct_err + byte_err + last_err + tbl_err + ecc_err;
        check("SE samples", se_i, total, errors);
        check("RC samples", rc_i, total, errors);
        check("CT samples", ct_i, total, errors);
        check("tlasts", lasts, scenes, errors);
        if (!drop_test) check("compressed bytes", pos, expected.size(), errors);
        check("lines_emitted", lines_emitted, scenes * fe.ny, errors);
        check("scenes_started", scenes_started, scenes, errors);
        check("line_timestamp", line_ts, scenes * fe.ny, errors);
        check("scene_timestamp", scene_ts, (scenes - 1) * fe.ny, errors);
        check("underrun_samples", underruns, 0, errors);
        check("dropped_samples", se_dropped, 0, errors);
        check("period overruns seen", overruns > 0, overrun_test, errors);
        check("scene_clamped", clamped, exp_clamped, errors);
        check("scene_replaced", replaced, exp_replaced, errors);
        check("CT lines_out", ct_lines, scenes * fe.ny, errors);
        check("CT dropped_lines", ct_dropped, fill_lines, errors);
        check("CT unfilled_lines", ct_unfilled, 0, errors);
        check("CT drops seen", ct_dropped > 0, drop_test, errors);
        check("SE done", se_st.done, limit_test, errors);
        check("SE busy", se_st.busy, 0, errors);
        check("tbl_words", fe_st.tbl_words, tbl.size(), errors);
        check("cfg_error", cfg_error, 0, errors);

        if (errors == 0)
            $display("PASS: tb_frontend %s, %0d samples, %0d bytes, %0d clamped, %0d replaced, %0d overruns, %0d dropped lines",
                     dir, ct_i, pos, clamped, replaced, overruns, ct_dropped);
        else
            $display("FAIL: tb_frontend %s: %0d errors (SE %0d, RC %0d, CT %0d, bytes %0d, tlast %0d)",
                     dir, errors, se_err, rc_err, ct_err, byte_err, last_err);
        $finish;
    end
endmodule
