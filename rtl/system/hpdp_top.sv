`timescale 1ns / 1ps

// HPDP top level on the ZCU106: the block design (PS, DMA, hpdp_core) and the
// GTH transceivers of the two SFP cages (SPEC.md 6.9, vivado/ip/gt_link.tcl).
//
//   SFP0 = GTH X0Y10 (channel 0), SFP1 = GTH X0Y11 (channel 1), 1.25 Gbaud on
//   each channel's CPLL from the 156.25 MHz Si570 clock on MGTREFCLK1 of quad
//   226 (U10/U9, no I2C setup). Both channels transmit the same words; the core
//   picks the receiving one (LNK.CTRL.rx_sel; default SFP1, fed by the fiber
//   from SFP0). The RX side runs on the TX user clock (62.5 MHz, clk_link): the
//   elastic buffers absorb the recovered-clock offset.
//
//   clk_fr = 125 MHz board clock / 4 = 31.25 MHz: the wizard's free-running and
//   DRP clock (FREERUN_FREQUENCY must match). A 2 ms power-on reset on clk_fr
//   starts the wizard's reset sequence after configuration; software can
//   repeat it (LNK.CTRL.gt_reset, rx_reset).
//
//   TX_DISABLE_B (AE22, AF20) high turns the SFP transmitters on (the board
//   inverts it to TX_DISABLE). LOS and TX_FAULT are not wired to the PL on this
//   board (UG1244 table 3-32).
module hpdp_top #(
    parameter int POR_CYCLES = 65535            // clk_fr cycles, about 2 ms
) (
    input  wire       clk_125mhz_p,             // H9 / G9, LVDS
    input  wire       clk_125mhz_n,
    input  wire       sfp_refclk_p,             // U10 / U9, 156.25 MHz
    input  wire       sfp_refclk_n,
    input  wire [1:0] sfp_rx_p,                 // [0] SFP0, [1] SFP1
    input  wire [1:0] sfp_rx_n,
    output wire [1:0] sfp_tx_p,
    output wire [1:0] sfp_tx_n,
    output wire [1:0] sfp_tx_disable_b
);
    // ---------------------------------------------------------------------
    // Clocks
    // ---------------------------------------------------------------------
    wire clk125_ibuf, clk_fr;
    IBUFDS u_ibuf_125 (.I (clk_125mhz_p), .IB (clk_125mhz_n), .O (clk125_ibuf));
    BUFGCE_DIV #(.BUFGCE_DIVIDE (4)) u_bufg_fr (
        .I (clk125_ibuf), .CE (1'b1), .CLR (1'b0), .O (clk_fr));

    wire gtrefclk;
    IBUFDS_GTE4 #(.REFCLK_HROW_CK_SEL (2'b00)) u_ibuf_refclk (
        .I (sfp_refclk_p), .IB (sfp_refclk_n), .CEB (1'b0), .O (gtrefclk), .ODIV2 ());

    // ---------------------------------------------------------------------
    // Transceiver resets (clk_fr): power-on, then software requests. Registered,
    // so no LUT output reaches the wizard's asynchronous reset inputs.
    // ---------------------------------------------------------------------
    wire gt_reset_all_req, gt_reset_rx_req;     // clk_ctrl, from the core
    wire reset_all_fr, reset_rx_fr;

    xpm_cdc_single #(.DEST_SYNC_FF (2), .SRC_INPUT_REG (0)) u_cdc_reset_all (
        .src_clk (1'b0), .src_in (gt_reset_all_req), .dest_clk (clk_fr), .dest_out (reset_all_fr));
    xpm_cdc_single #(.DEST_SYNC_FF (2), .SRC_INPUT_REG (0)) u_cdc_reset_rx (
        .src_clk (1'b0), .src_in (gt_reset_rx_req), .dest_clk (clk_fr), .dest_out (reset_rx_fr));

    logic [15:0] por_cnt = '0;
    logic        por = 1'b1;
    logic        gt_reset_all = 1'b1, gt_reset_rx = 1'b0;
    always_ff @(posedge clk_fr) begin
        if (por_cnt != 16'(POR_CYCLES)) por_cnt <= por_cnt + 16'd1;
        por          <= (por_cnt != 16'(POR_CYCLES));
        gt_reset_all <= por || reset_all_fr;
        gt_reset_rx  <= reset_rx_fr;
    end

    // ---------------------------------------------------------------------
    // GTH wizard, two channels
    // ---------------------------------------------------------------------
    wire        usrclk, clk_link, tx_active;
    wire        tx_done, rx_done;
    wire [1:0]  txpmaresetdone, cplllock, powergood;
    wire [15:0] txdata;
    wire [1:0]  txcharisk;
    wire [3:0]  prbs_sel;
    wire        prbs_force_err, prbs_cnt_reset, txpol, rxpol;
    wire [2:0]  loopback;
    wire [1:0]  rxbufreset;
    wire [31:0] rxdata, rxctrl0, rxctrl1;
    wire [15:0] rxctrl3;
    wire [1:0]  rxbyteisaligned, rxprbserr, rxprbslocked;
    wire [3:0]  rxclkcorcnt;
    wire [5:0]  rxbufstatus;

    gt_link u_gt (
        .gtwiz_userclk_tx_reset_in          (~&txpmaresetdone),
        .gtwiz_userclk_tx_srcclk_out        (),
        .gtwiz_userclk_tx_usrclk_out        (usrclk),
        .gtwiz_userclk_tx_usrclk2_out       (clk_link),
        .gtwiz_userclk_tx_active_out        (tx_active),
        .gtwiz_userclk_rx_active_in         (tx_active),
        .gtwiz_reset_clk_freerun_in         (clk_fr),
        .gtwiz_reset_all_in                 (gt_reset_all),
        .gtwiz_reset_tx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_tx_datapath_in         (1'b0),
        .gtwiz_reset_rx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_rx_datapath_in         (gt_reset_rx),
        .gtwiz_reset_rx_cdr_stable_out      (),
        .gtwiz_reset_tx_done_out            (tx_done),
        .gtwiz_reset_rx_done_out            (rx_done),
        .gtwiz_userdata_tx_in               ({2{txdata}}),
        .gtwiz_userdata_rx_out              (rxdata),
        .drpaddr_in                         (20'd0),
        .drpclk_in                          ({2{clk_fr}}),
        .drpdi_in                           (32'd0),
        .drpen_in                           (2'b00),
        .drpwe_in                           (2'b00),
        .eyescanreset_in                    (2'b00),
        .gthrxn_in                          (sfp_rx_n),
        .gthrxp_in                          (sfp_rx_p),
        .gtrefclk0_in                       ({2{gtrefclk}}),
        .loopback_in                        ({2{loopback}}),
        .rx8b10ben_in                       (2'b11),
        .rxbufreset_in                      (rxbufreset),
        .rxcommadeten_in                    (2'b11),
        .rxlpmen_in                         (2'b00),          // DFE (wizard default)
        .rxmcommaalignen_in                 (2'b11),
        .rxpcommaalignen_in                 (2'b11),
        .rxpolarity_in                      ({2{rxpol}}),
        .rxprbscntreset_in                  ({2{prbs_cnt_reset}}),
        .rxprbssel_in                       ({2{prbs_sel}}),
        .rxrate_in                          (6'd0),
        .rxusrclk_in                        ({2{usrclk}}),
        .rxusrclk2_in                       ({2{clk_link}}),
        .tx8b10ben_in                       (2'b11),
        .txctrl0_in                         (32'd0),
        .txctrl1_in                         (32'd0),
        .txctrl2_in                         ({2{6'd0, txcharisk}}),
        .txdiffctrl_in                      ({2{5'b11000}}),  // wizard default swing
        .txpolarity_in                      ({2{txpol}}),
        .txpostcursor_in                    (10'd0),
        .txprbsforceerr_in                  ({2{prbs_force_err}}),
        .txprbssel_in                       ({2{prbs_sel}}),
        .txprecursor_in                     (10'd0),
        .cplllock_out                       (cplllock),
        .drpdo_out                          (),
        .drprdy_out                         (),
        .gthtxn_out                         (sfp_tx_n),
        .gthtxp_out                         (sfp_tx_p),
        .gtpowergood_out                    (powergood),
        .rxbufstatus_out                    (rxbufstatus),
        .rxbyteisaligned_out                (rxbyteisaligned),
        .rxbyterealign_out                  (),
        .rxclkcorcnt_out                    (rxclkcorcnt),
        .rxcommadet_out                     (),
        .rxctrl0_out                        (rxctrl0),
        .rxctrl1_out                        (rxctrl1),
        .rxctrl2_out                        (),
        .rxctrl3_out                        (rxctrl3),
        .rxoutclk_out                       (),
        .rxpmaresetdone_out                 (),
        .rxprbserr_out                      (rxprbserr),
        .rxprbslocked_out                   (rxprbslocked),
        .rxresetdone_out                    (),
        .txbufstatus_out                    (),
        .txpmaresetdone_out                 (txpmaresetdone),
        .txresetdone_out                    ()
    );

    // per channel: RXCTRL0 = K flags, RXCTRL1 = disparity errors (16 bits each),
    // RXCTRL3 = not-in-table (8 bits each); two bytes per word
    wire [3:0] rxcharisk    = {rxctrl0[17:16], rxctrl0[1:0]};
    wire [3:0] rxdisperr    = {rxctrl1[17:16], rxctrl1[1:0]};
    wire [3:0] rxnotintable = {rxctrl3[9:8],   rxctrl3[1:0]};

    // ---------------------------------------------------------------------
    // Block design: PS, DMA, hpdp_core
    // ---------------------------------------------------------------------
    system_wrapper u_system (
        .clk_link           (clk_link),
        .gt_tx_done         (tx_done),
        .gt_rx_done         (rx_done),
        .gt_cplllock        (cplllock),
        .gt_powergood       (powergood),
        .gt_reset_all       (gt_reset_all_req),
        .gt_reset_rx        (gt_reset_rx_req),
        .sfp_tx_enable      (sfp_tx_disable_b),
        .gt_txdata          (txdata),
        .gt_txcharisk       (txcharisk),
        .gt_prbs_sel        (prbs_sel),
        .gt_prbs_force_err  (prbs_force_err),
        .gt_prbs_cnt_reset  (prbs_cnt_reset),
        .gt_loopback        (loopback),
        .gt_txpolarity      (txpol),
        .gt_rxpolarity      (rxpol),
        .gt_rxbufreset      (rxbufreset),
        .gt_rxdata          (rxdata),
        .gt_rxcharisk       (rxcharisk),
        .gt_rxdisperr       (rxdisperr),
        .gt_rxnotintable    (rxnotintable),
        .gt_rxbyteisaligned (rxbyteisaligned),
        .gt_rxprbserr       (rxprbserr),
        .gt_rxprbslocked    (rxprbslocked),
        .gt_rxclkcorcnt     (rxclkcorcnt),
        .gt_rxbufstatus     (rxbufstatus)
    );
endmodule
