`timescale 1ns / 1ps

// Test of corner_turn under sustained overload: the output is stalled for long
// stretches, so lines are dropped and sent as fill lines, and, with the
// descriptor queue full, lost without fill. Checks every output line against
// the input lines that were sent (in order): BIL -> BIP order of real lines,
// zeros for fill lines, first-of-line / last markers, and the first-of-scene
// marker on the first line sent of every scene (also when the scene's first
// line was lost: the marker moves to the next line sent), the scene timestamp of
// every sample, the error mark (any input sample of the line marked, or a
// double-bit error injected into a buffer word: the samples of that word), the
// fill mark. Counters too. Upsets are forced into one copy of the triplicated
// control state (wb, full, rd_on, the write and read addresses and counters,
// the line descriptor: RAD-04) during the run: the outputs must stay exact, the
// mismatch must show, and be gone at the next clock.
module tb_corner_turn;
    import frontend_pkg::*;
    import ecc_pkg::*;

    localparam int NX = 4, NZ = 4, NY = 3, LINE = NX * NZ, PERIOD = LINE + 6, LINES = 900;

    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;

    fe_cfg_t cfg;
    always_comb begin
        cfg    = '0;
        cfg.nx = 16'(NX);
        cfg.ny = 16'(NY);
        cfg.nz = 16'(NZ);
    end

    logic        s_valid = 1'b0, s_last = 1'b0, s_ue = 1'b0;
    logic [15:0] s_data = '0;
    logic [1:0]  s_user = '0;
    logic [15:0] m_tdata;
    logic [1:0]  m_tuser;
    logic [23:0] m_ts;
    logic        m_ue, m_fill;
    logic        m_tlast, m_tvalid, m_tready = 1'b0;
    logic [31:0] lines_out, dropped_lines, unfilled_lines;
    ecc_inj_t    inj = '0;
    ecc_evt_t    ecc;
    logic        tmr_mismatch;

    corner_turn #(.NX_MAX(8), .NZ_MAX(8)) dut (
        .clk, .rst, .cfg,
        .s_valid, .s_data, .s_user, .s_last, .s_ue,
        .m_axis_tdata (m_tdata), .m_axis_tuser (m_tuser), .m_axis_scene_ts (m_ts),
        .m_axis_ue (m_ue), .m_axis_fill (m_fill), .m_axis_tlast (m_tlast),
        .m_axis_tvalid (m_tvalid), .m_axis_tready (m_tready),
        .cnt_clr (1'b0), .lines_out, .dropped_lines, .unfilled_lines,
        .inj, .ecc, .tmr_mismatch
    );

    // upsets in one copy of the control state, at random times
    int n_upsets = 0, mm_cycles = 0, mm_stuck = 0, ub;
    logic mm_d = 1'b0;
    initial begin
        @(negedge rst);
        repeat (200) @(posedge clk);
        repeat (30) begin                       // all early in the run
            repeat ($urandom_range(300, 700)) @(posedge clk);
            @(negedge clk);
            case ($urandom_range(0, 11))
                0: dut.wb_0    = !dut.wb_0;
                1: dut.wb_2    = !dut.wb_2;
                2: dut.full_1  = dut.full_1 ^ 2'(1 << $urandom_range(0, 1));
                3: dut.full_2  = dut.full_2 ^ 2'b11;
                4: dut.rd_on_0 = !dut.rd_on_0;
                5: dut.rd_on_1 = !dut.rd_on_1;
                6: begin ub = $urandom_range(0, 5); dut.wcnt_2[ub] ^= 1'b1; end       // EB = 6 here
                7: dut.wr_on_0 ^= 1'b1;
                8: begin ub = $urandom_range(0, 23); dut.lines_in_1[ub] ^= 1'b1; end
                9: begin ub = $urandom_range(0, 5); dut.ra_0[ub] ^= 1'b1; end
                10: begin ub = $urandom_range(0, 15); dut.zr_2[ub] ^= 1'b1; end
                default: begin ub = $urandom_range(0, 27); dut.rd_1[ub] ^= 1'b1; end
            endcase
            n_upsets++;
        end
    end
    always @(posedge clk) begin
        if (tmr_mismatch) mm_cycles++;
        if (tmr_mismatch && mm_d) mm_stuck++;          // still there a clock later
        mm_d <= tmr_mismatch;
    end

    // sample i of input line n (never 0, so fill lines are told apart)
    function automatic logic [15:0] value(int n, int i);
        return 16'h8000 | 16'((n % 2048) * LINE + i);
    endfunction

    // input: one line every PERIOD cycles
    int n_in = 0, pos_in = 0;
    always @(posedge clk) begin
        if (rst || n_in >= LINES) begin
            s_valid <= 1'b0;
        end else begin
            int c;
            c = pos_in;
            s_valid <= (c < LINE);
            s_data  <= value(n_in, c);
            s_user  <= {c == 0, c == 0 && n_in % NY == 0};
            s_last  <= (c == LINE - 1);
            s_ue    <= (c < LINE) && n_in > 0 && ($urandom_range(0, 999) < 4);   // (line 0: buffer error only)
            if (pos_in == PERIOD - 1) begin
                pos_in <= 0;
                n_in   <= n_in + 1;
            end else begin
                pos_in <= pos_in + 1;
            end
        end
    end

    // which input lines were sent (the corner turn's decision at their last sample)
    typedef struct { int n; logic fill; logic mark; logic ue; logic dbe; } line_t;
    line_t sent [$];
    int    last_marked = -1, n_lines = 0, n_fill = 0, n_lost = 0, n_lost_first = 0;
    int    n_ue_lines = 0;
    logic  in_ue = 1'b0, dbe_pending = 1'b1;      // the first line written takes the injected error
    always @(posedge clk) begin
        if (!rst && s_valid)
            in_ue = s_user[1] ? s_ue : (in_ue || s_ue);
        if (!rst && s_valid && s_last) begin
            int n;
            n = n_lines;
            if (dut.dq_push) begin
                line_t l;
                l.n    = n;
                l.fill = dut.dq_din.fill;
                l.ue   = !l.fill && in_ue;
                l.dbe  = !l.fill && dbe_pending;
                if (l.dbe) dbe_pending = 1'b0;
                if (l.ue) n_ue_lines++;
                l.mark = (n / NY != last_marked);       // first line sent of its scene
                if (l.mark) last_marked = n / NY;
                if (l.fill) n_fill++;
                sent.push_back(l);
            end else begin
                n_lost++;
                if (n % NY == 0) n_lost_first++;
            end
            n_lines++;
        end
    end

    // output: long stalls, then draining
    int cyc = 0;
    always @(posedge clk) begin
        cyc++;
        m_tready <= ((cyc / 4000) % 2 == 0) ? ($urandom_range(0, 99) < 3) : ($urandom_range(0, 99) < 90);
    end

    int got = 0, k = 0, errors = 0, n_dbe_samples = 0;
    always @(posedge clk) begin
        if (!rst && m_tvalid && m_tready) begin
            line_t l;
            logic [15:0] e;
            logic ue;
            int x, z;
            if (got >= sent.size()) begin
                errors++;
                if (errors < 10) $display("  output line %0d not expected", got);
            end else begin
                l = sent[got];
                x = k / NZ;
                z = k % NZ;
                e = l.fill ? 16'd0 : value(l.n, z * NX + x);
                // the injected double-bit error sits in buffer 0, word 0: BIL samples 0..3
                ue = l.ue || (l.dbe && z * NX + x < 4);
                // (data of the double-bit error word is corrupt: not compared)
                if ((m_tdata !== e && !(l.dbe && z * NX + x < 4)) || m_tuser[1] !== (k == 0) || m_tlast !== (k == LINE - 1) ||
                    m_tuser[0] !== (k == 0 && l.mark) || m_ts !== 24'(l.n / NY * NY) ||
                    m_ue !== ue || m_fill !== l.fill) begin
                    errors++;
                    if (errors < 10)
                        $display("  output line %0d (input %0d, fill %b, mark %b) sample %0d: data %04h user %b last %b ts %0d ue %b fill %b, expected %04h ue %b",
                                 got, l.n, l.fill, l.mark, k, m_tdata, m_tuser, m_tlast, m_ts, m_ue, m_fill, e, ue);
                end
                if (m_ue && !l.ue) n_dbe_samples++;
            end
            if (k == LINE - 1) begin
                k = 0;
                got++;
            end else begin
                k++;
            end
        end
    end

    initial begin
        repeat (10) @(posedge clk);
        rst <= 1'b0;
        // a double-bit error into buffer 0, word 0 (at the first line written)
        @(posedge clk);
        inj <= '{arm: 1'b1, dbe: 1'b1, addr: 18'd0};
        @(posedge clk);
        inj <= '0;
        while (!(n_lines == LINES && got == sent.size())) @(posedge clk);
        repeat (20) @(posedge clk);
        if (lines_out != sent.size() || dropped_lines != n_fill + n_lost || unfilled_lines != n_lost) begin
            errors++;
            $display("  counters: lines_out %0d dropped %0d unfilled %0d, expected %0d %0d %0d",
                     lines_out, dropped_lines, unfilled_lines, sent.size(), n_fill + n_lost, n_lost);
        end
        if (n_upsets == 0 || mm_cycles != n_upsets || mm_stuck != 0) begin
            errors++;
            $display("  TMR: %0d upsets, %0d mismatch cycles, %0d not healed at the next clock",
                     n_upsets, mm_cycles, mm_stuck);
        end
        if (n_fill == 0 || n_lost_first == 0 || n_ue_lines == 0 || n_dbe_samples != 4) begin
            errors++;
            $display("  scenario not covered: %0d fill lines, %0d lost first lines, %0d marked lines, %0d buffer error samples",
                     n_fill, n_lost_first, n_ue_lines, n_dbe_samples);
        end
        if (errors == 0)
            $display("PASS: tb_corner_turn, %0d lines in, %0d sent (%0d fill, %0d marked), %0d lost (%0d of them first of a scene), buffer error on %0d samples, %0d control state upsets masked",
                     LINES, sent.size(), n_fill, n_ue_lines, n_lost, n_lost_first, n_dbe_samples, n_upsets);
        else
            $display("FAIL: tb_corner_turn: %0d errors", errors);
        $finish;
    end

    initial begin
        #20ms;
        $display("FAIL: tb_corner_turn timeout (%0d of %0d lines in, %0d of %0d out)", n_lines, LINES, got, sent.size());
        $finish;
    end
endmodule
