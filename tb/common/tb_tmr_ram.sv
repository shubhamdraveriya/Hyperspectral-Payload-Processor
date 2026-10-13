`timescale 1ns / 1ps

// Test of tmr_ram: random writes and reads against a reference; upsets forced
// into one copy at a time (any bit pattern) must not show at the output, must
// be flagged as a mismatch while present, and must be gone after the entry's
// next write.
module tb_tmr_ram;
    localparam int W = 16, D = 32;

    logic clk = 1'b0;
    always #2 clk = ~clk;

    logic                 we = 1'b0, mismatch;
    logic [$clog2(D)-1:0] waddr = '0, raddr = '0;
    logic [W-1:0]         wdata = '0, rdata;

    tmr_ram #(.WIDTH (W), .DEPTH (D)) dut (.clk, .we, .waddr, .wdata, .raddr, .rdata, .mismatch);

    logic [W-1:0] ref_mem [D];
    logic         upset   [D];
    int errors = 0, checks = 0, flagged = 0, upsets = 0;

    initial begin
        for (int a = 0; a < D; a++) begin
            @(negedge clk);
            we = 1'b1; waddr = a; wdata = W'($urandom);
            ref_mem[a] = wdata; upset[a] = 1'b0;
        end
        @(negedge clk);
        we = 1'b0;
        for (int n = 0; n < 4000; n++) begin
            @(negedge clk);
            // sometimes an upset in one copy of a random entry
            if ($urandom_range(0, 99) < 5) begin
                int a, c;
                a = $urandom_range(0, D - 1);
                c = $urandom_range(0, 2);
                if (!upset[a]) begin
                    case (c)
                        0: dut.g_copy[0].u_copy.mem[a] = dut.g_copy[0].u_copy.mem[a] ^ W'($urandom | 1);
                        1: dut.g_copy[1].u_copy.mem[a] = dut.g_copy[1].u_copy.mem[a] ^ W'($urandom | 1);
                        default: dut.g_copy[2].u_copy.mem[a] = dut.g_copy[2].u_copy.mem[a] ^ W'($urandom | 1);
                    endcase
                    upset[a] = 1'b1;
                    upsets++;
                end
            end
            // a write (heals the entry) or a read check
            we    = ($urandom_range(0, 99) < 30);
            waddr = $urandom_range(0, D - 1);
            wdata = W'($urandom);
            raddr = $urandom_range(0, D - 1);
            #1;
            checks++;
            if (rdata !== ref_mem[raddr] || mismatch !== upset[raddr]) begin
                errors++;
                if (errors < 10) $display("  read %0d: %04h mismatch %b, expected %04h %b",
                                          raddr, rdata, mismatch, ref_mem[raddr], upset[raddr]);
            end
            if (mismatch) flagged++;
            @(posedge clk);
            if (we) begin
                ref_mem[waddr] = wdata;
                upset[waddr]   = 1'b0;
            end
        end
        if (upsets == 0 || flagged == 0) begin errors++; $display("  no upset exercised"); end
        if (errors == 0)
            $display("PASS: tb_tmr_ram, %0d reads, %0d upsets, %0d reads flagged", checks, upsets, flagged);
        else
            $display("FAIL: tb_tmr_ram: %0d errors", errors);
        $finish;
    end
endmodule
