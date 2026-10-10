`timescale 1ns / 1ps

// Previous-row line buffer with four 16-bit samples per 64-bit word, so one
// UltraRAM word holds four samples instead of one (8 URAM instead of 32 at
// 512 x 256). A write updates its own lane through byte write enables; a read
// returns the addressed lane 2 cycles later (advanced by ce).
//
// c123_neighborhood reads Nz samples ahead of the write address, so with
// Nz >= 4 reads and writes never touch the same word, and the other lanes of a
// word being written hold previous-row samples that were already read.
module c123_linebuf #(
    parameter int DEPTH = 131072,       // samples, multiple of 4
    parameter     STYLE = "ultra"
) (
    input  logic                     clk,
    input  logic                     ce,
    input  logic                     we,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [15:0]              wdata,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [15:0]              rdata
);
    localparam int AB = $clog2(DEPTH);

    (* ram_style = STYLE *) logic [63:0] mem [DEPTH / 4];
    logic [63:0] q1, q2;
    logic [1:0]  lane1, lane2;

    // byte enables of the addressed 16-bit lane
    logic [7:0] be;
    always_comb begin
        be = '0;
        be[2 * waddr[1:0] +: 2] = 2'b11;
    end

    always_ff @(posedge clk) begin
        for (int b = 0; b < 8; b++)
            if (we && be[b]) mem[waddr[AB-1:2]][8 * b +: 8] <= wdata[8 * (b % 2) +: 8];
    end

    always_ff @(posedge clk) begin
        if (ce) begin
            q1    <= mem[raddr[AB-1:2]];
            lane1 <= raddr[1:0];
            q2    <= q1;
            lane2 <= lane1;
        end
    end

    assign rdata = q2[16 * lane2 +: 16];
endmodule
