`timescale 1ns / 1ps

// Error injector on the CADU byte stream (SPEC.md 6.8), standing in for an RF
// channel; bit-exact with channel.inject_hw().
//
//   mode 0  off
//   mode 1  random: bit b of each byte (b = 7 first on the wire) is flipped when
//           lane 7-b of eight xorshift32 generators is below `threshold`
//           (BER = threshold / 2^32); every lane steps once per byte
//   mode 2  burst: burst_len consecutive bits, from the first bit of every
//           `period`-th byte
//   mode 3  ASM: the first asm_bits bits of the sync marker of every
//           `period`-th CADU (tuser marks a CADU's first byte)
//
// seed_load (or reset) restarts the generators from `seed` and the byte and
// CADU counters. One byte per cycle; counts the flipped bits (saturating).
module error_injector (
    input  logic        clk,
    input  logic        rst,
    input  logic [1:0]  mode,
    input  logic [31:0] threshold,
    input  logic [31:0] seed,
    input  logic        seed_load,
    input  logic [15:0] burst_len,
    input  logic [31:0] period,                 // >= 1
    input  logic [5:0]  asm_bits,               // 0..32
    input  logic        cnt_clr,                // clear injected_bits

    input  logic [7:0]  s_axis_tdata,
    input  logic        s_axis_tuser,
    input  logic        s_axis_tlast,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    output logic [7:0]  m_axis_tdata,
    output logic        m_axis_tuser,
    output logic        m_axis_tlast,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic [31:0] injected_bits
);
    localparam logic [31:0] GOLDEN = 32'h9E37_79B9;

    function automatic logic [31:0] xorshift(input logic [31:0] x);
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        return x;
    endfunction

    function automatic logic [31:0] lane_seed(input logic [31:0] s, input int k);
        logic [31:0] v;
        v = s ^ (GOLDEN * 32'(k + 1));
        return (v == 32'd0) ? 32'd1 : v;
    endfunction

    logic [31:0] rng [8];
    logic [31:0] bc;                            // byte counter (burst)
    logic [31:0] cc;                            // CADU counter (ASM)
    logic [1:0]  ai;                            // byte within the ASM
    logic        in_asm;
    logic [15:0] burst_left;

    wire advance = !m_axis_tvalid || m_axis_tready;
    wire take    = advance && s_axis_tvalid;
    assign s_axis_tready = advance;

    // flip mask of the byte being taken
    logic [7:0]  mask;
    logic [15:0] burst_after;
    always_comb begin
        logic [15:0] bl;
        logic [5:0]  n;
        mask        = '0;
        burst_after = burst_left;
        case (mode)
            2'd1: for (int k = 0; k < 8; k++) mask[7 - k] = (rng[k] < threshold);
            2'd2: begin
                bl = (bc == 32'd0) ? burst_len : burst_left;
                n  = (bl >= 16'd8) ? 6'd8 : 6'(bl);
                mask        = ~(8'hFF >> n);
                burst_after = bl - 16'(n);
            end
            2'd3: begin
                // byte ai of the marker of a hit CADU: bits 8*ai .. 8*ai+7
                if ((s_axis_tuser && cc == 32'd0) || (in_asm && !s_axis_tuser)) begin
                    logic [5:0] a, first;
                    a     = s_axis_tuser ? 6'd0 : 6'({ai, 3'b000});
                    first = (asm_bits > a) ? asm_bits - a : 6'd0;
                    mask  = ~(8'hFF >> ((first >= 6'd8) ? 6'd8 : first));
                end
            end
            default: ;
        endcase
    end

    logic [3:0] flips;
    always_comb begin
        flips = '0;
        for (int b = 0; b < 8; b++) flips += 4'(mask[b]);
    end

    always_ff @(posedge clk) begin
        if (rst || seed_load) begin
            for (int k = 0; k < 8; k++) rng[k] <= lane_seed(seed, k);
            bc            <= '0;
            cc            <= '0;
            ai            <= '0;
            in_asm        <= 1'b0;
            burst_left    <= '0;
            m_axis_tvalid <= 1'b0;
            injected_bits <= '0;
        end else begin
            if (advance) m_axis_tvalid <= 1'b0;
            if (take) begin
                m_axis_tvalid <= 1'b1;
                m_axis_tdata  <= s_axis_tdata ^ mask;
                m_axis_tuser  <= s_axis_tuser;
                m_axis_tlast  <= s_axis_tlast;
                for (int k = 0; k < 8; k++) rng[k] <= xorshift(rng[k]);
                bc         <= (bc + 32'd1 >= period) ? 32'd0 : bc + 32'd1;
                burst_left <= burst_after;
                if (s_axis_tuser) begin
                    in_asm <= (cc == 32'd0);
                    ai     <= 2'd1;
                    cc     <= (cc + 32'd1 >= period) ? 32'd0 : cc + 32'd1;
                end else if (in_asm) begin
                    ai <= ai + 2'd1;
                    if (ai == 2'd3) in_asm <= 1'b0;
                end
                injected_bits <= (injected_bits > 32'hFFFF_FFF7) ? 32'hFFFF_FFFF
                                                                  : injected_bits + 32'(flips);
            end
            if (cnt_clr) injected_bits <= '0;
        end
    end
endmodule
