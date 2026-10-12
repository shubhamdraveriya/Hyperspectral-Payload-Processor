`timescale 1ns / 1ps

// Self-checking test of c123_core against vectors from the Python model
// (tb/ccsds123/gen_vectors.py). Plusarg +DIR=<test directory>.
//
// Drives the samples with random gaps (in_pct % of cycles), applies random
// back-pressure on the output (out_pct % ready), checks every output byte and
// tlast per scene, and checks every mapped residual against the model's trace
// so that a mismatch points at the first wrong sample.
module tb_c123_core;
    import c123_pkg::*;

    localparam int NX_MAX = 64;
    localparam int NZ_MAX = 64;

    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;             // 250 MHz

    cfg_t        cfg;
    logic [15:0] s_tdata = '0;
    logic        s_tuser = 1'b0, s_tvalid = 1'b0, s_tready;
    logic [63:0] m_tdata;
    logic [7:0]  m_tkeep;
    logic        m_tlast, m_tvalid, m_tready = 1'b0;
    logic        cfg_error;

    c123_core #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) dut (
        .clk, .rst, .cfg,
        .s_axis_tdata  (s_tdata),
        .s_axis_tuser  (s_tuser),
        .s_axis_tvalid (s_tvalid),
        .s_axis_tready (s_tready),
        .m_axis_tdata  (m_tdata),
        .m_axis_tkeep  (m_tkeep),
        .m_axis_tlast  (m_tlast),
        .m_axis_tvalid (m_tvalid),
        .m_axis_tready (m_tready),
        .cfg_error     (cfg_error),
        .lb_inj        ('0),
        .lb_ecc        ()
    );

    string       dir;
    int          scenes, in_pct, out_pct, scene_size;
    logic [15:0] samples [$];
    logic [15:0] deltas [$];
    logic [7:0]  expected [$];

    task automatic read_hex(string path, int width, ref logic [15:0] q16 [$], ref logic [7:0] q8 [$]);
        int fd, v;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        while ($fscanf(fd, "%h", v) == 1) begin
            if (width == 16) q16.push_back(16'(v));
            else             q8.push_back(8'(v));
        end
        $fclose(fd);
    endtask

    task automatic read_cfg(string path);
        int fd, n;
        int nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk, wb, raw, ud;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                    nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk,
                    wb, raw, ud, scenes, in_pct, out_pct);
        if (n != 22) $fatal(1, "bad cfg.txt (%0d fields)", n);
        $fclose(fd);
        cfg.nx = nx;  cfg.ny = ny;  cfg.nz = nz;  cfg.depth = depth;  cfg.bands = bands;
        cfg.full = full;  cfg.local_sum = ls;  cfg.omega = omega;  cfg.reg_size = r;
        cfg.v_min = vmin;  cfg.v_max = vmax;  cfg.t_inc_log2 = tinc;  cfg.u_max = umax;
        cfg.gamma0 = g0;  cfg.gamma_star = gs;  cfg.k_init = kk;  cfg.word_bytes = wb;
        cfg.raw = raw;  cfg.user_data = ud;
        scene_size = nx * ny * nz;
    endtask

    // -----------------------------------------------------------------
    // Input driver
    // -----------------------------------------------------------------
    int idx = 0;
    always @(posedge clk) begin
        if (rst) begin
            s_tvalid <= 1'b0;
        end else if (!s_tvalid || s_tready) begin
            if (idx < samples.size() && $urandom_range(0, 99) < in_pct) begin
                s_tvalid <= 1'b1;
                s_tdata  <= samples[idx];
                s_tuser  <= (idx % scene_size == 0);
                idx      <= idx + 1;
            end else begin
                s_tvalid <= 1'b0;
            end
        end
    end

    // -----------------------------------------------------------------
    // Output checker
    // -----------------------------------------------------------------
    int pos = 0, byte_errors = 0, lasts = 0, last_errors = 0, scene_end;
    int scene_ends [$];

    logic [7:0] got_b, exp_b;
    always @(posedge clk) begin
        if (!rst && m_tvalid && m_tready) begin
            for (int b = 0; b < 8; b++) begin
                if (m_tkeep[b]) begin
                    // plain variables: xsim mis-evaluates !== between a variable part-select
                    // and a queue element
                    got_b = m_tdata[8 * b +: 8];
                    exp_b = (pos < expected.size()) ? expected[pos] : 8'hxx;
                    if (pos >= expected.size() || got_b !== exp_b) begin
                        byte_errors++;
                        if (byte_errors <= 8)
                            $display("  byte %0d: got %02h, expected %02h", pos, got_b, exp_b);
                    end
                    pos++;
                end
            end
            if (m_tlast) begin
                if (lasts >= scene_ends.size() || pos != scene_ends[lasts]) begin
                    last_errors++;
                    $display("  tlast after byte %0d", pos);
                end
                lasts++;
            end
        end
        m_tready <= ($urandom_range(0, 99) < out_pct);
    end

    // Mapped residuals, checked where the coder takes them
    int di = 0, delta_errors = 0;
    logic [15:0] exp_d;
    always @(posedge clk) begin
        if (!rst && dut.ce && dut.pr_valid) begin
            exp_d = (di < deltas.size()) ? deltas[di] : 16'hxxxx;
            if (di >= deltas.size() || dut.pr.delta !== exp_d) begin
                delta_errors++;
                if (delta_errors <= 5) begin
                    int sc, rem;
                    sc  = di / scene_size;
                    rem = di % scene_size;
                    $display("  delta %0d (scene %0d, y %0d, x %0d, z %0d): got %0d, expected %0d", di, sc,
                             rem / (cfg.nx * cfg.nz), (rem / cfg.nz) % cfg.nx, rem % cfg.nz,
                             dut.pr.delta, di < deltas.size() ? deltas[di] : -1);
                end
            end
            di++;
        end
    end

    // -----------------------------------------------------------------
    // Test sequence
    // -----------------------------------------------------------------
    initial begin
        logic [15:0] dummy16 [$];
        logic [7:0]  dummy8 [$];
        int timeout;
        if (!$value$plusargs("DIR=%s", dir)) $fatal(1, "+DIR=<test directory> missing");
        read_cfg({dir, "/cfg.txt"});
        read_hex({dir, "/input.hex"}, 16, samples, dummy8);
        read_hex({dir, "/delta.hex"}, 16, deltas, dummy8);
        read_hex({dir, "/expected.hex"}, 8, dummy16, expected);
        // byte offset where each compressed image ends (tlast check)
        begin
            int fd, v;
            fd = $fopen({dir, "/sizes.txt"}, "r");
            if (fd != 0) begin
                scene_end = 0;
                while ($fscanf(fd, "%d", v) == 1) begin
                    scene_end += v;
                    scene_ends.push_back(scene_end);
                end
                $fclose(fd);
            end
        end
        $display("tb_c123_core: %s, %0d samples, %0d bytes, in %0d%%, out %0d%%",
                 dir, samples.size(), expected.size(), in_pct, out_pct);

        repeat (10) @(posedge clk);
        if (cfg_error) $display("  cfg_error is set");
        rst <= 1'b0;

        timeout = 64 * samples.size() + 20000;
        fork
            wait (pos >= expected.size() && lasts >= scenes && di >= samples.size());
            repeat (timeout) @(posedge clk);
        join_any
        disable fork;
        repeat (200) @(posedge clk);       // anything extra would show up here

        if (byte_errors == 0 && delta_errors == 0 && last_errors == 0 && pos == expected.size() &&
            lasts == scenes && di == samples.size() && !cfg_error)
            $display("PASS: tb_c123_core %s, %0d bytes, %0d samples", dir, pos, di);
        else
            $display("FAIL: tb_c123_core %s: %0d byte errors, %0d delta errors, %0d tlast errors, %0d/%0d bytes, %0d/%0d tlast, %0d/%0d samples",
                     dir, byte_errors, delta_errors, last_errors, pos, expected.size(), lasts, scenes,
                     di, samples.size());
        $finish;
    end
endmodule
