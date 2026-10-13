`timescale 1ns / 1ps

// Accepts samples in BIP order and delivers each with its causal neighbours
// in the same band, 3 cycles later (S1 input register, S2, S3 output).
//
// BIP makes "same band, previous pixel" exactly Nz samples back, so:
//   W  = s(z, y, x-1)    per-band memory written with every sample
//   NE = s(z, y-1, x+1)  read from the previous-row line buffer at x*Nz+z+Nz
//   N  = s(z, y-1, x)    NE of the previous pixel (per-band memory); at x = 0
//                        it comes from a per-band copy of the row's first pixel
//   NW = s(z, y-1, x-1)  N of the previous pixel (per-band memory)
// The line buffer is read Nz samples before the same address is overwritten
// with the current row. It gathers four samples per ECC word (sample_ram,
// SPEC RAD-02) and writes the word when its last lane (or the row's last
// sample) arrives, so a read in the word being gathered still finds the
// previous row there. Per-band memories need Nz >= 3.
module c123_neighborhood import c123_pkg::*, ecc_pkg::*; #(
    parameter int NX_MAX = 512,
    parameter int NZ_MAX = 256
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        ce,
    input  cfg_t        cfg,

    input  logic [15:0] s_tdata,
    input  logic        s_tuser,        // first sample of a scene
    input  logic        s_tvalid,
    output logic        s_tready,

    output logic        o_valid,
    output nb_t         o,

    input  ecc_inj_t    lb_inj,         // line buffer ECC
    output ecc_evt_t    lb_ecc,
    output logic        tmr_mismatch    // per-band memories: copies disagreed on a read (RAD-03)
);
    localparam int ZB = $clog2(NZ_MAX);
    localparam int AB = $clog2(NX_MAX * NZ_MAX);

    // ---------------------------------------------------------------------
    // Position of the next sample (s_tuser restarts the scene)
    // ---------------------------------------------------------------------
    logic [15:0]   z_cnt, x_cnt, y_cnt;
    logic [31:0]   t_cnt;
    logic [AB-1:0] a_cnt;

    assign s_tready = ce;
    wire accept = ce && s_tvalid;

    wire [15:0]   z0 = s_tuser ? '0 : z_cnt;
    wire [15:0]   x0 = s_tuser ? '0 : x_cnt;
    wire [15:0]   y0 = s_tuser ? '0 : y_cnt;
    wire [31:0]   t0 = s_tuser ? '0 : t_cnt;
    wire [AB-1:0] a0 = s_tuser ? '0 : a_cnt;
    wire last_z = (z0 == cfg.nz - 16'd1);
    wire last_x = (x0 == cfg.nx - 16'd1);
    wire last_y = (y0 == cfg.ny - 16'd1);

    always_ff @(posedge clk) begin
        if (rst) begin
            {z_cnt, x_cnt, y_cnt, t_cnt, a_cnt} <= '0;
        end else if (accept) begin
            if (!last_z) begin
                z_cnt <= z0 + 16'd1;  x_cnt <= x0;  y_cnt <= y0;  t_cnt <= t0;  a_cnt <= a0 + 1'b1;
            end else if (!last_x) begin
                z_cnt <= '0;  x_cnt <= x0 + 16'd1;  y_cnt <= y0;  t_cnt <= t0 + 1;  a_cnt <= a0 + 1'b1;
            end else if (!last_y) begin
                z_cnt <= '0;  x_cnt <= '0;  y_cnt <= y0 + 16'd1;  t_cnt <= t0 + 1;  a_cnt <= '0;
            end else begin
                {z_cnt, x_cnt, y_cnt, t_cnt, a_cnt} <= '0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // S1: accepted sample
    // ---------------------------------------------------------------------
    logic          v1;
    logic [15:0]   s1, z1;
    logic [31:0]   tv1;
    logic [AB-1:0] a1;
    logic          fr1, fc1, lc1, sos1, eos1, eor1;

    always_ff @(posedge clk) begin
        if (rst) begin
            v1 <= 1'b0;
        end else if (ce) begin
            v1   <= s_tvalid;
            s1   <= s_tdata;
            z1   <= z0;
            tv1  <= t0;
            a1   <= a0;
            fr1  <= (y0 == 16'd0);
            fc1  <= (x0 == 16'd0);
            lc1  <= last_x;
            sos1 <= (t0 == 0) && (z0 == 16'd0);
            eos1 <= last_z && last_x && last_y;
            eor1 <= last_z && last_x;
        end
    end

    // Per-band memories (asynchronous read, written by valid samples), each in
    // three voted copies (RAD-03)
    wire [ZB-1:0] za1 = z1[ZB-1:0];
    logic [15:0]  w_rd, n_mem, rs_rd, nw_rd, n_rd, ne_rd;
    logic [3:0]   mm;

    tmr_ram #(.WIDTH (16), .DEPTH (NZ_MAX)) u_mem_w (
        .clk, .we (ce && v1), .waddr (za1), .wdata (s1), .raddr (za1), .rdata (w_rd), .mismatch (mm[0]));
    tmr_ram #(.WIDTH (16), .DEPTH (NZ_MAX)) u_mem_nw (
        .clk, .we (ce && v1), .waddr (za1), .wdata (n_rd), .raddr (za1), .rdata (nw_rd), .mismatch (mm[1]));
    tmr_ram #(.WIDTH (16), .DEPTH (NZ_MAX)) u_mem_rs (
        .clk, .we (ce && v1 && fc1), .waddr (za1), .wdata (s1), .raddr (za1), .rdata (rs_rd), .mismatch (mm[2]));
    // (u_mem_n, written from S3, is at the end)

    assign n_rd         = fc1 ? rs_rd : n_mem;
    assign tmr_mismatch = ce && v1 && (mm[0] || mm[1] || (fc1 ? mm[2] : mm[3]));

    logic [15:0] last_w, last_s;        // previous valid sample: W and s

    always_ff @(posedge clk) begin
        if (ce && v1) begin
            last_w <= w_rd;
            last_s <= s1;
        end
    end

    // Previous-row line buffer: write s at x*Nz+z, read NE at x*Nz+z+Nz (ne_rd
    // valid in S3)

    sample_ram #(
        .DEPTH (NX_MAX * NZ_MAX),
        .STYLE (NX_MAX * NZ_MAX >= 65536 ? "ultra" : "block")
    ) u_line (
        .clk   (clk),
        .rst   (rst),
        .ce    (ce),
        .we    (ce && v1),
        .wlast (eor1),
        .waddr (a1),
        .wdata (s1),
        .re    (v1),
        .raddr (a1 + AB'(cfg.nz)),
        .rdata (ne_rd),
        .inj   (lb_inj),
        .ecc   (lb_ecc)
    );

    // rho(t) = clip(v_min + floor((t - Nx) / t_inc), v_min, v_max) + D - omega
    wire signed [33:0] t_rel  = $signed({2'b00, tv1}) - $signed({18'd0, cfg.nx});
    wire signed [33:0] t_step = t_rel >>> cfg.t_inc_log2;
    wire signed [5:0]  v_span = cfg.v_max - cfg.v_min;
    wire signed [5:0]  v_now  = (t_step < 0)      ? 6'(cfg.v_min) :
                                (t_step > v_span) ? 6'(cfg.v_max) :
                                                    6'(cfg.v_min) + 6'(t_step);
    wire signed [5:0]  rho1   = v_now + $signed({1'b0, cfg.depth}) - $signed({1'b0, cfg.omega});

    // ---------------------------------------------------------------------
    // S2, S3
    // ---------------------------------------------------------------------
    logic v2;
    nb_t  r2, r3;

    always_ff @(posedge clk) begin
        if (rst) begin
            v2      <= 1'b0;
            o_valid <= 1'b0;
        end else if (ce) begin
            v2           <= v1;
            r2.s         <= s1;
            r2.n         <= n_rd;
            r2.ne        <= '0;             // replaced by the line buffer output in S3
            r2.nw        <= nw_rd;
            r2.w         <= w_rd;
            r2.w_prev    <= last_w;
            r2.s_prev    <= last_s;
            r2.z         <= z1;
            r2.first_row <= fr1;
            r2.first_col <= fc1;
            r2.last_col  <= lc1;
            r2.t0        <= (tv1 == 0);
            r2.t1        <= (tv1 == 1);
            r2.sos       <= sos1;
            r2.eos       <= eos1;
            r2.rho       <= rho1;

            o_valid <= v2;
            r3      <= r2;
        end
    end

    always_comb begin
        o    = r3;
        o.ne = ne_rd;
    end

    // N of the next row's pixel x+1 is this sample's NE
    tmr_ram #(.WIDTH (16), .DEPTH (NZ_MAX)) u_mem_n (
        .clk, .we (ce && o_valid), .waddr (r3.z[ZB-1:0]), .wdata (ne_rd), .raddr (za1), .rdata (n_mem),
        .mismatch (mm[3]));
endmodule
