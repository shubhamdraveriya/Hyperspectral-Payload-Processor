# GTH transceivers for the optical link (SPEC.md 6.9), UltraScale Transceivers
# Wizard. Sourced by create_project.tcl (or on its own in an open project).
#
# Settings follow the GTH SFP link already brought up on this ZCU106
# (Desktop/JSB MARK 1/gth_sfp_link: near-end PMA loopback eye 89 % UI wide):
#
# - SFP0 = GTH bank 225 channel 2 (X0Y10, TX Y4, RX AA2), SFP1 = channel 3
#   (X0Y11, TX W6, RX W2). The link is SFP0 TX -> fiber -> SFP1 RX; both
#   channels have TX and RX (PRBS and loopback tests use both).
# - Reference clock 156.25 MHz on MGTREFCLK1 of bank 226 (U10/U9): the on-board
#   Si570 through an SI53340 buffer, no I2C setup needed (measured 156,250,064 Hz).
#   It reaches bank 225 one quad south ("clk1+1"). The alternative Si5328 on
#   MGTREFCLK1_225 needs I2C programming first.
# - 1.25 Gbaud on the CPLL, built-in 8b/10b, 16-bit user words (62.5 MHz), comma
#   alignment of K28.5 into byte 0, RX elastic buffer with clock correction on the
#   idle word {K28.5, D16.2} (+-200 ppm), equalization left at the wizard default.
# - RX user clocking outside the core: the RX side runs on the TX user clock
#   (the elastic buffer bridges the recovered clock), one 62.5 MHz domain.
# - Free-running / DRP clock 31.25 MHz (125 MHz board clock / 4). FREERUN_FREQUENCY
#   must equal it: with a wrong value the CPLL calibration never completes on
#   hardware although simulation passes. Later property groups can reset it, so it
#   is set last and checked.

set ip [create_ip -name gtwizard_ultrascale -vendor xilinx.com -library ip -module_name gt_link]
set_property -dict [list \
    CONFIG.CHANNEL_ENABLE                {X0Y11 X0Y10} \
    CONFIG.TX_MASTER_CHANNEL             {X0Y10} \
    CONFIG.RX_MASTER_CHANNEL             {X0Y10} \
    CONFIG.TX_LINE_RATE                  {1.25} \
    CONFIG.RX_LINE_RATE                  {1.25} \
    CONFIG.TX_PLL_TYPE                   {CPLL} \
    CONFIG.RX_PLL_TYPE                   {CPLL} \
    CONFIG.TX_REFCLK_FREQUENCY           {156.25} \
    CONFIG.RX_REFCLK_FREQUENCY           {156.25} \
] [get_ips gt_link]
set_property -dict [list \
    CONFIG.TX_DATA_ENCODING              {8B10B} \
    CONFIG.RX_DATA_DECODING              {8B10B} \
    CONFIG.TX_USER_DATA_WIDTH            {16} \
    CONFIG.TX_INT_DATA_WIDTH             {20} \
    CONFIG.RX_USER_DATA_WIDTH            {16} \
    CONFIG.RX_INT_DATA_WIDTH             {20} \
] [get_ips gt_link]
set_property -dict [list \
    CONFIG.TX_REFCLK_SOURCE              {X0Y11 clk1+1 X0Y10 clk1+1} \
    CONFIG.RX_REFCLK_SOURCE              {X0Y11 clk1+1 X0Y10 clk1+1} \
] [get_ips gt_link]
set_property -dict [list \
    CONFIG.RX_COMMA_PRESET               {K28.5} \
    CONFIG.RX_COMMA_P_ENABLE             {true} \
    CONFIG.RX_COMMA_M_ENABLE             {true} \
    CONFIG.RX_COMMA_MASK                 {1111111111} \
    CONFIG.RX_COMMA_ALIGN_WORD           {2} \
    CONFIG.RX_COMMA_DOUBLE_ENABLE        {false} \
    CONFIG.RX_COMMA_SHOW_REALIGN_ENABLE  {true} \
] [get_ips gt_link]
set_property -dict [list \
    CONFIG.RX_CC_NUM_SEQ                 {1} \
    CONFIG.RX_CC_LEN_SEQ                 {2} \
    CONFIG.RX_CC_VAL_0_0                 {10111100} \
    CONFIG.RX_CC_K_0_0                   {true} \
    CONFIG.RX_CC_VAL_0_1                 {01010000} \
    CONFIG.RX_CC_K_0_1                   {false} \
    CONFIG.RX_CC_PERIODICITY             {1000} \
    CONFIG.RX_PPM_OFFSET                 {200} \
] [get_ips gt_link]
set_property -dict [list \
    CONFIG.LOCATE_TX_USER_CLOCKING       {CORE} \
    CONFIG.LOCATE_RX_USER_CLOCKING       {EXAMPLE_DESIGN} \
    CONFIG.LOCATE_RESET_CONTROLLER       {CORE} \
    CONFIG.LOCATE_COMMON                 {CORE} \
] [get_ips gt_link]
set_property CONFIG.ENABLE_OPTIONAL_PORTS {cplllock_out gtpowergood_out loopback_in rxbufstatus_out rxbyteisaligned_out rxbyterealign_out rxclkcorcnt_out rxcommadet_out rxctrl0_out rxctrl1_out rxctrl2_out rxctrl3_out rxpmaresetdone_out rxpolarity_in rxprbscntreset_in rxprbserr_out rxprbslocked_out rxprbssel_in rxresetdone_out txbufstatus_out txctrl0_in txctrl1_in txctrl2_in txpmaresetdone_out txpolarity_in txprbsforceerr_in txprbssel_in txresetdone_out txdiffctrl_in txpostcursor_in txprecursor_in drpaddr_in drpdi_in drpen_in drpwe_in drpdo_out drprdy_out eyescanreset_in rxrate_in rxlpmen_in} [get_ips gt_link]

# set last, and check (see above)
set_property CONFIG.FREERUN_FREQUENCY {31.25} [get_ips gt_link]
if {[get_property CONFIG.FREERUN_FREQUENCY [get_ips gt_link]] != 31.25} {
    error "gt_link: FREERUN_FREQUENCY is not 31.25 MHz"
}
