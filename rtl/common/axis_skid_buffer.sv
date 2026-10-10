`timescale 1ns / 1ps

// AXI4-Stream register slice ("skid buffer").
//
// Registers tdata/tuser/tlast/tvalid towards the output and s_tready towards
// the input, so no combinational path crosses the block (SPEC.md 5.4), while
// still passing one transfer per cycle. When the output stalls, the one
// transfer already accepted is parked in the skid register.
module axis_skid_buffer #(
    parameter int DATA_W = 8,
    parameter int USER_W = 1
) (
    input  logic              clk,
    input  logic              rst,

    input  logic [DATA_W-1:0] s_tdata,
    input  logic [USER_W-1:0] s_tuser,
    input  logic              s_tlast,
    input  logic              s_tvalid,
    output logic              s_tready,

    output logic [DATA_W-1:0] m_tdata,
    output logic [USER_W-1:0] m_tuser,
    output logic              m_tlast,
    output logic              m_tvalid,
    input  logic              m_tready
);
    localparam int W = DATA_W + USER_W + 1;

    logic [W-1:0] s_bus, m_bus, skid_bus;
    logic         skid_valid;

    assign s_bus = {s_tlast, s_tuser, s_tdata};
    assign {m_tlast, m_tuser, m_tdata} = m_bus;

    wire s_fire     = s_tvalid && s_tready;
    wire m_can_load = !m_tvalid || m_tready;

    always_ff @(posedge clk) begin
        if (rst) begin
            m_tvalid   <= 1'b0;
            skid_valid <= 1'b0;
            s_tready   <= 1'b0;
        end else begin
            if (m_can_load) begin
                if (skid_valid) begin
                    // Drain the parked transfer first (s_tready was low)
                    m_bus      <= skid_bus;
                    m_tvalid   <= 1'b1;
                    skid_valid <= 1'b0;
                end else begin
                    m_bus      <= s_bus;
                    m_tvalid   <= s_fire;
                end
            end else if (s_fire) begin
                // Output stalled: park the transfer accepted this cycle
                skid_bus   <= s_bus;
                skid_valid <= 1'b1;
            end
            // Ready next cycle unless the skid register will hold data
            s_tready <= m_can_load || (!s_fire && !skid_valid);
        end
    end
endmodule
