# Soft Error Mitigation controller (RAD-01), instantiated in hpdp_top
# (rtl/rad/sem_support.sv) on clk_fr = 31.25 MHz, which runs from the board
# oscillator, independent of the PS clocks. Mitigation and testing: continuous
# scan and correction of the configuration memory, error injection through the
# command interface. The ICAPE3 and FRAME_ECCE4 primitives are in sem_support.
# On Zynq UltraScale+, software hands the configuration logic to the ICAP (CSU
# pcap_ctrl.PCAP_PR = 0) and then grants it to the controller (RAD.SEM_CTRL.grant).
create_ip -name sem_ultra -vendor xilinx.com -library ip -module_name sem_ip
set_property -dict [list CONFIG.MODE {mitigation_and_testing} CONFIG.CLOCK_PERIOD {32000} \
    CONFIG.ENABLE_CLASSIFICATION {false} CONFIG.LOCATE_CONFIG_PRIM {example_design}] [get_ips sem_ip]
