`timescale 1ns / 1ps

// Simple dual-port RAM, one write and one read port, 2-cycle read latency
// (memory latch + output register, both advanced by ce). Reads and writes to
// the same address in the same cycle are not used by the callers.
module sdp_ram #(
    parameter int WIDTH = 16,
    parameter int DEPTH = 1024,
    parameter     STYLE = "block"       // "block", "ultra" or "distributed"
) (
    input  logic                     clk,
    input  logic                     ce,
    input  logic                     we,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [WIDTH-1:0]         wdata,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [WIDTH-1:0]         rdata
);
    (* ram_style = STYLE *) logic [WIDTH-1:0] mem [DEPTH];
    logic [WIDTH-1:0] q;

    always_ff @(posedge clk) begin
        if (we) mem[waddr] <= wdata;
    end

    always_ff @(posedge clk) begin
        if (ce) begin
            q     <= mem[raddr];
            rdata <= q;
        end
    end
endmodule
