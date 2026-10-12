`timescale 1ns / 1ps

// Constants and functions of the downlink chain (SPEC.md 6.5 - 6.7), matching
// model/hpdp_model/framing.py, rs.py and channel.py.
//
// Byte streams between the blocks: tdata[7:0], first byte transmitted first;
// tlast and tuser are defined per interface.
package downlink_pkg;

    // Space packets (CCSDS 133.0)
    localparam int  PRIMARY_HDR   = 6;
    localparam int  SECONDARY_HDR = 8;
    localparam logic [10:0] IDLE_APID = 11'h7FF;
    localparam logic [7:0]  IDLE_FILL = 8'h55;

    // AOS frames (CCSDS 732.0), one frame per RS codeblock
    localparam int  RS_DEPTH    = 4;
    localparam int  FRAME_BYTES = 223 * RS_DEPTH;            // 892
    localparam int  FRAME_HDR   = 6;
    localparam int  MPDU_HDR    = 2;
    localparam int  ZONE_BYTES  = FRAME_BYTES - FRAME_HDR - MPDU_HDR;  // 884
    localparam logic [5:0]  IDLE_VCID = 6'd63;
    localparam logic [10:0] FHP_NONE  = 11'h7FF;

    // Channel coding (CCSDS 131.0)
    localparam int  CODEBLOCK_BYTES = 255 * RS_DEPTH;        // 1020
    localparam int  CADU_BYTES      = CODEBLOCK_BYTES + 4;   // 1024
    localparam logic [31:0] ASM = 32'h1ACF_FC1D;

    // ---------------------------------------------------------------------
    // Reed-Solomon (255,223), E = 16: GF(2^8) with x^8 + x^7 + x^2 + x + 1,
    // generator roots alpha^(11 j), j = 112..143 (coefficients from rs.py)
    // ---------------------------------------------------------------------
    localparam logic [7:0] RS_GEN [32] = '{
        8'h5B, 8'h7F, 8'h56, 8'h10, 8'h1E, 8'h0D, 8'hEB, 8'h61,
        8'hA5, 8'h08, 8'h2A, 8'h36, 8'h56, 8'hAB, 8'h20, 8'h71,
        8'h20, 8'hAB, 8'h56, 8'h36, 8'h2A, 8'h08, 8'hA5, 8'h61,
        8'hEB, 8'h0D, 8'h1E, 8'h10, 8'h56, 8'h7F, 8'h5B, 8'h01
    };

    // a * b in GF(2^8); with a constant operand synthesis reduces it to XORs
    function automatic logic [7:0] gf_mul(input logic [7:0] a, input logic [7:0] b);
        logic [7:0] p, x;
        p = '0;
        x = a;
        for (int k = 0; k < 8; k++) begin
            if (b[k]) p ^= x;
            x = x[7] ? ((x << 1) ^ 8'h87) : (x << 1);       // reduce by 0x187
        end
        return p;
    endfunction

    // Berlekamp dual basis <-> conventional basis (libfec ccsds_tal.c), as
    // linear maps: column k is the image of bit k
    localparam logic [7:0] TO_DUAL_COL   [8] = '{8'h7B, 8'hAF, 8'h99, 8'hFA, 8'h86, 8'hEC, 8'hEF, 8'h8D};
    localparam logic [7:0] FROM_DUAL_COL [8] = '{8'hCC, 8'hAC, 8'h79, 8'hF0, 8'hFD, 8'h2E, 8'h42, 8'hC5};

    function automatic logic [7:0] to_dual(input logic [7:0] x);
        logic [7:0] y = '0;
        for (int k = 0; k < 8; k++) if (x[k]) y ^= TO_DUAL_COL[k];
        return y;
    endfunction

    function automatic logic [7:0] from_dual(input logic [7:0] x);
        logic [7:0] y = '0;
        for (int k = 0; k < 8; k++) if (x[k]) y ^= FROM_DUAL_COL[k];
        return y;
    endfunction

    // ---------------------------------------------------------------------
    // Pseudo-randomizer h(x) = x^8 + x^7 + x^5 + x^3 + 1, all-ones seed:
    // 8-bit LFSR, output = MSB, feedback = parity of bits 7, 4, 2, 0.
    // pn_byte returns the next output byte; pn_next the state after it.
    // ---------------------------------------------------------------------
    function automatic logic [7:0] pn_byte(input logic [7:0] r);
        logic [7:0] s = r, out = '0;
        for (int k = 0; k < 8; k++) begin
            out = {out[6:0], s[7]};
            s   = {s[6:0], s[7] ^ s[4] ^ s[2] ^ s[0]};
        end
        return out;
    endfunction

    function automatic logic [7:0] pn_next(input logic [7:0] r);
        logic [7:0] s = r;
        for (int k = 0; k < 8; k++) s = {s[6:0], s[7] ^ s[4] ^ s[2] ^ s[0]};
        return s;
    endfunction

endpackage
