# Implementation-only constraints of HPDP_IBERT=1 builds (In-System IBERT, LNK-10).
# Debug hub on the free-running clk_fr (31.25 MHz), so the IBERT stays reachable
# while the GTH user clock is down; the IBERT clock and the DRP clock are clk_fr
# too, and all three must stay equal. The JTAG clock must be <= 12.5 MHz (2.5x
# slower than the hub, UG908): sw/host/ibert_scan.tcl uses 6 MHz.
set_property C_CLK_INPUT_FREQ_HZ 31250000 [get_debug_cores dbg_hub]
set_property C_ENABLE_CLK_DIVIDER false [get_debug_cores dbg_hub]
set_property C_USER_SCAN_CHAIN 1 [get_debug_cores dbg_hub]
connect_debug_port dbg_hub/clk [get_nets clk_fr]

# The eye-scan sampler is off in the wizard's channels; it does not touch the data path
set_property ES_EYE_SCAN_EN TRUE [get_cells -hier -filter {REF_NAME == GTHE4_CHANNEL}]
