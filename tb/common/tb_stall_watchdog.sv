`timescale 1ns / 1ps

// Test of stall_watchdog against a reference model: random pending / progress
// patterns with long quiet stretches, timeouts 1, 2, 7 and 40, changed on the fly.
module tb_stall_watchdog;
    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;

    logic [31:0] timeout = 32'd7;
    logic        pending = 1'b0, progress = 1'b0, stalled, stall;

    stall_watchdog dut (.clk, .rst, .timeout, .pending, .progress, .stalled, .stall);

    // reference
    int cnt = 0, errors = 0, stalls = 0;
    logic m_stalled = 1'b0, m_stall = 1'b0;
    always @(posedge clk) begin
        if (!rst && (stalled !== m_stalled || stall !== m_stall)) begin
            errors++;
            if (errors < 10) $display("  stalled %b stall %b, expected %b %b", stalled, stall, m_stalled, m_stall);
        end
        if (stall) stalls++;
        m_stall = 1'b0;
        if (rst || timeout == 0 || !pending || progress) begin
            cnt = 0;
            m_stalled = 1'b0;
        end else if (!m_stalled) begin
            if (cnt == timeout - 1) begin
                m_stalled = 1'b1;
                m_stall   = 1'b1;
            end else begin
                cnt++;
            end
        end
    end

    initial begin
        repeat (5) @(posedge clk);
        rst <= 1'b0;
        for (int phase = 0; phase < 40; phase++) begin
            int p_pend, p_prog;
            timeout <= (phase % 4 == 0) ? 32'd1 : (phase % 4 == 1) ? 32'd2 : (phase % 4 == 2) ? 32'd7 : 32'd40;
            p_pend = $urandom_range(30, 100);
            p_prog = (phase % 3 == 0) ? 0 : $urandom_range(0, 30);
            repeat (500) begin
                @(posedge clk);
                pending  <= ($urandom_range(0, 99) < p_pend);
                progress <= ($urandom_range(0, 99) < p_prog);
            end
        end
        repeat (5) @(posedge clk);
        if (stalls == 0) begin errors++; $display("  no stall seen"); end
        if (errors == 0)
            $display("PASS: tb_stall_watchdog, %0d stalls", stalls);
        else
            $display("FAIL: tb_stall_watchdog: %0d errors", errors);
        $finish;
    end
endmodule
