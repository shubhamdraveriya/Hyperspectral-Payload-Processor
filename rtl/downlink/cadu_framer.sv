`timescale 1ns / 1ps

// Pseudo-randomizer and attached sync marker (SPEC.md 6.7, CC-03/CC-04), as
// channel.make_cadu(): each 1020-byte codeblock is XORed with the PN sequence
// (restarted at every codeblock) and preceded by 1A CF FC 1D, giving a
// 1024-byte CADU.
//
// Output: tuser first byte of a CADU (the ASM), tlast last byte.
module cadu_framer import downlink_pkg::*; (
    input  logic       clk,
    input  logic       rst,
    input  logic       cnt_clr,                 // clear cadus

    input  logic [7:0] s_axis_tdata,
    input  logic       s_axis_tlast,            // last byte of the codeblock
    input  logic       s_axis_tvalid,
    output logic       s_axis_tready,

    output logic [7:0] m_axis_tdata,
    output logic       m_axis_tuser,
    output logic       m_axis_tlast,
    output logic       m_axis_tvalid,
    input  logic       m_axis_tready,

    output logic [31:0] cadus
);
    logic [2:0] ai;                             // ASM byte index, 4 = codeblock
    logic [7:0] pn;                             // randomizer state

    wire advance  = !m_axis_tvalid || m_axis_tready;
    wire in_asm   = (ai != 3'd4);
    wire take     = !in_asm && advance && s_axis_tvalid;

    assign s_axis_tready = !in_asm && advance;

    always_ff @(posedge clk) begin
        if (rst) begin
            ai            <= '0;
            pn            <= 8'hFF;
            m_axis_tvalid <= 1'b0;
            cadus         <= '0;
        end else begin
            if (advance) m_axis_tvalid <= 1'b0;
            if (in_asm && advance && s_axis_tvalid) begin
                // the ASM goes out once a codeblock is on its way
                m_axis_tvalid <= 1'b1;
                m_axis_tdata  <= ASM[8 * (3 - ai) +: 8];
                m_axis_tuser  <= (ai == 3'd0);
                m_axis_tlast  <= 1'b0;
                ai            <= ai + 3'd1;
            end
            if (take) begin
                m_axis_tvalid <= 1'b1;
                m_axis_tdata  <= s_axis_tdata ^ pn_byte(pn);
                m_axis_tuser  <= 1'b0;
                m_axis_tlast  <= s_axis_tlast;
                pn            <= s_axis_tlast ? 8'hFF : pn_next(pn);
                if (s_axis_tlast) begin
                    ai    <= '0;
                    cadus <= cadus + 32'd1;
                end
            end
            if (cnt_clr) cadus <= '0;
        end
    end
endmodule
