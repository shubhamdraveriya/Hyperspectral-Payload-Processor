`timescale 1ns / 1ps

// RAM of 16-bit samples packed four per 64-bit word, so one
// UltraRAM word holds four samples instead of one (8 URAM instead of 32 for
// 128 Ki samples). A write updates its own lane through byte write enables; a read
// returns the addressed lane LATENCY cycles later (advanced by ce). Deep
// UltraRAM cascades need LATENCY 3 - 4 to absorb the cascade pipeline registers.
//
// Callers must not read a word in the cycle another lane of it is written.
module sample_ram #(
    parameter int DEPTH   = 131072,     // samples, multiple of 4
    parameter     STYLE   = "ultra",
    parameter int LATENCY = 2           // >= 2
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
    logic [63:0] q    [LATENCY];
    logic [1:0]  lane [LATENCY];

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
            q[0]    <= mem[raddr[AB-1:2]];
            lane[0] <= raddr[1:0];
            for (int i = 1; i < LATENCY; i++) begin
                q[i]    <= q[i - 1];
                lane[i] <= lane[i - 1];
            end
        end
    end

    assign rdata = q[LATENCY - 1][16 * lane[LATENCY - 1] +: 16];
endmodule
