`timescale 1ns / 1ps

// Single-clock first-word-fall-through FIFO (distributed RAM).
// dout is valid whenever empty is low; rd_en pops it.
module sync_fifo #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 32            // power of two
) (
    input  logic                   clk,
    input  logic                   rst,
    input  logic [WIDTH-1:0]       din,
    input  logic                   wr_en,
    input  logic                   rd_en,
    output logic [WIDTH-1:0]       dout,
    output logic                   empty,
    output logic                   full,
    output logic [$clog2(DEPTH):0] count
);
    localparam int AW = $clog2(DEPTH);

    (* ram_style = "distributed" *) logic [WIDTH-1:0] mem [DEPTH];
    logic [AW-1:0] wr_ptr, rd_ptr;

    wire do_wr = wr_en && !full;
    wire do_rd = rd_en && !empty;

    always_ff @(posedge clk) begin
        if (do_wr) mem[wr_ptr] <= din;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            wr_ptr <= '0;
            rd_ptr <= '0;
            count  <= '0;
        end else begin
            if (do_wr) wr_ptr <= wr_ptr + 1;
            if (do_rd) rd_ptr <= rd_ptr + 1;
            if (do_wr && !do_rd)      count <= count + 1;
            else if (do_rd && !do_wr) count <= count - 1;
        end
    end

    assign dout  = mem[rd_ptr];
    assign empty = (count == 0);
    assign full  = (count == DEPTH);

    // synthesis translate_off
    always @(posedge clk) begin
        if (!rst && wr_en && full) $error("%m: write while full, data lost");
    end
    // synthesis translate_on
endmodule
