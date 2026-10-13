`timescale 1ns / 1ps

// Sample-adaptive entropy coder (5.4.3.2).
//   C0: per-band counter / accumulator (initial values at t = 1)
//   C1: threshold Sigma + floor(49 Gamma / 2^7); counter / accumulator update
//   C2: code parameter k
//   C3: codeword -> output
// The first sample of each band (t = 0) is sent as D raw bits. In raw mode
// every sample is sent as D raw bits.
module c123_coder import c123_pkg::*; #(
    parameter int NZ_MAX = 256
) (
    input  logic    clk,
    input  logic    rst,
    input  logic    ce,
    input  cfg_t    cfg,
    input  consts_t k,

    input  logic    i_valid,
    input  pr_t     i,

    output logic    o_valid,
    output cw_t     o,
    output logic    tmr_mismatch        // the copies of the state disagreed on a read (RAD-03)
);
    localparam int ZB = $clog2(NZ_MAX);

    // per band: {accumulator, counter}, in three voted copies (RAD-03)
    logic [42:0] st_rd, st_next;
    logic        v1, t0_1;
    logic [15:0] z1;
    logic        st_mm;

    tmr_ram #(.WIDTH (43), .DEPTH (NZ_MAX)) u_state (
        .clk      (clk),
        .we       (ce && v1 && !t0_1),
        .waddr    (z1[ZB-1:0]),
        .wdata    (st_next),
        .raddr    (i.z[ZB-1:0]),
        .rdata    (st_rd),
        .mismatch (st_mm)
    );
    assign tmr_mismatch = st_mm && ce && i_valid && !i.t1;

    // C0
    wire  [31:0] acc0  = i.t1 ? k.acc_init   : st_rd[42:11];
    wire  [10:0] cnt0  = i.t1 ? k.count_init : st_rd[10:0];

    logic        sos1, eos1;
    logic [15:0] d1, s1;
    logic [31:0] acc1;
    logic [10:0] cnt1;

    always_ff @(posedge clk) begin
        if (rst) begin
            v1 <= 1'b0;
        end else if (ce) begin
            v1 <= i_valid;  d1 <= i.delta;  s1 <= i.s;  z1 <= i.z;
            t0_1 <= i.t0;  sos1 <= i.sos;  eos1 <= i.eos;
            acc1 <= acc0;  cnt1 <= cnt0;
        end
    end

    // C1: threshold and state update
    wire [31:0] thr1 = acc1 + ((32'(cnt1) * 32'd49) >> 7);
    always_comb begin
        if (cnt1 < k.count_limit) st_next = {acc1 + 32'(d1), cnt1 + 11'd1};
        else                      st_next = {(acc1 + 32'(d1) + 32'd1) >> 1, (cnt1 + 11'd1) >> 1};
    end



    logic        v2, t0_2, sos2, eos2;
    logic [15:0] d2, s2;
    logic [31:0] thr2;
    logic [10:0] cnt2;

    always_ff @(posedge clk) begin
        if (rst) begin
            v2 <= 1'b0;
        end else if (ce) begin
            v2 <= v1;  d2 <= d1;  s2 <= s1;  t0_2 <= t0_1;  sos2 <= sos1;  eos2 <= eos1;
            thr2 <= thr1;  cnt2 <= cnt1;
        end
    end

    // C2: k = largest k <= D-2 with Gamma 2^k <= threshold; 0 if 2 Gamma > threshold
    logic [3:0] k_c;
    always_comb begin
        k_c = 4'd0;
        for (int j = 1; j <= 14; j++)
            if (j <= int'(cfg.depth) - 2 && ({21'd0, cnt2} << j) <= thr2) k_c = 4'(j);
    end

    logic        v3, t0_3, sos3, eos3;
    logic [15:0] d3, s3;
    logic [3:0]  k3;

    always_ff @(posedge clk) begin
        if (rst) begin
            v3 <= 1'b0;
        end else if (ce) begin
            v3 <= v2;  d3 <= d2;  s3 <= s2;  t0_3 <= t0_2;  sos3 <= sos2;  eos3 <= eos2;
            k3 <= k_c;
        end
    end

    // C3: length-limited Golomb power-of-2 codeword
    wire [15:0] u3 = d3 >> k3;

    always_ff @(posedge clk) begin
        if (rst) begin
            o_valid <= 1'b0;
        end else if (ce) begin
            o_valid <= v3;
            o.sos   <= sos3;
            o.eos   <= eos3;
            if (cfg.raw) begin
                o.len <= 6'(cfg.depth);
                o.val <= CWW'(s3);
            end else if (t0_3) begin
                o.len <= 6'(cfg.depth);
                o.val <= CWW'(d3);
            end else if (u3 < 16'(cfg.u_max)) begin
                o.len <= 6'(u3) + 6'd1 + 6'(k3);
                o.val <= (CWW'(1) << k3) | (CWW'(d3) & ((CWW'(1) << k3) - 1));
            end else begin
                o.len <= cfg.u_max + 6'(cfg.depth);
                o.val <= CWW'(d3);
            end
        end
    end
endmodule
