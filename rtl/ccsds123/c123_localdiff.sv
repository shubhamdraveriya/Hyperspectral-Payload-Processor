`timescale 1ns / 1ps

// Local sum (4.4) and local difference vector (4.5) in two stages:
//   S4: sigma
//   S5: directional differences (full mode), and the central differences of
//       the previous min(z, P) bands of the same pixel, which in BIP order are
//       the samples just processed (shift register).
// Lossless coding: the sample representatives used by 4.4/4.5 equal the samples.
module c123_localdiff import c123_pkg::*; (
    input  logic        clk,
    input  logic        rst,
    input  logic        ce,
    input  cfg_t        cfg,
    input  logic [15:0] smid,

    input  logic        i_valid,
    input  nb_t         i,

    output logic        o_valid,
    output ld_t         o
);
    // ---------------------------------------------------------------------
    // S4: local sum
    // ---------------------------------------------------------------------
    logic [17:0] sigma_c;

    always_comb begin
        logic [17:0] n, ne, nw, w, wp, m;
        n  = {2'b00, i.n};
        ne = {2'b00, i.ne};
        nw = {2'b00, i.nw};
        w  = {2'b00, i.w};
        wp = {2'b00, i.w_prev};
        m  = {2'b00, smid};
        if (cfg.local_sum[1] && !i.first_row) begin          // column-oriented, y > 0
            sigma_c = n << 2;
        end else if (i.first_row) begin                     // y = 0 (x > 0)
            if (!cfg.local_sum[0]) sigma_c = w << 2;         // wide
            else                   sigma_c = (i.z != 16'd0) ? wp << 2 : m << 2;
        end else if (i.first_col) begin                     // neighbour-oriented, x = 0
            sigma_c = (n + ne) << 1;
        end else if (i.last_col) begin                      // x = Nx - 1
            sigma_c = !cfg.local_sum[0] ? w + nw + (n << 1) : (nw + n) << 1;
        end else begin
            sigma_c = !cfg.local_sum[0] ? w + nw + n + ne : nw + (n << 1) + ne;
        end
    end

    logic        v4;
    nb_t         r4;
    logic [17:0] sigma4;

    always_ff @(posedge clk) begin
        if (rst) begin
            v4 <= 1'b0;
        end else if (ce) begin
            v4     <= i_valid;
            r4     <= i;
            sigma4 <= sigma_c;
        end
    end

    // ---------------------------------------------------------------------
    // S5: local differences
    // ---------------------------------------------------------------------
    wire signed [DW-1:0] sig = $signed({1'b0, sigma4});
    wire signed [DW-1:0] dc  = $signed({1'b0, r4.s, 2'b00}) - sig;          // central
    wire signed [DW-1:0] dnn = $signed({1'b0, r4.n, 2'b00}) - sig;
    wire signed [DW-1:0] dww = $signed({1'b0, r4.w, 2'b00}) - sig;
    wire signed [DW-1:0] dnw = $signed({1'b0, r4.nw, 2'b00}) - sig;

    // number of previous bands used: min(z, P)
    wire [3:0] pz = (r4.z < {12'd0, cfg.bands}) ? r4.z[3:0] : cfg.bands;

    logic [MAX_P-1:0][DW-1:0] dprev;    // dprev[0]: band z-1 of this pixel

    always_ff @(posedge clk) begin
        if (rst) begin
            o_valid <= 1'b0;
        end else if (ce) begin
            o_valid  <= v4;
            o.sigma  <= sigma4;
            o.s      <= r4.s;
            o.s_prev <= r4.s_prev;
            o.z      <= r4.z;
            o.t0     <= r4.t0;
            o.t1     <= r4.t1;
            o.sos    <= r4.sos;
            o.eos    <= r4.eos;
            o.rho    <= r4.rho;

            if (cfg.full && !r4.first_row) begin
                o.u[0] <= dnn;
                o.u[1] <= r4.first_col ? dnn : dww;
                o.u[2] <= r4.first_col ? dnn : dnw;
            end else begin
                o.u[0] <= '0;
                o.u[1] <= '0;
                o.u[2] <= '0;
            end
            for (int j = 0; j < MAX_P; j++)
                o.u[3 + j] <= (j < pz) ? dprev[j] : '0;

            if (v4) begin
                dprev[0] <= dc;
                for (int j = 1; j < MAX_P; j++) dprev[j] <= dprev[j - 1];
            end
        end
    end
endmodule
