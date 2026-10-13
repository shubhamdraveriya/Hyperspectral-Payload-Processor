`timescale 1ns / 1ps

// Fault-injection campaign, simulation part (SPEC RAD-07 a). The payload chain
// as in hpdp_core (sensor_frontend -> scene_guard -> c123_core) processes one
// scene per trial, from the vectors of +DIR (DMA source). Trial 0 has no fault
// and gives the reference output. Every later trial resets the chain, reloads
// the calibration table, injects one fault of a class (the classes in turn) at
// a random moment of the scene, and compares the compressed output with the
// reference. Outcome of a trial:
//   masked     output identical, nothing reported
//   corrected  output identical, a correction reported (ECC single-bit, a TMR
//              copy outvoted)
//   detected   an uncorrectable error reported (ECC double-bit, error marks
//              on the samples, a scene padded) or the scene did not complete
//              (what the PL watchdog reports, RAD-05), whatever the output
//   silent     output different, nothing but corrections reported
// Classes: ECC single / double-bit errors in the calibration table, the
// corner-turn buffers, the compressor line buffer and the weights (through
// the memories' injection inputs: the simulation model of the ECC memories
// does not decode real bit flips); an upset in one copy of a TMR memory
// (coder state, neighbourhood memories) or of a TMR state machine register;
// an upset in an unprotected pipeline or control register (what is left).
// Plusargs: +DIR, +TRIALS=<per class> (default 4), +SEED. PASS when no
// protected class has a silent outcome; results also in fault_results.csv.
module tb_fault;
    import frontend_pkg::*;
    import c123_pkg::*;
    import ecc_pkg::*;

    localparam int NX_MAX = 64, NZ_MAX = 64;
    localparam int NCLASS = 11;
    localparam string CLASS_NAME [NCLASS] = '{
        "ECC SBE calibration table", "ECC DBE calibration table",
        "ECC SBE corner-turn buffer", "ECC DBE corner-turn buffer",
        "ECC SBE compressor line buffer", "ECC DBE compressor line buffer",
        "ECC SBE compressor weights", "ECC DBE compressor weights",
        "TMR memory copy (coder, neighbourhood)", "TMR state machine copy",
        "unprotected register"};
    localparam bit PROTECTED [NCLASS] = '{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0};

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
    logic         dma_tvalid = 1'b0, dma_tready, tbl_tvalid = 1'b0, tbl_tlast = 1'b0, tbl_tready;
    logic [15:0]  fe_tdata, sg_tdata;
    logic [1:0]   fe_tuser, sg_tuser;
    logic [23:0]  fe_ts, sg_ts;
    logic         fe_ue, fe_fill, fe_tlast, fe_tvalid, fe_tready;
    logic         sg_ue, sg_fill, sg_tlast, sg_tvalid, sg_tready;
    logic [63:0]  m_tdata;
    logic [7:0]   m_tkeep;
    logic         m_tlast, m_tvalid, cfg_error;
    se_status_t   se_st;
    fe_status_t   fe_st;
    logic [32:0]  tbl_rdata;
    logic         tbl_done;
    ecc_inj_t     tbl_inj = '0, ct_inj = '0, lb_inj = '0;
    ecc_evt_t     tbl_ecc, ct_ecc, lb_ecc;
    logic         ct_mm, sg_mm, w_sbe, w_dbe, cmp_mm, pk_mm;
    logic [31:0]  padded_scenes, padded_samples;

    sensor_frontend #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_fe (
        .clk_sys, .rst_sys, .clk_sensor, .rst_sensor,
        .cfg_sys (fe), .cfg_sensor (fe), .cnt_clr_sys (1'b0), .cnt_clr_sensor (1'b0),
        .tbl_cmd ('0), .tbl_rdata, .tbl_done,
        .tbl_inj, .tbl_ecc, .ct_inj, .ct_ecc, .ct_tmr_mismatch (ct_mm),
        .s_dma_tdata (dma_tdata), .s_dma_tvalid (dma_tvalid), .s_dma_tready (dma_tready),
        .s_tbl_tdata (tbl_tdata), .s_tbl_tlast (tbl_tlast), .s_tbl_tvalid (tbl_tvalid), .s_tbl_tready (tbl_tready),
        .m_axis_tdata (fe_tdata), .m_axis_tuser (fe_tuser), .m_axis_scene_ts (fe_ts), .m_axis_ue (fe_ue),
        .m_axis_fill (fe_fill), .m_axis_tlast (fe_tlast), .m_axis_tvalid (fe_tvalid), .m_axis_tready (fe_tready),
        .se_status (se_st), .fe_status (fe_st)
    );

    scene_guard u_sg (
        .clk (clk_sys), .rst (rst_sys), .nx (fe.nx), .ny (fe.ny), .nz (fe.nz), .cnt_clr (1'b0),
        .s_tdata (fe_tdata), .s_tuser (fe_tuser), .s_scene_ts (fe_ts), .s_ue (fe_ue), .s_fill (fe_fill),
        .s_tlast (fe_tlast), .s_tvalid (fe_tvalid), .s_tready (fe_tready),
        .m_tdata (sg_tdata), .m_tuser (sg_tuser), .m_scene_ts (sg_ts), .m_ue (sg_ue), .m_fill (sg_fill),
        .m_tlast (sg_tlast), .m_tvalid (sg_tvalid), .m_tready (sg_tready),
        .padded_scenes, .padded_samples, .tmr_mismatch (sg_mm)
    );

    c123_core #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_cmp (
        .clk (clk_sys), .rst (rst_sys), .cfg (cc),
        .s_axis_tdata (sg_tdata), .s_axis_tuser (sg_tuser[0]), .s_axis_tvalid (sg_tvalid), .s_axis_tready (sg_tready),
        .m_axis_tdata (m_tdata), .m_axis_tkeep (m_tkeep), .m_axis_tlast (m_tlast), .m_axis_tvalid (m_tvalid),
        .m_axis_tready (1'b1), .cfg_error,
        .lb_inj, .lb_ecc, .w_sbe, .w_dbe, .tmr_mismatch (cmp_mm), .fsm_mismatch (pk_mm)
    );

    // -----------------------------------------------------------------
    // Vectors (one scene)
    // -----------------------------------------------------------------
    string        dir;
    logic [127:0] tbl [$], dma [$];
    int           line_len, scene_len, period;

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
        int source, bypass, scenes, dma_pct, out_pct, drop_t, overrun_t, limit_t;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                    nx, ny, nz, depth, bands, full, ls, omega, r, vmin, vmax, tinc, umax, g0, gs, kk,
                    wb, rw, ud, source, period, bypass, scenes, dma_pct, out_pct, drop_t, overrun_t, limit_t);
        if (n != 28) $fatal(1, "bad cfg.txt (%0d fields)", n);
        $fclose(fd);
        cc = '0;
        cc.nx = nx;  cc.ny = ny;  cc.nz = nz;  cc.depth = depth;  cc.bands = bands;
        cc.full = full;  cc.local_sum = ls;  cc.omega = omega;  cc.reg_size = r;
        cc.v_min = vmin;  cc.v_max = vmax;  cc.t_inc_log2 = tinc;  cc.u_max = umax;
        cc.gamma0 = g0;  cc.gamma_star = gs;  cc.k_init = kk;  cc.word_bytes = wb;
        cc.raw = rw;  cc.user_data = ud;
        fe = '0;
        fe.nx = nx;  fe.ny = ny;  fe.nz = nz;  fe.depth = depth;
        fe.line_period = period;  fe.source = 2'(source);  fe.rc_bypass = bypass;
        fe.lines = ny;                              // one scene per trial
        line_len  = nx * nz;
        scene_len = line_len * ny;
        if (source != 0) $fatal(1, "tb_fault needs the DMA source");
    endtask

    // -----------------------------------------------------------------
    // DMA source, output capture, event monitors
    // -----------------------------------------------------------------
    int   dma_i = 0, dma_n;
    logic dma_go = 1'b0;
    always @(posedge clk_sys) begin
        if (rst_sys || !dma_go) begin
            dma_tvalid <= 1'b0;
        end else if (!dma_tvalid || dma_tready) begin
            dma_tvalid <= (dma_i < dma_n);
            if (dma_i < dma_n) begin
                dma_tdata <= dma[dma_i];
                dma_i     <= dma_i + 1;
            end
        end
    end

    logic [7:0] out_q [$];
    logic       got_last = 1'b0;
    always @(posedge clk_sys) begin
        if (!rst_sys && m_tvalid && !got_last) begin
            for (int b = 0; b < 8; b++)
                if (m_tkeep[b]) out_q.push_back(m_tdata[8 * b +: 8]);
            if (m_tlast) got_last <= 1'b1;
        end
    end

    logic       watching = 1'b0;
    int         n_corr, n_det;
    logic [31:0] pad_start;
    always @(posedge clk_sys) begin
        if (watching && !rst_sys) begin
            if (tbl_ecc.sbe || ct_ecc.sbe || lb_ecc.sbe || w_sbe || cmp_mm || ct_mm || sg_mm || pk_mm)
                n_corr++;
            if (tbl_ecc.dbe || ct_ecc.dbe || lb_ecc.dbe || w_dbe || (sg_tvalid && sg_tready && sg_ue))
                n_det++;
        end
    end

    // -----------------------------------------------------------------
    // Fault injection
    // -----------------------------------------------------------------
    int fault_what;                             // which register / memory (for the log)

    // an upset in one copy of a TMR memory: coder state or a neighbourhood memory
    task automatic flip_tmr_ram();
        int m, c, a, b;
        m = $urandom_range(0, 4);
        c = $urandom_range(0, 2);
        a = $urandom_range(0, fe.nz - 1);
        b = $urandom_range(0, 15);
        fault_what = m;
        case (m)
            0: case (c)
                   0: u_cmp.u_coder.u_state.g_copy[0].u_copy.mem[a][$urandom_range(0, 42)] ^= 1'b1;
                   1: u_cmp.u_coder.u_state.g_copy[1].u_copy.mem[a][$urandom_range(0, 42)] ^= 1'b1;
                   default: u_cmp.u_coder.u_state.g_copy[2].u_copy.mem[a][$urandom_range(0, 42)] ^= 1'b1;
               endcase
            1: case (c)
                   0: u_cmp.u_nb.u_mem_w.g_copy[0].u_copy.mem[a][b] ^= 1'b1;
                   1: u_cmp.u_nb.u_mem_w.g_copy[1].u_copy.mem[a][b] ^= 1'b1;
                   default: u_cmp.u_nb.u_mem_w.g_copy[2].u_copy.mem[a][b] ^= 1'b1;
               endcase
            2: case (c)
                   0: u_cmp.u_nb.u_mem_n.g_copy[0].u_copy.mem[a][b] ^= 1'b1;
                   1: u_cmp.u_nb.u_mem_n.g_copy[1].u_copy.mem[a][b] ^= 1'b1;
                   default: u_cmp.u_nb.u_mem_n.g_copy[2].u_copy.mem[a][b] ^= 1'b1;
               endcase
            3: case (c)
                   0: u_cmp.u_nb.u_mem_nw.g_copy[0].u_copy.mem[a][b] ^= 1'b1;
                   1: u_cmp.u_nb.u_mem_nw.g_copy[1].u_copy.mem[a][b] ^= 1'b1;
                   default: u_cmp.u_nb.u_mem_nw.g_copy[2].u_copy.mem[a][b] ^= 1'b1;
               endcase
            default: case (c)
                   0: u_cmp.u_nb.u_mem_rs.g_copy[0].u_copy.mem[a][b] ^= 1'b1;
                   1: u_cmp.u_nb.u_mem_rs.g_copy[1].u_copy.mem[a][b] ^= 1'b1;
                   default: u_cmp.u_nb.u_mem_rs.g_copy[2].u_copy.mem[a][b] ^= 1'b1;
               endcase
        endcase
    endtask

    // an upset in one copy of a TMR state machine register
    task automatic flip_tmr_fsm();
        int m;
        m = $urandom_range(0, 5);
        fault_what = m;
        case (m)
            0: u_fe.u_ct.wb_1 ^= 1'b1;
            1: u_fe.u_ct.full_0[$urandom_range(0, 1)] ^= 1'b1;
            2: u_fe.u_ct.rd_on_2 ^= 1'b1;
            3: u_sg.pad_1 ^= 1'b1;
            4: u_cmp.u_pack.state_0[$urandom_range(0, 2)] ^= 1'b1;
            default: u_cmp.u_pack.state_2[$urandom_range(0, 2)] ^= 1'b1;
        endcase
    endtask

    // an upset in an unprotected register
    task automatic flip_unprotected();
        int m;
        m = $urandom_range(0, 13);
        fault_what = m;
        case (m)
            0:  u_cmp.u_coder.acc1[$urandom_range(0, 31)] ^= 1'b1;
            1:  u_cmp.u_coder.cnt1[$urandom_range(0, 10)] ^= 1'b1;
            2:  u_cmp.u_coder.d1[$urandom_range(0, 15)] ^= 1'b1;
            3:  u_cmp.u_nb.s1[$urandom_range(0, 15)] ^= 1'b1;
            4:  u_cmp.u_nb.z1[$urandom_range(0, 15)] ^= 1'b1;
            5:  u_cmp.u_nb.a1[$urandom_range(0, 11)] ^= 1'b1;
            6:  u_fe.u_rc.diff3[$urandom_range(0, 16)] ^= 1'b1;
            7:  u_fe.u_rc.sum5[$urandom_range(0, 33)] ^= 1'b1;
            8:  u_fe.u_rc.c6[$urandom_range(0, 15)] ^= 1'b1;
            9:  u_cmp.u_pack.acc[$urandom_range(0, 127)] ^= 1'b1;
            10: u_fe.u_ct.ra[$urandom_range(0, 11)] ^= 1'b1;
            11: u_fe.u_ct.zr[$urandom_range(0, 15)] ^= 1'b1;
            12: u_fe.u_ct.wcnt[$urandom_range(0, 11)] ^= 1'b1;
            default: u_sg.n[$urandom_range(0, 39)] ^= 1'b1;
        endcase
    endtask

    // one trial: reset, table, the fault armed or at a random moment, one scene
    typedef enum int {MASKED, CORRECTED, DETECTED, SILENT} outcome_t;
    logic [7:0] ref_q [$];
    int timeout_cycles;

    task automatic trial(int cls, output outcome_t outcome, output logic done_ok, output logic same);
        int delay;
        logic dbe;
        // reset the chain
        rst_sys <= 1'b1; rst_sensor <= 1'b1; fe.run <= 1'b0; dma_go <= 1'b0;
        tbl_inj <= '0; ct_inj <= '0; lb_inj <= '0;
        repeat (20) @(posedge clk_sensor);
        dma_i = 0; out_q.delete(); got_last <= 1'b0;
        @(posedge clk_sys) rst_sys <= 1'b0;
        @(posedge clk_sensor) rst_sensor <= 1'b0;
        repeat (40) @(posedge clk_sys);
        n_corr = 0; n_det = 0; pad_start = padded_scenes; fault_what = -1;
        watching = 1'b1;
        dbe = (cls % 2 == 1);
        // the table: an error armed before the load goes into its entry
        if (cls == 0 || cls == 1) begin
            @(posedge clk_sys);
            tbl_inj <= '{arm: 1'b1, dbe: dbe, addr: 18'($urandom_range(0, line_len - 1))};
            @(posedge clk_sys);
            tbl_inj <= '0;
        end
        foreach (tbl[i]) begin
            @(posedge clk_sys);
            tbl_tvalid <= 1'b1; tbl_tdata <= tbl[i]; tbl_tlast <= (i == tbl.size() - 1);
        end
        @(posedge clk_sys);
        tbl_tvalid <= 1'b0; tbl_tlast <= 1'b0;
        // buffer errors armed now go into a word when the scene writes it
        if (cls == 2 || cls == 3) begin
            @(posedge clk_sys);
            ct_inj <= '{arm: 1'b1, dbe: dbe,
                        addr: 18'($urandom_range(0, 1) * NX_MAX * NZ_MAX + $urandom_range(0, line_len - 1))};
            @(posedge clk_sys);
            ct_inj <= '0;
        end
        if (cls == 4 || cls == 5) begin
            @(posedge clk_sys);
            lb_inj <= '{arm: 1'b1, dbe: dbe, addr: 18'($urandom_range(0, line_len - 1))};
            @(posedge clk_sys);
            lb_inj <= '0;
        end
        // the scene
        dma_go <= 1'b1;
        repeat (300) @(posedge clk_sys);
        @(posedge clk_sensor) fe.run <= 1'b1;
        delay = $urandom_range(10, fe.ny * period * 10 / 4);     // within the scene (clk_sys cycles)
        fork
            begin
                repeat (delay) @(posedge clk_sys);
                @(negedge clk_sys);
                case (cls)
                    6, 7: begin
                        // the next weight write stores the error
                        if (dbe) force u_cmp.u_pred.u_weights.inj_dbe = 1'b1;
                        else     force u_cmp.u_pred.u_weights.inj_sbe = 1'b1;
                        begin
                            int k;
                            k = 0;
                            do begin
                                @(posedge clk_sys);
                                k++;
                            end while (!u_cmp.u_pred.u_weights.we && k < 20000);
                        end
                        @(negedge clk_sys);
                        release u_cmp.u_pred.u_weights.inj_sbe;
                        release u_cmp.u_pred.u_weights.inj_dbe;
                    end
                    8:  flip_tmr_ram();
                    9:  flip_tmr_fsm();
                    10: flip_unprotected();
                    default: ;
                endcase
            end
            begin
                int t;
                t = 0;
                while (!got_last && t < timeout_cycles) begin
                    @(posedge clk_sys);
                    t++;
                end
            end
        join
        repeat (50) @(posedge clk_sys);
        watching = 1'b0;
        done_ok = got_last;
        same = done_ok && (out_q.size() == ref_q.size());
        if (same)
            foreach (out_q[i]) if (out_q[i] !== ref_q[i]) same = 1'b0;
        if (!done_ok || n_det > 0 || padded_scenes != pad_start) outcome = DETECTED;
        else if (!same)                                          outcome = SILENT;
        else if (n_corr > 0)                                     outcome = CORRECTED;
        else                                                     outcome = MASKED;
    endtask

    // -----------------------------------------------------------------
    // Campaign
    // -----------------------------------------------------------------
    initial begin
        int trials, seed, fd, errors, burn;
        int count [NCLASS][4];
        outcome_t o;
        logic done_ok, same;
        if (!$value$plusargs("DIR=%s", dir)) $fatal(1, "+DIR=<test directory> missing");
        if (!$value$plusargs("TRIALS=%d", trials)) trials = 4;
        if ($value$plusargs("SEED=%d", seed)) repeat (seed) burn = $urandom;   // (xsim: no seeding call)
        read_cfg({dir, "/cfg.txt"});
        read128({dir, "/tbl.hex"}, tbl);
        read128({dir, "/dma.hex"}, dma);
        dma_n = scene_len / 8;
        timeout_cycles = fe.ny * period * 10 / 4 * 3 + 20000;
        foreach (count[c, k]) count[c][k] = 0;
        $display("tb_fault: %s, %0dx%0dx%0d, %0d trials per class", dir, fe.nx, fe.ny, fe.nz, trials);

        // reference
        trial(-1, o, done_ok, same);
        if (!done_ok) $fatal(1, "tb_fault: the reference scene did not complete");
        ref_q = out_q;
        if (n_corr != 0 || n_det != 0) $fatal(1, "tb_fault: events without a fault");
        $display("  reference: %0d bytes", ref_q.size());

        errors = 0;
        for (int t = 0; t < trials; t++)
            for (int c = 0; c < NCLASS; c++) begin
                trial(c, o, done_ok, same);
                count[c][o]++;
                if (o == SILENT && PROTECTED[c]) begin
                    errors++;
                    $display("  silent corruption: class %s, target %0d", CLASS_NAME[c], fault_what);
                end
            end

        fd = $fopen("fault_results.csv", "w");
        $fdisplay(fd, "class,trials,masked,corrected,detected,silent");
        $display("  %-40s %6s %7s %9s %8s %6s", "class", "trials", "masked", "corrected", "detected", "silent");
        for (int c = 0; c < NCLASS; c++) begin
            $display("  %-40s %6d %7d %9d %8d %6d", CLASS_NAME[c], trials, count[c][MASKED], count[c][CORRECTED],
                     count[c][DETECTED], count[c][SILENT]);
            $fdisplay(fd, "%s,%0d,%0d,%0d,%0d,%0d", CLASS_NAME[c], trials, count[c][MASKED], count[c][CORRECTED],
                      count[c][DETECTED], count[c][SILENT]);
        end
        $fclose(fd);
        if (errors == 0)
            $display("PASS: tb_fault, %0d trials, no silent corruption in a protected class", trials * NCLASS);
        else
            $display("FAIL: tb_fault: %0d silent corruptions in protected classes", errors);
        $finish;
    end
endmodule
