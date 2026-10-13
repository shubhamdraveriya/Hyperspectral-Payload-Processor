`timescale 1ns / 1ps

// Small memory with asynchronous read (LUT RAM) kept in three copies (SPEC
// RAD-03): writes go to all three, a read returns the bitwise majority, so an
// upset in one copy is masked, and gone at the entry's next write. `mismatch`
// flags a read where the copies disagree. The copies are separate instances
// with DONT_TOUCH, so synthesis does not merge them.
module tmr_ram #(
    parameter int WIDTH = 16,
    parameter int DEPTH = 256
) (
    input  logic                     clk,
    input  logic                     we,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [WIDTH-1:0]         wdata,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [WIDTH-1:0]         rdata,
    output logic                     mismatch
);
    logic [WIDTH-1:0] q [3];

    for (genvar c = 0; c < 3; c++) begin : g_copy
        (* DONT_TOUCH = "true", KEEP_HIERARCHY = "yes" *)
        lut_ram #(.WIDTH (WIDTH), .DEPTH (DEPTH)) u_copy (
            .clk, .we, .waddr, .wdata, .raddr, .rdata (q[c])
        );
    end

    assign rdata    = (q[0] & q[1]) | (q[0] & q[2]) | (q[1] & q[2]);
    assign mismatch = (q[0] != q[1]) || (q[0] != q[2]);
endmodule

// One copy: LUT RAM, synchronous write, asynchronous read
module lut_ram #(
    parameter int WIDTH = 16,
    parameter int DEPTH = 256
) (
    input  logic                     clk,
    input  logic                     we,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [WIDTH-1:0]         wdata,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [WIDTH-1:0]         rdata
);
    (* ram_style = "distributed" *) logic [WIDTH-1:0] mem [DEPTH];

    always_ff @(posedge clk)
        if (we) mem[waddr] <= wdata;

    assign rdata = mem[raddr];
endmodule
