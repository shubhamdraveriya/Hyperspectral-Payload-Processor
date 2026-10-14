`timescale 1ns / 1ps

// Test of sample_ram with ECC (SPEC RAD-02), in the two ways it is used:
//   u_ct: UltraRAM, latency 4, ce always high (corner turn)
//   u_lb: block RAM, latency 2, random ce stalls (compressor line buffer)
// Random runs of writes (word-aligned starts, random lengths, some abandoned),
// random reads anywhere, including the word being gathered (which must still
// return its previous content), and error injections into target words:
// single-bit errors must be corrected (data intact) and counted on every read
// of the word until it is rewritten; double-bit errors must be reported.
module tb_sample_ram;
    import ecc_pkg::*;

    localparam int DEPTH = 1024;        // samples
    localparam int AB    = $clog2(DEPTH);
    localparam int CYCLES = 60000;

    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;

    int errors = 0;

    // one test bench instance per configuration
    for (genvar k = 0; k < 2; k++) begin : g_dut
        localparam int    LAT   = (k == 0) ? 4 : 2;
        localparam string STYLE = (k == 0) ? "ultra" : "block";

        logic          ce = 1'b1, we = 1'b0, wlast = 1'b0, re = 1'b0;
        logic [AB-1:0] waddr = '0, raddr = '0;
        logic [15:0]   wdata = '0, rdata;
        ecc_inj_t      inj = '0;
        ecc_evt_t      ecc;

        sample_ram #(.DEPTH(DEPTH), .STYLE(STYLE), .LATENCY(LAT)) dut (
            .clk, .rst, .ce, .we, .wlast, .waddr, .wdata, .re, .raddr, .rdata, .inj, .ecc
        );

        // ---------------------------------------------------------------
        // Reference: committed words, error flags per word, gather lanes
        // ---------------------------------------------------------------
        logic [63:0] mem   [DEPTH / 4];
        logic        f_sbe [DEPTH / 4];
        logic        f_dbe [DEPTH / 4];
        logic [15:0] gl [3];
        logic        armed = 1'b0, armed_dbe;
        int          target;

        typedef struct { logic [15:0] data; logic sbe, dbe; } exp_t;
        exp_t exp_pipe [LAT];
        logic vpipe [LAT];
        logic loaded = 1'b0;
        exp_t exp_out;
        int   n_reads = 0, n_sbe = 0, n_dbe = 0, n_inj = 0, n_gather_reads = 0;

        initial begin
            for (int w = 0; w < DEPTH / 4; w++) begin
                mem[w] = '0; f_sbe[w] = 1'b0; f_dbe[w] = 1'b0;
            end
            for (int i = 0; i < LAT; i++) vpipe[i] = 1'b0;
        end

        // run state of the stimulus
        int run_left = 0, cur = 0;

        always @(posedge clk) begin
            if (!rst) begin
                // ---- check the output of the previous cycle's state ----
                if (loaded) begin
                    n_reads++;
                    if (ecc.sbe !== exp_out.sbe || ecc.dbe !== exp_out.dbe) begin
                        errors++;
                        if (errors < 10) $display("  %s: read flags sbe %b dbe %b, expected %b %b",
                                                  STYLE, ecc.sbe, ecc.dbe, exp_out.sbe, exp_out.dbe);
                    end
                    if (!exp_out.dbe && rdata !== exp_out.data) begin
                        errors++;
                        if (errors < 10) $display("  %s: read %04h, expected %04h", STYLE, rdata, exp_out.data);
                    end
                    if (ecc.sbe) n_sbe++;
                    if (ecc.dbe) n_dbe++;
                end else if (ecc.sbe || ecc.dbe) begin
                    errors++;
                    if (errors < 10) $display("  %s: error flag without a read", STYLE);
                end
                if (ecc.pending !== armed) begin
                    errors++;
                    if (errors < 10) $display("  %s: pending %b, expected %b", STYLE, ecc.pending, armed);
                end

                // ---- reference model of this cycle's inputs ----
                // read (read-first: before this cycle's write)
                if (ce) begin
                    exp_t e;
                    int w;
                    w = raddr / 4;
                    e.data = mem[w][16 * (raddr % 4) +: 16];
                    e.sbe  = f_sbe[w] && !f_dbe[w];
                    e.dbe  = f_dbe[w];
                    loaded = vpipe[LAT - 2];
                    exp_out = exp_pipe[LAT - 2];
                    for (int i = LAT - 1; i > 0; i--) begin
                        exp_pipe[i] = exp_pipe[i - 1];
                        vpipe[i]    = vpipe[i - 1];
                    end
                    exp_pipe[0] = e;
                    vpipe[0]    = re;
                    if (re && we && waddr / 4 == w && waddr % 4 != 3 && !wlast) n_gather_reads++;
                end else begin
                    loaded = 1'b0;
                end
                // write
                if (we) begin
                    int w, l;
                    logic [63:0] word;
                    w = waddr / 4;
                    l = waddr % 4;
                    if (l == 3 || wlast) begin
                        for (int i = 0; i < 4; i++)
                            word[16 * i +: 16] = (i == l) ? wdata : (i < l) ? gl[i % 3] : 16'd0;
                        mem[w] = word;
                        if (armed && w == target) begin
                            f_sbe[w] = !armed_dbe;
                            f_dbe[w] = armed_dbe;
                            armed = 1'b0;
                        end else begin
                            f_sbe[w] = 1'b0;
                            f_dbe[w] = 1'b0;
                        end
                    end
                    if (l != 3) gl[l] = wdata;
                end
                if (inj.arm) begin
                    armed     = 1'b1;
                    armed_dbe = inj.dbe;
                    target    = inj.addr / 4;
                    n_inj++;
                end
            end

            // ---- next stimulus ----
            inj.arm <= 1'b0;
            if (rst) begin
                we <= 1'b0; re <= 1'b0;
            end else begin
                logic ce_n;
                ce_n = (k == 0) ? 1'b1 : ($urandom_range(0, 99) < 75);
                ce <= ce_n;
                // writes advance only with ce (as in the callers)
                if (ce_n && $urandom_range(0, 99) < 80) begin
                    if (run_left == 0 || $urandom_range(0, 999) < 3) begin
                        // new run (possibly abandoning the current one)
                        cur      = $urandom_range(0, DEPTH / 4 - 1) * 4;
                        run_left = $urandom_range(1, 40);
                        if (cur + run_left > DEPTH) run_left = DEPTH - cur;
                    end
                    we    <= 1'b1;
                    waddr <= AB'(cur);
                    wdata <= 16'($urandom);
                    wlast <= (run_left == 1);
                    cur++;
                    run_left--;
                end else begin
                    we <= 1'b0;
                end
                // reads: mostly near the writes (the gathered word), else anywhere
                re <= ce_n && ($urandom_range(0, 99) < 70);
                raddr <= ($urandom_range(0, 1) && cur < DEPTH) ? AB'(cur - $urandom_range(0, 3)) : AB'($urandom_range(0, DEPTH - 1));
                // injections into words about to be written
                if (!armed && !inj.arm && $urandom_range(0, 999) < 20) begin
                    inj.arm  <= 1'b1;
                    inj.dbe  <= ($urandom_range(0, 3) == 0);
                    inj.addr <= 20'((cur < DEPTH && run_left > 4) ? cur + 4 : $urandom_range(0, DEPTH - 1));
                end
            end
        end
    end

    initial begin
        repeat (10) @(posedge clk);
        rst <= 1'b0;
        repeat (CYCLES) @(posedge clk);
        if (g_dut[0].n_sbe == 0 || g_dut[0].n_dbe == 0 || g_dut[1].n_sbe == 0 || g_dut[1].n_dbe == 0) begin
            errors++;
            $display("  error flags never seen");
        end
        if (g_dut[0].n_gather_reads == 0 || g_dut[1].n_gather_reads == 0) begin
            errors++;
            $display("  no read hit a word being gathered");
        end
        if (errors == 0)
            $display("PASS: tb_sample_ram, ultra/L4: %0d reads (%0d SBE, %0d DBE, %0d injections), block/L2 with stalls: %0d reads (%0d SBE, %0d DBE, %0d injections)",
                     g_dut[0].n_reads, g_dut[0].n_sbe, g_dut[0].n_dbe, g_dut[0].n_inj,
                     g_dut[1].n_reads, g_dut[1].n_sbe, g_dut[1].n_dbe, g_dut[1].n_inj);
        else
            $display("FAIL: tb_sample_ram: %0d errors", errors);
        $finish;
    end
endmodule
