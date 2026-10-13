`timescale 1ns / 1ps

// Adaptive prediction (4.6 - 4.7), mapping (4.11) and weight update (4.10).
//
//   in  : weight RAM read for band z
//   P1  : delay
//   P2  : weights (default initialization at t = 1)
//   P3  : multiplier operands, 2^omega (sigma - 4 smid)
//   P4-5: products (DSP M and P registers)
//   P6-8: adder tree 18 -> 6 -> 2 -> 1                  (d-hat)
//   P9  : d-hat + 2^omega (sigma - 4 smid)
//   P10 : wrap to R bits (mod*_R)
//   P11 : + 2^(omega+2) smid + 2^(omega+1)
//   P12 : clip, double-resolution prediction (t = 0 special case)
//   P13 : predicted sample, residual, mapped index         -> output
//   P14 : weight increments
//   P15 : updated weights -> weight RAM
// The read for band z happens 15 cycles before its write-back; the next read
// of band z comes Nz samples later, hence NZ_MIN = 16.
module c123_predictor import c123_pkg::*; #(
    parameter int NZ_MAX = 256
) (
    input  logic    clk,
    input  logic    rst,
    input  logic    ce,
    input  cfg_t    cfg,
    input  consts_t k,

    input  logic    i_valid,
    input  ld_t     i,

    output logic    o_valid,
    output pr_t     o,
    output logic    w_sbe,              // weight RAM read corrected / uncorrectable (RAD-03)
    output logic    w_dbe
);
    localparam int ZB = $clog2(NZ_MAX);

    typedef logic [NWS-1:0][WW-1:0] wvec_t;

    // Pipeline of the sample's own fields
    typedef struct packed {
        logic                   v;
        logic [NWS-1:0][DW-1:0] u;
        logic [15:0]            s, s_prev, z;
        logic                   t0, t1, sos, eos;
        logic signed [5:0]      rho;
    } ctl_t;

    ctl_t c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14, c15;

    function automatic ctl_t ctl_of(logic v, ld_t x);
        ctl_t r;
        r.v = v;  r.u = x.u;  r.s = x.s;  r.s_prev = x.s_prev;  r.z = x.z;
        r.t0 = x.t0;  r.t1 = x.t1;  r.sos = x.sos;  r.eos = x.eos;  r.rho = x.rho;
        return r;
    endfunction

    // ---------------------------------------------------------------------
    // Weight RAM: block RAM with ECC (RAD-03), the weights padded to a
    // multiple of 64 bits. Each band's weights are rewritten after every use,
    // so a corrected word goes back corrected.
    // ---------------------------------------------------------------------
    localparam int WBITS = NWS * WW;
    localparam int WPAD  = (WBITS + 63) / 64 * 64;

    wvec_t w_rd, w_new15;
    logic [WPAD-1:0] w_q;

    ecc_ram #(
        .WIDTH   (WPAD),
        .DEPTH   (NZ_MAX),
        .STYLE   ("block"),
        .LATENCY (2)
    ) u_weights (
        .clk     (clk),
        .rst     (rst),
        .ce      (ce),
        .we      (ce && c15.v && !c15.t0),
        .waddr   (c15.z[ZB-1:0]),
        .wdata   (WPAD'(w_new15)),
        .inj_sbe (1'b0),
        .inj_dbe (1'b0),
        .re      (i_valid),
        .raddr   (i.z[ZB-1:0]),
        .rdata   (w_q),
        .sbe     (w_sbe),
        .dbe     (w_dbe)
    );
    assign w_rd = w_q[WBITS-1:0];

    // Default initialization (4.6.3.2): directional 0, first band 7/8 2^omega,
    // each further band 1/8 of the previous. Slots beyond min(z, P) are unused:
    // their local differences are zero.
    wvec_t w_default;
    always_comb begin
        w_default = '0;
        for (int j = 0; j < MAX_P; j++)
            w_default[3 + j] = k.w_init >> (3 * j);
    end

    // sigma travels with the sample: capture it at P1/P2
    logic [17:0] i_sigma1, i_sigma2;
    always_ff @(posedge clk) begin
        if (ce) begin
            i_sigma1 <= i.sigma;
            i_sigma2 <= i_sigma1;
        end
    end

    // ---------------------------------------------------------------------
    // P1 - P5: weights, products
    // ---------------------------------------------------------------------
    wvec_t w3, w4, w5, w6, w7, w8, w9, w10, w11, w12, w13, w14;
    logic signed [RAWW-1:0] sig_term3, sig_term4, sig_term5, sig_term6, sig_term7, sig_term8;
    logic signed [PRW-1:0]  prod4 [NWS];
    logic signed [PRW-1:0]  prod5 [NWS];
    logic signed [SUMW-1:0] sum6 [6];
    logic signed [SUMW-1:0] sum7 [2];
    logic signed [SUMW-1:0] dhat8;
    logic signed [RAWW-1:0] raw9, wrap10, hr11;
    logic [16:0]            dbl12;

    always_ff @(posedge clk) begin
        if (rst) begin
            {c1.v, c2.v, c3.v, c4.v, c5.v, c6.v, c7.v, c8.v} <= '0;
        end else if (ce) begin
            c1 <= ctl_of(i_valid, i);
            c2 <= c1;
            c3 <= c2;
            c4 <= c3;
            c5 <= c4;
            c6 <= c5;
            c7 <= c6;
            c8 <= c7;
        end
    end

    always_ff @(posedge clk) begin
        if (ce) begin
            // P3: operands
            w3 <= c2.t1 ? w_default : w_rd;
            sig_term3 <= ($signed({{(RAWW-18){1'b0}}, i_sigma2}) - $signed({{(RAWW-18){1'b0}}, k.smid, 2'b00}))
                         <<< cfg.omega;
            // P4, P5: products
            for (int j = 0; j < NWS; j++) begin
                prod4[j] <= $signed(w3[j]) * $signed(c3.u[j]);
                prod5[j] <= prod4[j];
            end
            w4 <= w3;  w5 <= w4;  w6 <= w5;  w7 <= w6;  w8 <= w7;
            sig_term4 <= sig_term3;  sig_term5 <= sig_term4;  sig_term6 <= sig_term5;
            sig_term7 <= sig_term6;  sig_term8 <= sig_term7;
            // P6 - P8: adder tree
            for (int j = 0; j < 6; j++)
                sum6[j] <= SUMW'(prod5[3 * j]) + SUMW'(prod5[3 * j + 1]) + SUMW'(prod5[3 * j + 2]);
            sum7[0] <= sum6[0] + sum6[1] + sum6[2];
            sum7[1] <= sum6[3] + sum6[4] + sum6[5];
            dhat8   <= sum7[0] + sum7[1];
        end
    end

    // ---------------------------------------------------------------------
    // P9 - P12: prediction
    // ---------------------------------------------------------------------
    // mod*_R: keep the R low bits as a signed number (R >= 48 leaves it unchanged)
    function automatic logic signed [RAWW-1:0] wrap_r(logic signed [RAWW-1:0] x, logic [6:0] r);
        logic [5:0] sh;
        if (r >= RAWW) return x;
        sh = 6'(RAWW - r);
        return (x <<< sh) >>> sh;
    endfunction

    wire [5:0] dbl_shift = 6'(cfg.omega) + 6'd1;

    always_ff @(posedge clk) begin
        if (rst) begin
            {c9.v, c10.v, c11.v, c12.v, c13.v, c14.v, c15.v} <= '0;
        end else if (ce) begin
            c9 <= c8;  c10 <= c9;  c11 <= c10;  c12 <= c11;  c13 <= c12;  c14 <= c13;  c15 <= c14;
        end
    end

    always_ff @(posedge clk) begin
        if (ce) begin
            raw9   <= RAWW'(dhat8) + sig_term8;
            wrap10 <= wrap_r(raw9, cfg.reg_size);
            hr11   <= wrap10 + $signed(k.hr_offset);
            if (c11.t0)
                dbl12 <= (cfg.bands != 4'd0 && c11.z != 16'd0) ? {c11.s_prev, 1'b0} : {k.smid, 1'b0};
            else if (hr11 < 0)
                dbl12 <= '0;
            else if (hr11 > $signed(k.hr_max))
                dbl12 <= 17'(k.hr_max >> dbl_shift);
            else
                dbl12 <= 17'(hr11 >>> dbl_shift);
            w9 <= w8;  w10 <= w9;  w11 <= w10;  w12 <= w11;  w13 <= w12;  w14 <= w13;
        end
    end

    // ---------------------------------------------------------------------
    // P13: mapped prediction residual
    // ---------------------------------------------------------------------
    wire [15:0]        s_hat  = dbl12[16:1];
    wire signed [16:0] resid  = $signed({1'b0, c12.s}) - $signed({1'b0, s_hat});
    wire [15:0]        theta  = (s_hat < k.smax - s_hat) ? s_hat : k.smax - s_hat;
    wire [16:0]        aq     = resid[16] ? 17'(-resid) : 17'(resid);
    wire signed [16:0] signed_q = dbl12[0] ? -resid : resid;
    wire [16:0]        delta_c = (aq > {1'b0, theta}) ? aq + {1'b0, theta} :
                                 (!signed_q[16])      ? aq << 1 : (aq << 1) - 17'd1;
    // scaled prediction error sign: e = 2 s' - double-resolution prediction
    wire               e_pos  = ({c12.s, 1'b0} >= dbl12);

    logic sgn13;
    always_ff @(posedge clk) begin
        if (rst) begin
            o_valid <= 1'b0;
        end else if (ce) begin
            o_valid  <= c12.v;
            o.delta  <= delta_c[15:0];
            o.s      <= c12.s;
            o.z      <= c12.z;
            o.t0     <= c12.t0;
            o.t1     <= c12.t1;
            o.sos    <= c12.sos;
            o.eos    <= c12.eos;
            sgn13    <= e_pos;
        end
    end

    // ---------------------------------------------------------------------
    // P14 - P15: weight update
    //   w += floor((sgn+(e) 2^-rho u + 1) / 2), clipped to [-2^(omega+2), 2^(omega+2) - 1]
    // ---------------------------------------------------------------------
    localparam int IW = DW + 25;         // u << 23 plus sign and rounding room

    function automatic logic signed [IW-1:0] increment(logic signed [DW-1:0] u, logic pos,
                                                       logic signed [5:0] rho);
        logic signed [IW-1:0] v;
        v = pos ? IW'(u) : -IW'(u);
        if (rho >= 0)
            return (v + (IW'(1) <<< rho)) >>> (rho + 1);
        return ((v <<< (-rho)) + IW'(1)) >>> 1;
    endfunction

    logic signed [IW-1:0] inc14 [NWS];
    wire signed [WW+1:0]  w_lo = -(WW+2)'(1) <<< (cfg.omega + 2);
    wire signed [WW+1:0]  w_hi = ((WW+2)'(1) <<< (cfg.omega + 2)) - 1;

    always_ff @(posedge clk) begin
        if (ce) begin
            for (int j = 0; j < NWS; j++)
                inc14[j] <= increment($signed(c13.u[j]), sgn13, c13.rho);
            for (int j = 0; j < NWS; j++) begin
                logic signed [IW-1:0] sum;
                sum = IW'($signed(w14[j])) + inc14[j];
                if (sum < w_lo)      w_new15[j] <= WW'(w_lo);
                else if (sum > w_hi) w_new15[j] <= WW'(w_hi);
                else                 w_new15[j] <= WW'(sum);
            end
        end
    end
endmodule
