# In-System IBERT on both SFP channels (LNK-10 eye scans), only in builds with
# HPDP_IBERT=1 (vivado/create_project.tcl). gt0 = X0Y10 (SFP0), gt1 = X0Y11 (SFP1),
# as the wizard's channel order. Its clock, the GT DRP clock and the debug hub
# clock must all be clk_fr (31.25 MHz, vivado/ibert/ibert_debug.xdc): with the hub
# on a faster clock the Serial I/O Analyzer scans only +/-0.25 UI while labelling
# it +/-0.5 UI (found on this board in JSB MARK 1/gth_sfp_link, 2026-10-07).
create_ip -name in_system_ibert -vendor xilinx.com -library ip -module_name isib_link
set_property -dict [list CONFIG.C_GTS_USED {X0Y10 X0Y11} CONFIG.C_ENABLE_INPUT_PORTS {true} \
    CONFIG.SYSTEM_CLOCK.FREQ_HZ {31250000}] [get_ips isib_link]
