`timescale 1ns / 1ps

// Simple dual-port RAM, one write and one read port. Reads take LATENCY
// cycles (memory latch + output pipeline, all advanced by ce); deep UltraRAM
// cascades need LATENCY 3 - 4 to absorb the cascade pipeline registers. Writes
// have one enable per BYTE_W bits (UltraRAM: 9-bit bytes at width 72). Reads
// and writes to the same address in the same cycle are not used by the callers.
module sdp_ram #(
    parameter int WIDTH   = 16,
    parameter int DEPTH   = 1024,
    parameter     STYLE   = "block",    // "block", "ultra" or "distributed"
    parameter int LATENCY = 2,          // >= 2
    parameter int BYTE_W  = WIDTH       // write enable granularity, divides WIDTH
) (
    input  logic                     clk,
    input  logic                     ce,
    input  logic [WIDTH/BYTE_W-1:0]  we,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [WIDTH-1:0]         wdata,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [WIDTH-1:0]         rdata
);
    (* ram_style = STYLE *) logic [WIDTH-1:0] mem [DEPTH];
    logic [WIDTH-1:0] q [LATENCY];

    always_ff @(posedge clk) begin
        for (int b = 0; b < WIDTH / BYTE_W; b++)
            if (we[b]) mem[waddr][BYTE_W * b +: BYTE_W] <= wdata[BYTE_W * b +: BYTE_W];
    end

    always_ff @(posedge clk) begin
        if (ce) begin
            q[0] <= mem[raddr];
            for (int i = 1; i < LATENCY; i++) q[i] <= q[i - 1];
        end
    end

    assign rdata = q[LATENCY - 1];
endmodule
