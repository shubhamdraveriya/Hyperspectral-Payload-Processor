`timescale 1ns / 1ps

// Self-checking: random tvalid/tready on both sides, every transfer checked
// in order (data, user, last), plus a full-throughput phase that must move
// one transfer per cycle.
module tb_axis_skid_buffer;
    localparam int DATA_W = 16;
    localparam int USER_W = 2;
    localparam int N_RANDOM = 20000;
    localparam int N_STREAM = 1000;

    logic clk = 0, rst = 1;
    logic [DATA_W-1:0] s_tdata = '0, m_tdata;
    logic [USER_W-1:0] s_tuser = '0, m_tuser;
    logic s_tlast = 0, s_tvalid = 0, s_tready;
    logic m_tlast, m_tvalid, m_tready = 0;

    axis_skid_buffer #(.DATA_W(DATA_W), .USER_W(USER_W)) dut (.*);

    always #2 clk = ~clk;

    // Expected transfers, in order
    logic [DATA_W+USER_W:0] expected [$];
    int sent = 0, received = 0, errors = 0;
    int in_pct = 70, out_pct = 70;       // probability (%) of valid / ready each cycle

    // Source: holds data stable until accepted (AXI-Stream rule)
    always @(posedge clk) begin
        if (rst) begin
            s_tvalid <= 0;
        end else begin
            if (s_tvalid && s_tready) begin
                expected.push_back({s_tlast, s_tuser, s_tdata});
                sent++;
            end
            if (!s_tvalid || s_tready) begin
                if (sent < N_RANDOM + N_STREAM && $urandom_range(0, 99) < in_pct) begin
                    s_tvalid <= 1;
                    s_tdata  <= $urandom;
                    s_tuser  <= $urandom;
                    s_tlast  <= $urandom_range(0, 7) == 0;
                end else begin
                    s_tvalid <= 0;
                end
            end
        end
    end

    // Sink: random ready, compare every transfer
    always @(posedge clk) begin
        if (!rst && m_tvalid && m_tready) begin
            if (expected.size() == 0) begin
                errors++;
                $display("  output transfer with nothing expected");
            end else if ({m_tlast, m_tuser, m_tdata} !== expected[0]) begin
                errors++;
                if (errors <= 10) $display("  transfer %0d: got %h, expected %h", received, {m_tlast, m_tuser, m_tdata}, expected[0]);
                void'(expected.pop_front());
            end else begin
                void'(expected.pop_front());
            end
            received++;
        end
        m_tready <= ($urandom_range(0, 99) < out_pct);
    end

    // AXI-Stream rule: once valid, output must hold until accepted
    logic [DATA_W+USER_W:0] held;
    logic                   was_stalled = 0;
    always @(posedge clk) begin
        if (!rst && was_stalled && (!m_tvalid || {m_tlast, m_tuser, m_tdata} !== held)) begin
            errors++;
            $display("  output changed while stalled");
        end
        was_stalled <= m_tvalid && !m_tready;
        held        <= {m_tlast, m_tuser, m_tdata};
    end

    int t_start, t_end;

    initial begin
        repeat (5) @(posedge clk);
        rst <= 0;

        // Phase 1: random valid and ready
        wait (sent >= N_RANDOM);

        // Phase 2: both sides always ready -> one transfer per cycle
        in_pct  = 100;
        out_pct = 100;
        repeat (10) @(posedge clk);
        t_start = received;
        repeat (N_STREAM / 2) @(posedge clk);
        t_end = received;
        if (t_end - t_start != N_STREAM / 2) begin
            errors++;
            $display("  throughput: %0d transfers in %0d cycles", t_end - t_start, N_STREAM / 2);
        end

        wait (sent == N_RANDOM + N_STREAM && received == sent);
        repeat (5) @(posedge clk);
        if (errors == 0 && expected.size() == 0)
            $display("PASS: tb_axis_skid_buffer, %0d transfers", received);
        else
            $display("FAIL: tb_axis_skid_buffer, %0d errors", errors);
        $finish;
    end

    initial begin
        #10ms;
        $display("FAIL: tb_axis_skid_buffer, timeout (%0d sent, %0d received)", sent, received);
        $finish;
    end
endmodule
