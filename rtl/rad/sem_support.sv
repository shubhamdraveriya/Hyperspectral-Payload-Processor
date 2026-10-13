`timescale 1ns / 1ps

// Soft Error Mitigation controller with its configuration primitives (RAD-01,
// PG187), as the IP's example support layer: sem_ip (sem_ultra, mitigation and
// testing), ICAPE3 and FRAME_ECCE4. Runs on clk (clk_fr, 31.25 MHz). The ICAP
// is granted to the controller by `grant` (software, after PCAP_PR is cleared);
// nothing else uses the ICAP, so it is never asked back. The monitor interface
// (status reports) is passed on byte by byte and never back-pressures the
// controller; nothing is sent to it (commands come through the command port).
//
// status: [0] heartbeat, [1] initialization, [2] observation, [3] correction,
// [4] classification, [5] injection, [6] diagnostic scan, [7] detect only,
// [8] essential, [9] uncorrectable.
module sem_support (
    input  logic        clk,
    input  logic        grant,
    output logic [9:0]  status,
    input  logic        cmd_strobe,
    input  logic [43:0] cmd_code,
    output logic        cmd_busy,
    output logic        icap_avail,
    output logic [7:0]  mon_data,
    output logic        mon_write
);
    logic [31:0] icap_o, icap_i;
    logic        icap_csib, icap_rdwrb, icap_prerror, icap_prdone;
    logic        fecc_eccerrornotsingle, fecc_eccerrorsingle, fecc_endofframe, fecc_endofscan, fecc_crcerror;
    logic [26:0] fecc_far;
    logic [1:0]  fecc_farsel;

    sem_ip u_sem (
        .status_heartbeat       (status[0]),
        .status_initialization  (status[1]),
        .status_observation     (status[2]),
        .status_correction      (status[3]),
        .status_classification  (status[4]),
        .status_injection       (status[5]),
        .status_diagnostic_scan (status[6]),
        .status_detect_only     (status[7]),
        .status_essential       (status[8]),
        .status_uncorrectable   (status[9]),
        .monitor_txdata         (mon_data),
        .monitor_txwrite        (mon_write),
        .monitor_txfull         (1'b0),
        .monitor_rxdata         (8'h00),
        .monitor_rxread         (),
        .monitor_rxempty        (1'b1),
        .command_strobe         (cmd_strobe),
        .command_busy           (cmd_busy),
        .command_code           (cmd_code),
        .icap_clk               (clk),
        .icap_o                 (icap_o),
        .icap_csib              (icap_csib),
        .icap_rdwrb             (icap_rdwrb),
        .icap_i                 (icap_i),
        .icap_prerror           (icap_prerror),
        .icap_prdone            (icap_prdone),
        .icap_avail             (icap_avail),
        .cap_rel                (1'b0),
        .cap_gnt                (grant),
        .cap_req                (),
        .fecc_eccerrornotsingle (fecc_eccerrornotsingle),
        .fecc_eccerrorsingle    (fecc_eccerrorsingle),
        .fecc_endofframe        (fecc_endofframe),
        .fecc_endofscan         (fecc_endofscan),
        .fecc_crcerror          (fecc_crcerror),
        .fecc_far               (fecc_far),
        .fecc_farsel            (fecc_farsel),
        .aux_error_cr_ne        (1'b0),
        .aux_error_cr_es        (1'b0),
        .aux_error_uc           (1'b0)
    );

    ICAPE3 #(.DEVICE_ID (32'hFFFF_FFFF)) u_icap (
        .PRERROR (icap_prerror),
        .PRDONE  (icap_prdone),
        .AVAIL   (icap_avail),
        .O       (icap_o),
        .I       (icap_i),
        .CSIB    (icap_csib),
        .RDWRB   (icap_rdwrb),
        .CLK     (clk)
    );

    FRAME_ECCE4 u_fecc (
        .ICAPTOPCLK        (clk),
        .ICAPBOTCLK        (),
        .ECCERRORNOTSINGLE (fecc_eccerrornotsingle),
        .ECCERRORSINGLE    (fecc_eccerrorsingle),
        .ENDOFFRAME        (fecc_endofframe),
        .ENDOFSCAN         (fecc_endofscan),
        .CRCERROR          (fecc_crcerror),
        .FAR               (fecc_far),
        .FARSEL            (fecc_farsel)
    );
endmodule
