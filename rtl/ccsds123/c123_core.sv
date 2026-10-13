`timescale 1ns / 1ps

// CCSDS 123.0-B-2 compressor (SPEC.md 6.4), lossless, BIP order, one sample
// per clock.
//
//   s_axis (16-bit samples, tuser = first sample of a scene)
//     -> c123_neighborhood -> c123_localdiff -> c123_predictor -> c123_coder
//     -> codeword FIFO -> c123_packer -> m_axis (64-bit words, tlast per image)
//
// The pipeline advances as a whole on `ce`, which drops when the codeword FIFO
// is nearly full (output back-pressure, SPEC CMP-04); bubbles flow through it
// when no sample is offered. cfg must be stable while a scene is processed.
module c123_core import c123_pkg::*, ecc_pkg::*; #(
    parameter int NX_MAX = 512,
    parameter int NZ_MAX = 256
) (
    input  logic        clk,
    input  logic        rst,
    input  cfg_t        cfg,

    input  logic [15:0] s_axis_tdata,
    input  logic        s_axis_tuser,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    output logic [63:0] m_axis_tdata,
    output logic [7:0]  m_axis_tkeep,
    output logic        m_axis_tlast,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic        cfg_error,      // configuration outside what the core supports

    input  ecc_inj_t    lb_inj,         // line buffer ECC (SPEC RAD-02)
    output ecc_evt_t    lb_ecc,
    output logic        w_sbe,          // weight RAM ECC (RAD-03): corrected / uncorrectable read
    output logic        w_dbe,
    output logic        tmr_mismatch,   // per-band TMR memories: copies disagreed (RAD-03)
    output logic        fsm_mismatch    // the packer's voted state copies disagree (RAD-04)
);
    localparam int CW_DEPTH = 32;

    // ---------------------------------------------------------------------
    // Per-scene constants
    // ---------------------------------------------------------------------
    consts_t k;
    always_ff @(posedge clk) begin
        logic [15:0] smid, smax;
        smid = 16'd1 << (cfg.depth - 5'd1);
        smax = 16'((17'd1 << cfg.depth) - 17'd1);
        k.smid        <= smid;
        k.smax        <= smax;
        k.hr_offset   <= (RAWW'(smid) << (cfg.omega + 5'd2)) + (RAWW'(1) << (cfg.omega + 5'd1));
        k.hr_max      <= (RAWW'(smax) << (cfg.omega + 5'd2)) + (RAWW'(1) << (cfg.omega + 5'd1));
        k.w_init      <= WW'(7) << (cfg.omega - 5'd3);
        k.count_init  <= 11'd1 << cfg.gamma0;
        k.acc_init    <= (((32'd3 << (5'(cfg.k_init) + 5'd6)) - 32'd49) << cfg.gamma0) >> 7;
        k.count_limit <= (11'd1 << cfg.gamma_star) - 11'd1;
        cfg_error     <= cfg.nx == 16'd0 || cfg.nx > 16'(NX_MAX) || cfg.ny == 16'd0 ||
                         cfg.nz < 16'(NZ_MIN) || cfg.nz > 16'(NZ_MAX) ||
                         cfg.depth < 5'd2 || cfg.depth > 5'd16;
    end

    // ---------------------------------------------------------------------
    // Header (5.3): image, predictor and sample-adaptive entropy coder metadata
    // ---------------------------------------------------------------------
    logic [151:0] header;
    always_ff @(posedge clk) begin
        header <= {
            // image metadata (96 bits)
            cfg.user_data, cfg.nx, cfg.ny, cfg.nz,
            1'b0 /* unsigned */, 1'b0, 1'b0 /* D <= 16 */, cfg.depth[3:0],
            1'b0 /* band-interleaved */, cfg.nz /* sub-frame depth M = Nz */,
            2'b00, cfg.word_bytes[2:0],
            2'b00 /* sample-adaptive */, 1'b0, 2'b00 /* lossless */, 2'b00, 4'd0,
            // predictor metadata (40 bits)
            1'b0, 1'b0 /* no sample representative subpart */, cfg.bands, !cfg.full, 1'b0,
            cfg.local_sum, cfg.reg_size[5:0], 4'(cfg.omega - 5'd4), 4'(cfg.t_inc_log2 - 4'd4),
            4'(cfg.v_min + 5'sd6), 4'(cfg.v_max + 5'sd6), 1'b0, 1'b0, 1'b0, 5'd0,
            // entropy coder metadata (16 bits)
            cfg.u_max[4:0], 3'(cfg.gamma_star - 4'd4), cfg.gamma0[2:0], cfg.k_init, 1'b0
        };
    end

    // ---------------------------------------------------------------------
    // Pipeline
    // ---------------------------------------------------------------------
    logic ce;
    logic nb_mm, cd_mm;
    logic nb_valid, ld_valid, pr_valid, cw_valid_c;
    nb_t  nb;
    ld_t  ld;
    pr_t  pr;
    cw_t  cw_c;

    c123_neighborhood #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_nb (
        .clk, .rst, .ce, .cfg,
        .s_tdata  (s_axis_tdata),
        .s_tuser  (s_axis_tuser),
        .s_tvalid (s_axis_tvalid),
        .s_tready (s_axis_tready),
        .o_valid  (nb_valid),
        .o        (nb),
        .lb_inj, .lb_ecc,
        .tmr_mismatch (nb_mm)
    );

    c123_localdiff u_ld (
        .clk, .rst, .ce, .cfg,
        .smid    (k.smid),
        .i_valid (nb_valid),
        .i       (nb),
        .o_valid (ld_valid),
        .o       (ld)
    );

    c123_predictor #(.NZ_MAX(NZ_MAX)) u_pred (
        .clk, .rst, .ce, .cfg, .k,
        .i_valid (ld_valid),
        .i       (ld),
        .o_valid (pr_valid),
        .o       (pr),
        .w_sbe, .w_dbe
    );

    c123_coder #(.NZ_MAX(NZ_MAX)) u_coder (
        .clk, .rst, .ce, .cfg, .k,
        .i_valid (pr_valid),
        .i       (pr),
        .o_valid (cw_valid_c),
        .o       (cw_c),
        .tmr_mismatch (cd_mm)
    );
    assign tmr_mismatch = nb_mm || cd_mm;

    // ---------------------------------------------------------------------
    // Codeword FIFO and pipeline enable
    // ---------------------------------------------------------------------
    cw_t                     cw_q;
    logic                    cw_empty, cw_pop;
    logic [$clog2(CW_DEPTH):0] cw_count;

    sync_fifo #(.WIDTH($bits(cw_t)), .DEPTH(CW_DEPTH)) u_cw (
        .clk   (clk),
        .rst   (rst),
        .din   (cw_c),
        .wr_en (ce && cw_valid_c),
        .rd_en (cw_pop),
        .dout  (cw_q),
        .empty (cw_empty),
        .full  (),
        .count (cw_count)
    );

    // registered: at most two writes can land before ce takes effect
    always_ff @(posedge clk) begin
        if (rst) ce <= 1'b0;
        else     ce <= (cw_count <= CW_DEPTH - 3);
    end

    c123_packer u_pack (
        .clk, .rst, .cfg,
        .header   (header),
        .cw       (cw_q),
        .cw_valid (!cw_empty),
        .cw_pop   (cw_pop),
        .m_tdata  (m_axis_tdata),
        .m_tkeep  (m_axis_tkeep),
        .m_tlast  (m_axis_tlast),
        .m_tvalid (m_axis_tvalid),
        .m_tready (m_axis_tready),
        .tmr_mismatch (fsm_mismatch)
    );
endmodule
