`timescale 1ns / 1ps

// Link transmitter (SPEC.md 6.9, LNK-03), clk_link domain, 16-bit words to the
// GTH (which does the 8b/10b encoding): two data bytes per word (first byte
// in [7:0]), idle words {D16.2, K28.5} when no data is waiting, and an idle
// word at least every cc_interval words, which the receiver's elastic buffer
// uses for clock correction.
module link_tx (
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] cc_interval,            // words, >= 2
    input  logic        cnt_clr,                // clear the word counters

    input  logic [15:0] s_data,                 // FWFT FIFO: data valid when !s_empty
    input  logic        s_empty,
    output logic        s_rd,

    output logic [15:0] txdata,
    output logic [1:0]  txcharisk,              // 1: K character

    output logic [31:0] data_words,
    output logic [31:0] idle_words
);
    localparam logic [15:0] IDLE = 16'h50BC;    // byte 0 K28.5, byte 1 D16.2

    logic [15:0] since_cc;

    wire cc_due = (since_cc + 16'd1 >= cc_interval);
    assign s_rd = !s_empty && !cc_due;

    always_ff @(posedge clk) begin
        if (rst) begin
            since_cc   <= '0;
            txdata     <= IDLE;
            txcharisk  <= 2'b01;
            data_words <= '0;
            idle_words <= '0;
        end else if (s_rd) begin
            txdata     <= s_data;
            txcharisk  <= 2'b00;
            since_cc   <= since_cc + 16'd1;
            data_words <= data_words + 32'd1;
        end else begin
            txdata     <= IDLE;
            txcharisk  <= 2'b01;
            since_cc   <= '0;
            idle_words <= idle_words + 32'd1;
        end
        if (!rst && cnt_clr) begin
            data_words <= '0;
            idle_words <= '0;
        end
    end
endmodule
