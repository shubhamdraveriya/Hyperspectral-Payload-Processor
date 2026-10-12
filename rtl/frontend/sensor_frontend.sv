`timescale 1ns / 1ps

// Sensor front end (SPEC.md 6.1 - 6.3):
//
//   DMA (128-bit raw BIL samples) -> sensor_emulator -> radiometric_corr
//     -> corner_turn -> m_axis (16-bit BIP samples, to the compressor)
//
// Only the corner turn output can be back-pressured; everything before it runs
// at the sensor's pace, and overflows are counted (SYS-05). The configuration
// comes in once per clock domain (the same values, synchronized by the caller).
module sensor_frontend import frontend_pkg::*; #(
    parameter int NX_MAX = 512,
    parameter int NZ_MAX = 256
) (
    input  logic         clk_sys,
    input  logic         rst_sys,
    input  fe_cfg_t      cfg_sys,
    input  logic         cnt_clr_sys,       // clear the clk_sys counters
    input  logic         clk_sensor,
    input  logic         rst_sensor,
    input  fe_cfg_t      cfg_sensor,
    input  logic         cnt_clr_sensor,    // clear the clk_sensor counters

    input  logic [127:0] s_dma_tdata,       // raw samples, 8 per beat, sample 0 in [15:0]
    input  logic         s_dma_tvalid,
    output logic         s_dma_tready,

    input  logic [127:0] s_tbl_tdata,       // calibration records, 2 per beat
    input  logic         s_tbl_tlast,
    input  logic         s_tbl_tvalid,
    output logic         s_tbl_tready,

    input  tbl_cmd_t     tbl_cmd,           // calibration table debug access (clk_sys)
    output logic [32:0]  tbl_rdata,
    output logic         tbl_done,

    output logic [15:0]  m_axis_tdata,
    output logic [1:0]   m_axis_tuser,      // [0] first sample of a scene, [1] first of a line
    output logic         m_axis_tlast,      // last sample of a line
    output logic         m_axis_tvalid,
    input  logic         m_axis_tready,

    output se_status_t   se_status,         // clk_sensor domain
    output fe_status_t   fe_status,         // clk_sys domain

    // A scene's first sample left the emulator (clk_sys pulse), with its
    // timestamp: the number of lines before it, i.e. the emulator's
    // scene_timestamp (SE-05) for this scene (both count from reset)
    output logic         scene_start,
    output logic [23:0]  scene_ts
);
    logic        se_valid, se_last, rc_valid, rc_last;
    logic [15:0] se_data, rc_data;
    logic [1:0]  se_user, rc_user;

    logic [23:0] lines_seen;
    always_ff @(posedge clk_sys) begin
        if (rst_sys)
            lines_seen <= '0;
        else if (se_valid && se_user[1])
            lines_seen <= lines_seen + 24'd1;
    end
    assign scene_start = se_valid && se_user[0];
    assign scene_ts    = lines_seen;

    sensor_emulator u_se (
        .clk_sys, .rst_sys,
        .s_axis_tdata  (s_dma_tdata),
        .s_axis_tvalid (s_dma_tvalid),
        .s_axis_tready (s_dma_tready),
        .m_valid       (se_valid),
        .m_data        (se_data),
        .m_user        (se_user),
        .m_last        (se_last),
        .clk_sensor, .rst_sensor,
        .cfg           (cfg_sensor),
        .cnt_clr       (cnt_clr_sensor),
        .status        (se_status)
    );

    radiometric_corr #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_rc (
        .clk            (clk_sys),
        .rst            (rst_sys),
        .cfg            (cfg_sys),
        .s_tbl_tdata, .s_tbl_tlast, .s_tbl_tvalid, .s_tbl_tready,
        .s_valid        (se_valid),
        .s_data         (se_data),
        .s_user         (se_user),
        .s_last         (se_last),
        .m_valid        (rc_valid),
        .m_data         (rc_data),
        .m_user         (rc_user),
        .m_last         (rc_last),
        .tbl_cmd, .tbl_rdata, .tbl_done,
        .scene_clamped  (fe_status.scene_clamped),
        .scene_replaced (fe_status.scene_replaced),
        .tbl_words      (fe_status.tbl_words)
    );

    corner_turn #(.NX_MAX(NX_MAX), .NZ_MAX(NZ_MAX)) u_ct (
        .clk            (clk_sys),
        .rst            (rst_sys),
        .cfg            (cfg_sys),
        .s_valid        (rc_valid),
        .s_data         (rc_data),
        .s_user         (rc_user),
        .s_last         (rc_last),
        .m_axis_tdata, .m_axis_tuser, .m_axis_tlast, .m_axis_tvalid, .m_axis_tready,
        .cnt_clr        (cnt_clr_sys),
        .lines_out      (fe_status.lines_out),
        .dropped_lines  (fe_status.dropped_lines),
        .unfilled_lines (fe_status.unfilled_lines)
    );
endmodule
