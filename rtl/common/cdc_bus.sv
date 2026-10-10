`timescale 1ns / 1ps

// Carries a slowly changing bus (configuration, status counters) to another
// clock domain as consistent snapshots: xpm_cdc_handshake, resent continuously.
// dest_data follows src_data within one handshake round trip (about
// 3 src_clk + 3 dest_clk cycles per transfer, two transfers worst case); every
// update delivers all bits from the same src_clk cycle, so related fields never
// mix old and new values. Events cross as counters inside the bus (the
// receiver acts when a count changes), not as pulses.
module cdc_bus #(
    parameter int WIDTH = 32
) (
    input  logic             src_clk,
    input  logic             src_rst,
    input  logic [WIDTH-1:0] src_data,
    input  logic             dest_clk,
    input  logic             dest_rst,
    output logic [WIDTH-1:0] dest_data
);
    logic             src_send, src_rcv, dest_req;
    logic [WIDTH-1:0] dest_out;

    // send, wait for the acknowledge, release, wait for it to drop, repeat
    always_ff @(posedge src_clk) begin
        if (src_rst)
            src_send <= 1'b0;
        else if (src_send == src_rcv)
            src_send <= !src_send;
    end

    xpm_cdc_handshake #(
        .DEST_EXT_HSK   (0),
        .DEST_SYNC_FF   (2),
        .INIT_SYNC_FF   (0),
        .SIM_ASSERT_CHK (0),
        .SRC_SYNC_FF    (2),
        .WIDTH          (WIDTH)
    ) u_hs (
        .src_clk  (src_clk),
        .src_in   (src_data),
        .src_send (src_send),
        .src_rcv  (src_rcv),
        .dest_clk (dest_clk),
        .dest_out (dest_out),
        .dest_req (dest_req),
        .dest_ack (1'b0)
    );

    // zero after reset until the first transfer
    logic [WIDTH-1:0] q = '0;
    always_ff @(posedge dest_clk) begin
        if (dest_rst)
            q <= '0;
        else if (dest_req)
            q <= dest_out;
    end
    assign dest_data = q;
endmodule
