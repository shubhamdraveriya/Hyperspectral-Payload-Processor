`timescale 1ns / 1ps

// CCSDS Reed-Solomon (255,223) encoder, E = 16, interleaving depth I = 4
// (SPEC.md 6.7, CC-01/CC-02), as rs.encode_interleaved(): an 892-byte frame
// in, a 1020-byte codeblock out (the frame, then 128 parity bytes).
//
// Byte k of the frame belongs to codeword k mod 4. The symbols are in the
// dual basis on the channel: data bytes pass through unchanged, the parity is
// computed in the conventional basis (one shared LFSR engine; the four
// codeword states rotate through it, one per byte) and converted back.
// Parity order: symbol i of codewords 0..3, for i = 0..31; feeding a state's
// own first symbol back as input shifts it out with zero feedback.
module rs_encoder import downlink_pkg::*; (
    input  logic       clk,
    input  logic       rst,

    input  logic [7:0] s_axis_tdata,
    input  logic       s_axis_tlast,            // last byte of the frame
    input  logic       s_axis_tvalid,
    output logic       s_axis_tready,

    output logic [7:0] m_axis_tdata,
    output logic       m_axis_tlast,            // last byte of the codeblock
    output logic       m_axis_tvalid,
    input  logic       m_axis_tready
);
    localparam int NPAR = 32 * RS_DEPTH;        // 128

    typedef logic [7:0] state_t [32];

    state_t      st [RS_DEPTH];                 // st[0]: codeword of the next byte
    logic        parity;                        // sending the parity bytes
    logic [6:0]  pi;                            // parity byte index

    wire advance = !m_axis_tvalid || m_axis_tready;
    wire take    = !parity && advance && s_axis_tvalid;
    wire step    = take || (parity && advance);

    assign s_axis_tready = !parity && advance;

    // LFSR step of state st[0] with input symbol d (conventional basis)
    function automatic state_t lfsr(input state_t s, input logic [7:0] d);
        state_t n;
        logic [7:0] fb;
        fb = d ^ s[0];
        for (int i = 0; i < 32; i++)
            n[i] = ((i < 31) ? s[i + 1] : 8'h00) ^ gf_mul(fb, RS_GEN[i]);
        return n;
    endfunction

    wire [7:0] d_conv = parity ? st[0][0] : from_dual(s_axis_tdata);

    always_ff @(posedge clk) begin
        if (rst) begin
            parity        <= 1'b0;
            pi            <= '0;
            m_axis_tvalid <= 1'b0;
            for (int j = 0; j < RS_DEPTH; j++) st[j] <= '{default: 8'h00};
        end else begin
            if (advance) m_axis_tvalid <= 1'b0;
            if (step) begin
                // rotate: the updated state goes to the back
                for (int j = 0; j < RS_DEPTH - 1; j++) st[j] <= st[j + 1];
                st[RS_DEPTH - 1] <= lfsr(st[0], d_conv);
                m_axis_tvalid <= 1'b1;
                m_axis_tdata  <= parity ? to_dual(st[0][0]) : s_axis_tdata;
                m_axis_tlast  <= parity && (pi == 7'(NPAR - 1));
            end
            if (take && s_axis_tlast) begin
                parity <= 1'b1;
                pi     <= '0;
            end
            if (parity && advance) begin
                pi <= pi + 7'd1;
                if (pi == 7'(NPAR - 1)) begin
                    parity <= 1'b0;
                    // the shifted-out states are all zero again: ready for the next frame
                end
            end
        end
    end
endmodule
