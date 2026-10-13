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
    parameter bit IBERT      = 1'b0,            // In-System IBERT (HPDP_IBERT=1 builds, LNK-10)
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

    // In-System IBERT (LNK-10 eye scans in Hardware Manager), only in HPDP_IBERT=1
    // builds: it drives the channels' DRP ports and eye-scan resets, and passes the
    // design's TX/RX settings through unless they are overridden in Hardware
    // Manager. Its clock, the DRP clock and the debug hub are clk_fr.
    logic [19:0] ib_drpaddr;
    logic [31:0] ib_drpdi, gt_drpdo;
    logic [1:0]  ib_drpen, ib_drpwe, gt_drprdy, ib_eyescanreset, ib_rxlpmen;
    logic [5:0]  ib_rxrate;
    logic [9:0]  ib_txdiff, ib_txpre, ib_txpost;

    if (IBERT) begin : g_ibert
        isib_link u_isib (
            .clk            (clk_fr),
            .drpclk_i       ({2{clk_fr}}),
            .drpclk_o       (),
            .gt0_drpen_o    (ib_drpen[0]),
            .gt0_drpwe_o    (ib_drpwe[0]),
            .gt0_drpaddr_o  (ib_drpaddr[9:0]),
            .gt0_drpdi_o    (ib_drpdi[15:0]),
            .gt0_drprdy_i   (gt_drprdy[0]),
            .gt0_drpdo_i    (gt_drpdo[15:0]),
            .gt1_drpen_o    (ib_drpen[1]),
            .gt1_drpwe_o    (ib_drpwe[1]),
            .gt1_drpaddr_o  (ib_drpaddr[19:10]),
            .gt1_drpdi_o    (ib_drpdi[31:16]),
            .gt1_drprdy_i   (gt_drprdy[1]),
            .gt1_drpdo_i    (gt_drpdo[31:16]),
            .eyescanreset_o (ib_eyescanreset),
            .rxrate_o       (ib_rxrate),
            .txdiffctrl_o   (ib_txdiff),
            .txprecursor_o  (ib_txpre),
            .txpostcursor_o (ib_txpost),
            .rxlpmen_o      (ib_rxlpmen),
            .rxrate_i       (6'd0),
            .txdiffctrl_i   ({2{5'b11000}}),
            .txprecursor_i  (10'd0),
            .txpostcursor_i (10'd0),
            .rxlpmen_i      (2'b00),
            .rxoutclk_i     ({2{usrclk}})
        );
    end else begin : g_no_ibert
        assign ib_drpaddr      = '0;
        assign ib_drpdi        = '0;
        assign ib_drpen        = '0;
        assign ib_drpwe        = '0;
        assign ib_eyescanreset = '0;
        assign ib_rxlpmen      = 2'b00;           // DFE (wizard default)
        assign ib_rxrate       = '0;
        assign ib_txdiff       = {2{5'b11000}};   // wizard default swing
        assign ib_txpre        = '0;
        assign ib_txpost       = '0;
    end

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
        .drpaddr_in                         (ib_drpaddr),
        .drpclk_in                          ({2{clk_fr}}),
        .drpdi_in                           (ib_drpdi),
        .drpen_in                           (ib_drpen),
        .drpwe_in                           (ib_drpwe),
        .eyescanreset_in                    (ib_eyescanreset),
        .gthrxn_in                          (sfp_rx_n),
        .gthrxp_in                          (sfp_rx_p),
        .gtrefclk0_in                       ({2{gtrefclk}}),
        .loopback_in                        ({2{loopback}}),
        .rx8b10ben_in                       (2'b11),
        .rxbufreset_in                      (rxbufreset),
        .rxcommadeten_in                    (2'b11),
        .rxlpmen_in                         (ib_rxlpmen),
        .rxmcommaalignen_in                 (2'b11),
        .rxpcommaalignen_in                 (2'b11),
        .rxpolarity_in                      ({2{rxpol}}),
        .rxprbscntreset_in                  ({2{prbs_cnt_reset}}),
        .rxprbssel_in                       ({2{prbs_sel}}),
        .rxrate_in                          (ib_rxrate),
        .rxusrclk_in                        ({2{usrclk}}),
        .rxusrclk2_in                       ({2{clk_link}}),
        .tx8b10ben_in                       (2'b11),
        .txctrl0_in                         (32'd0),
        .txctrl1_in                         (32'd0),
        .txctrl2_in                         ({2{6'd0, txcharisk}}),
        .txdiffctrl_in                      (ib_txdiff),
        .txpolarity_in                      ({2{txpol}}),
        .txpostcursor_in                    (ib_txpost),
        .txprbsforceerr_in                  ({2{prbs_force_err}}),
        .txprbssel_in                       ({2{prbs_sel}}),
        .txprecursor_in                     (ib_txpre),
        .cplllock_out                       (cplllock),
        .drpdo_out                          (gt_drpdo),
        .drprdy_out                         (gt_drprdy),
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
    // Soft error mitigation controller (RAD-01) on clk_fr, which runs from the
    // board oscillator whatever the PS does; its interface goes to hpdp_core
    logic [9:0]  sem_status;
    logic        sem_cmd_busy, sem_icap_avail, sem_mon_write, sem_cmd_strobe, sem_grant;
    logic [7:0]  sem_mon_data;
    logic [43:0] sem_cmd_code;

    sem_support u_sem (
        .clk        (clk_fr),
        .grant      (sem_grant),
        .status     (sem_status),
        .cmd_strobe (sem_cmd_strobe),
        .cmd_code   (sem_cmd_code),
        .cmd_busy   (sem_cmd_busy),
        .icap_avail (sem_icap_avail),
        .mon_data   (sem_mon_data),
        .mon_write  (sem_mon_write)
    );

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
        .gt_rxbufstatus     (rxbufstatus),
        .clk_sem            (clk_fr),
        .sem_status         (sem_status),
        .sem_cmd_busy       (sem_cmd_busy),
        .sem_icap_avail     (sem_icap_avail),
        .sem_mon_data       (sem_mon_data),
        .sem_mon_write      (sem_mon_write),
        .sem_cmd_strobe     (sem_cmd_strobe),
        .sem_cmd_code       (sem_cmd_code),
        .sem_grant          (sem_grant)
    );
endmodule
