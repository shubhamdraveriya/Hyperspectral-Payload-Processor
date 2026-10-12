`timescale 1ns / 1ps

// Test of corner_turn under sustained overload: the output is stalled for long
// stretches, so lines are dropped and sent as fill lines, and, with the
// descriptor queue full, lost without fill. Checks every output line against
// the input lines that were sent (in order): BIL -> BIP order of real lines,
// zeros for fill lines, first-of-line / last markers, and the first-of-scene
// marker on the first line sent of every scene (also when the scene's first
// line was lost: the marker moves to the next line sent). Counters too.
module tb_corner_turn;
    import frontend_pkg::*;

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

    logic        s_valid = 1'b0, s_last = 1'b0;
    logic [15:0] s_data = '0;
    logic [1:0]  s_user = '0;
    logic [15:0] m_tdata;
    logic [1:0]  m_tuser;
    logic        m_tlast, m_tvalid, m_tready = 1'b0;
    logic [31:0] lines_out, dropped_lines, unfilled_lines;

    corner_turn #(.NX_MAX(8), .NZ_MAX(8)) dut (
        .clk, .rst, .cfg,
        .s_valid, .s_data, .s_user, .s_last,
        .m_axis_tdata (m_tdata), .m_axis_tuser (m_tuser), .m_axis_tlast (m_tlast),
        .m_axis_tvalid (m_tvalid), .m_axis_tready (m_tready),
        .cnt_clr (1'b0), .lines_out, .dropped_lines, .unfilled_lines,
        .inj ('0), .ecc ()
    );

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
            if (pos_in == PERIOD - 1) begin
                pos_in <= 0;
                n_in   <= n_in + 1;
            end else begin
                pos_in <= pos_in + 1;
            end
        end
    end

    // which input lines were sent (the corner turn's decision at their last sample)
    typedef struct { int n; logic fill; logic mark; } line_t;
    line_t sent [$];
    int    last_marked = -1, n_lines = 0, n_fill = 0, n_lost = 0, n_lost_first = 0;
    always @(posedge clk) begin
        if (!rst && s_valid && s_last) begin
            int n;
            n = n_lines;
            if (dut.dq_push) begin
                line_t l;
                l.n    = n;
                l.fill = dut.dq_din.fill;
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

    int got = 0, k = 0, errors = 0;
    always @(posedge clk) begin
        if (!rst && m_tvalid && m_tready) begin
            line_t l;
            logic [15:0] e;
            int x, z;
            if (got >= sent.size()) begin
                errors++;
                if (errors < 10) $display("  output line %0d not expected", got);
            end else begin
                l = sent[got];
                x = k / NZ;
                z = k % NZ;
                e = l.fill ? 16'd0 : value(l.n, z * NX + x);
                if (m_tdata !== e || m_tuser[1] !== (k == 0) || m_tlast !== (k == LINE - 1) ||
                    m_tuser[0] !== (k == 0 && l.mark)) begin
                    errors++;
                    if (errors < 10)
                        $display("  output line %0d (input %0d, fill %b, mark %b) sample %0d: data %04h user %b last %b, expected %04h",
                                 got, l.n, l.fill, l.mark, k, m_tdata, m_tuser, m_tlast, e);
                end
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
        while (!(n_lines == LINES && got == sent.size())) @(posedge clk);
        repeat (20) @(posedge clk);
        if (lines_out != sent.size() || dropped_lines != n_fill + n_lost || unfilled_lines != n_lost) begin
            errors++;
            $display("  counters: lines_out %0d dropped %0d unfilled %0d, expected %0d %0d %0d",
                     lines_out, dropped_lines, unfilled_lines, sent.size(), n_fill + n_lost, n_lost);
        end
        if (n_fill == 0 || n_lost_first == 0) begin
            errors++;
            $display("  scenario not covered: %0d fill lines, %0d lost first lines", n_fill, n_lost_first);
        end
        if (errors == 0)
            $display("PASS: tb_corner_turn, %0d lines in, %0d sent (%0d fill), %0d lost (%0d of them first of a scene)",
                     LINES, sent.size(), n_fill, n_lost, n_lost_first);
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
