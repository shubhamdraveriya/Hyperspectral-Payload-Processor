`timescale 1ns / 1ps

// RAM of 16-bit samples packed four per 64-bit word, so one
// UltraRAM word holds four samples instead of one (8 URAM instead of 32 for
// 128 Ki samples), with the memory's built-in ECC (ecc_ram, SPEC RAD-02).
//
// ECC words are written whole, so writes are gathered. Writes come in runs of
// consecutive samples that start on a word boundary (lane 0); wlast marks a
// run's last sample. A word is written when its lane 3 or the run's last
// sample arrives (lanes after the end of a run are written as zero). A run
// may be abandoned by starting a new one: its unwritten lanes are lost.
// Until its word is written, a sample is not readable: callers read samples
// of finished words only (both callers read ahead of, or away from, the word
// being gathered).
//
// A read returns the addressed lane LATENCY cycles later (with LATENCY 2,
// advanced by ce; longer latencies need ce high, see ecc_ram). re marks the
// reads whose errors count.
module sample_ram import ecc_pkg::*; #(
    parameter int DEPTH   = 131072,     // samples, multiple of 4
    parameter     STYLE   = "ultra",    // "ultra" or "block"
    parameter int LATENCY = 2           // >= 2
) (
    input  logic                     clk,
    input  logic                     rst,
    input  logic                     ce,
    input  logic                     we,
    input  logic                     wlast,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [15:0]              wdata,
    input  logic                     re,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [15:0]              rdata,
    input  ecc_inj_t                 inj,
    output ecc_evt_t                 ecc
);
    localparam int AB = $clog2(DEPTH);

    // Write gathering: lanes 0 - 2 of the word being written
    logic [15:0] g [3];
    logic [63:0] word;
    wire  [1:0]  wl     = waddr[1:0];
    wire         wr_now = we && (wl == 2'd3 || wlast);

    always_comb
        for (int i = 0; i < 4; i++)
            word[16 * i +: 16] = (i == wl) ? wdata : (i < wl) ? g[i % 3] : 16'd0;

    always_ff @(posedge clk)
        if (we && wl != 2'd3) g[wl] <= wdata;

    // Error injection: armed until the next write of the target word
    logic          armed, armed_dbe;
    logic [AB-3:0] target;
    wire           inj_now = armed && wr_now && waddr[AB-1:2] == target;
    always_ff @(posedge clk) begin
        if (rst) begin
            armed <= 1'b0;
        end else if (inj.arm) begin
            armed     <= 1'b1;
            armed_dbe <= inj.dbe;
            target    <= inj.addr[AB-1:2];
        end else if (inj_now) begin
            armed <= 1'b0;
        end
    end

    logic [63:0] q;
    logic [1:0]  lane [LATENCY];

    ecc_ram #(
        .DEPTH   (DEPTH / 4),
        .STYLE   (STYLE),
        .LATENCY (LATENCY)
    ) u_mem (
        .clk     (clk),
        .rst     (rst),
        .ce      (ce),
        .we      (wr_now),
        .waddr   (waddr[AB-1:2]),
        .wdata   (word),
        .inj_sbe (inj_now && !armed_dbe),
        .inj_dbe (inj_now && armed_dbe),
        .re      (re),
        .raddr   (raddr[AB-1:2]),
        .rdata   (q),
        .sbe     (ecc.sbe),
        .dbe     (ecc.dbe)
    );

    assign ecc.pending = armed;

    always_ff @(posedge clk) begin
        if (ce) begin
            lane[0] <= raddr[1:0];
            for (int i = 1; i < LATENCY; i++) lane[i] <= lane[i - 1];
        end
    end

    assign rdata = q[16 * lane[LATENCY - 1] +: 16];

    // synthesis translate_off
    // A run must start on a word boundary
    logic          in_run;
    logic [AB-1:0] next_addr;
    always_ff @(posedge clk) begin
        if (rst) begin
            in_run <= 1'b0;
        end else if (we) begin
            if (wl != 2'd0 && !(in_run && waddr == next_addr))
                $error("sample_ram: write at %0d starts a run inside a word", waddr);
            in_run    <= !wlast;
            next_addr <= waddr + 1'b1;
        end
    end
    // synthesis translate_on
endmodule
