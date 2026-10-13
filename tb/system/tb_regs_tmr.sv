`timescale 1ns / 1ps

// Test of hpdp_regs_tmr: AXI4-Lite writes and reads of configuration registers;
// an upset forced into one copy's register (each copy in turn) must not change
// the configuration output nor the value read back, must show as a mismatch,
// and must be gone after the register is written again.
module tb_regs_tmr;
    import hpdp_regs_addr_pkg::*;

    logic clk = 1'b0, rst = 1'b1;
    always #5 clk = ~clk;

    logic        awvalid = 1'b0, wvalid = 1'b0, bready = 1'b1, arvalid = 1'b0, rready = 1'b1;
    logic        awready, wready, bvalid, arready, rvalid, mismatch;
    logic [19:0] awaddr = '0, araddr = '0;
    logic [31:0] wdata = '0, rdata;
    logic [1:0]  bresp, rresp;
    hpdp_regs_pkg::hpdp_regs__in_t  hwif_in = '{default: '0};
    hpdp_regs_pkg::hpdp_regs__out_t hwif_out;

    hpdp_regs_tmr dut (
        .clk, .rst,
        .s_axil_awready (awready), .s_axil_awvalid (awvalid), .s_axil_awaddr (awaddr), .s_axil_awprot (3'b000),
        .s_axil_wready (wready), .s_axil_wvalid (wvalid), .s_axil_wdata (wdata), .s_axil_wstrb (4'hF),
        .s_axil_bready (bready), .s_axil_bvalid (bvalid), .s_axil_bresp (bresp),
        .s_axil_arready (arready), .s_axil_arvalid (arvalid), .s_axil_araddr (araddr), .s_axil_arprot (3'b000),
        .s_axil_rready (rready), .s_axil_rvalid (rvalid), .s_axil_rdata (rdata), .s_axil_rresp (rresp),
        .hwif_in, .hwif_out, .mismatch
    );

    task automatic wr(logic [19:0] a, logic [31:0] d);
        @(posedge clk);
        awvalid <= 1'b1; awaddr <= a; wvalid <= 1'b1; wdata <= d;
        fork
            begin @(posedge clk iff awready); awvalid <= 1'b0; end
            begin @(posedge clk iff wready);  wvalid  <= 1'b0; end
        join
        @(posedge clk iff bvalid);
    endtask

    task automatic rd(logic [19:0] a, output logic [31:0] d);
        @(posedge clk);
        arvalid <= 1'b1; araddr <= a;
        @(posedge clk iff arready);
        arvalid <= 1'b0;
        @(posedge clk iff rvalid);
        d = rdata;
    endtask

    int errors = 0;
    initial begin
        logic [31:0] v;
        repeat (5) @(posedge clk);
        rst <= 1'b0;
        repeat (5) @(posedge clk);
        wr(RAD_WD_TIMEOUT & 20'hFFFFF, 32'h1234_5678);
        wr(SE_LINE_PERIOD & 20'hFFFFF, 32'd4321);
        for (int c = 0; c < 3; c++) begin
            // an upset in copy c
            case (c)
                0: dut.g_copy[0].u_regs.field_storage.rad.WD_TIMEOUT.cycles.value[7] =
                       !dut.g_copy[0].u_regs.field_storage.rad.WD_TIMEOUT.cycles.value[7];
                1: dut.g_copy[1].u_regs.field_storage.rad.WD_TIMEOUT.cycles.value[7] =
                       !dut.g_copy[1].u_regs.field_storage.rad.WD_TIMEOUT.cycles.value[7];
                default: dut.g_copy[2].u_regs.field_storage.rad.WD_TIMEOUT.cycles.value[7] =
                       !dut.g_copy[2].u_regs.field_storage.rad.WD_TIMEOUT.cycles.value[7];
            endcase
            repeat (2) @(posedge clk);
            if (hwif_out.rad.WD_TIMEOUT.cycles.value !== 32'h1234_5678 || mismatch !== 1'b1) begin
                errors++;
                $display("  copy %0d upset: output %08h, mismatch %b", c, hwif_out.rad.WD_TIMEOUT.cycles.value, mismatch);
            end
            rd(RAD_WD_TIMEOUT & 20'hFFFFF, v);
            if (v !== 32'h1234_5678) begin errors++; $display("  copy %0d upset: read %08h", c, v); end
            rd(SE_LINE_PERIOD & 20'hFFFFF, v);
            if (v !== 32'd4321) begin errors++; $display("  copy %0d upset: other register read %08h", c, v); end
            wr(RAD_WD_TIMEOUT & 20'hFFFFF, 32'h1234_5678);      // heals the copy
            repeat (2) @(posedge clk);
            if (mismatch !== 1'b0) begin errors++; $display("  copy %0d: mismatch after the rewrite", c); end
        end
        if (errors == 0)
            $display("PASS: tb_regs_tmr, an upset in each copy masked (output and reads) and healed");
        else
            $display("FAIL: tb_regs_tmr: %0d errors", errors);
        $finish;
    end

    initial begin
        #1ms;
        $display("FAIL: tb_regs_tmr timeout");
        $finish;
    end
endmodule
