# HPDP on the ZCU106 (xczu7ev-ffvc1156-2-e): board pins of hpdp_top.
# Pins from UG1244 (table 3-32, SFP) and the board file (zcu106 v2.6), as used by
# the GTH SFP link already brought up on this board.
#
# The PS clocks (pl_clk0, pl_clk1), the clk_sensor MMCM, clk_fr and the GTH user
# clock are unrelated; every crossing between them is an XPM CDC macro, which
# brings its own constraints, so there are no clock groups here.

# 125 MHz system clock -> BUFGCE_DIV /4 -> clk_fr, 31.25 MHz (GTH free-running / DRP clock)
set_property -dict {PACKAGE_PIN H9 IOSTANDARD LVDS} [get_ports clk_125mhz_p]
set_property -dict {PACKAGE_PIN G9 IOSTANDARD LVDS} [get_ports clk_125mhz_n]
create_clock -period 8.000 -name clk_125mhz [get_ports clk_125mhz_p]

# SFP reference clock: 156.25 MHz (Si570 via SI53340) on MGTREFCLK1 of quad 226,
# used by quad 225 one quad south. The GTH user clocks are derived from it.
set_property PACKAGE_PIN U10 [get_ports sfp_refclk_p]
set_property PACKAGE_PIN U9  [get_ports sfp_refclk_n]
create_clock -period 6.400 -name sfp_refclk [get_ports sfp_refclk_p]

# SFP0 cage: GTHE4_CHANNEL_X0Y10 (quad 225 channel 2); SFP1: X0Y11 (channel 3).
# The channel LOCs come from the wizard's own constraints.
set_property PACKAGE_PIN AA2 [get_ports {sfp_rx_p[0]}]
set_property PACKAGE_PIN AA1 [get_ports {sfp_rx_n[0]}]
set_property PACKAGE_PIN Y4  [get_ports {sfp_tx_p[0]}]
set_property PACKAGE_PIN Y3  [get_ports {sfp_tx_n[0]}]
set_property PACKAGE_PIN W2  [get_ports {sfp_rx_p[1]}]
set_property PACKAGE_PIN W1  [get_ports {sfp_rx_n[1]}]
set_property PACKAGE_PIN W6  [get_ports {sfp_tx_p[1]}]
set_property PACKAGE_PIN W5  [get_ports {sfp_tx_n[1]}]

# SFPx_TX_DISABLE_B: high = transmitter on (LNK-09). Quasi-static.
set_property -dict {PACKAGE_PIN AE22 IOSTANDARD LVCMOS12 SLEW SLOW DRIVE 8} [get_ports {sfp_tx_disable_b[0]}]
set_property -dict {PACKAGE_PIN AF20 IOSTANDARD LVCMOS12 SLEW SLOW DRIVE 8} [get_ports {sfp_tx_disable_b[1]}]
set_false_path -to [get_ports {sfp_tx_disable_b[*]}]
