# HPDP block design (sourced by create_project.tcl).
#
#   PS DDR4 ─HP0─ AXI DMA MM2S ──► hpdp_core ──► AXI DMA S2MM (compressed scenes) ─HP0─ PS DDR4
#                                      │  ▲
#                     GTH TX (gt_*) ◄──┘  └── GTH RX (gt_*)
#                                      └──► AXI DMA "dma_gr" S2MM (ground records) ─HP0─ PS DDR4
#
# Clocks (SPEC.md 5.2): clk_ctrl = pl_clk0 (100 MHz) for AXI4-Lite,
# clk_sys = pl_clk1 (250 MHz) for the data path, clk_sensor = 100 MHz from an
# MMCM (clocking wizard on pl_clk0), unrelated to clk_sys. clk_link (62.5 MHz)
# comes from the transceivers in hpdp_top: the gt_* pins of hpdp_core become
# block design ports. Addresses (SPEC.md 8.1): hpdp_core registers at
# 0xA000_0000 (1 MB), DMA at 0xA010_0000, ground DMA at 0xA011_0000.
#
# Expects ::build_id (32-bit value for SYS.BUILD_ID) from the caller.

proc latest_ip {name} {
    set defs [lsort -dictionary [get_ipdefs -all -filter "NAME == $name"]]
    if {![llength $defs]} { error "IP $name not found in the catalog" }
    return [lindex $defs end]
}

create_bd_design system

# ---------------------------------------------------------------------------
# Processing system: ZCU106 board preset (DDR4, MIO, PS clocks), then the
# PL interfaces this design needs
# ---------------------------------------------------------------------------
set ps [create_bd_cell -type ip -vlnv [latest_ip zynq_ultra_ps_e] ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0                {1}   \
    CONFIG.PSU__USE__M_AXI_GP1                {0}   \
    CONFIG.PSU__USE__M_AXI_GP2                {0}   \
    CONFIG.PSU__MAXIGP0__DATA_WIDTH           {32}  \
    CONFIG.PSU__USE__S_AXI_GP2                {1}   \
    CONFIG.PSU__SAXIGP2__DATA_WIDTH           {128} \
    CONFIG.PSU__FPGA_PL0_ENABLE               {1}   \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__FPGA_PL1_ENABLE               {1}   \
    CONFIG.PSU__CRL_APB__PL1_REF_CTRL__FREQMHZ {250} \
    CONFIG.PSU__USE__IRQ0                     {1}   \
] $ps

# PS DDR4: this ZCU106 carries an MTA4ATF51264HZ-2G6E1 SODIMM (read from its
# SPD: x16 devices, 8 Gb, 2 bank groups, 16 row bits). The board preset is
# for the older x8 module; with it the controller drives a bank-group bit the
# x16 devices lack and HIF address bit 14 aliases (0x4000 lands on 0x0).
# Set HPDP_DDR_BOARD_PRESET to keep the preset on boards with the x8 module.
if {![info exists ::env(HPDP_DDR_BOARD_PRESET)]} {
    set_property -dict [list \
        CONFIG.PSU__DDRC__DRAM_WIDTH      {16 Bits}    \
        CONFIG.PSU__DDRC__DEVICE_CAPACITY {8192 MBits} \
        CONFIG.PSU__DDRC__BG_ADDR_COUNT   {1}          \
        CONFIG.PSU__DDRC__ROW_ADDR_COUNT  {16}         \
    ] $ps
}

set clk_ctrl [get_bd_pins ps/pl_clk0]
set clk_sys  [get_bd_pins ps/pl_clk1]

# ---------------------------------------------------------------------------
# clk_sensor: MMCM, 100 MHz, from pl_clk0 (whose actual frequency is 99.99 MHz)
# ---------------------------------------------------------------------------
set mmcm [create_bd_cell -type ip -vlnv [latest_ip clk_wiz] clk_sensor_gen]
set_property -dict [list \
    CONFIG.PRIM_SOURCE                {Global_buffer} \
    CONFIG.PRIM_IN_FREQ               [get_property CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ $ps] \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {100.000} \
    CONFIG.CLK_OUT1_PORT              {clk_sensor} \
    CONFIG.USE_LOCKED                 {true} \
    CONFIG.RESET_TYPE                 {ACTIVE_LOW} \
    CONFIG.RESET_PORT                 {resetn} \
] $mmcm
connect_bd_net $clk_ctrl [get_bd_pins clk_sensor_gen/clk_in1]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins clk_sensor_gen/resetn]
set clk_sensor [get_bd_pins clk_sensor_gen/clk_sensor]

# ---------------------------------------------------------------------------
# Resets, one per clock domain
# ---------------------------------------------------------------------------
foreach {name clk} [list rst_ctrl $clk_ctrl rst_sys $clk_sys rst_sensor $clk_sensor] {
    create_bd_cell -type ip -vlnv [latest_ip proc_sys_reset] $name
    connect_bd_net $clk [get_bd_pins $name/slowest_sync_clk]
    connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins $name/ext_reset_in]
}
connect_bd_net [get_bd_pins clk_sensor_gen/locked] [get_bd_pins rst_sensor/dcm_locked]

# ---------------------------------------------------------------------------
# AXI DMA, simple mode: MM2S 128-bit (raw samples or calibration table),
# S2MM 64-bit stream (compressed scenes) into a 128-bit memory interface
# ---------------------------------------------------------------------------
set dma [create_bd_cell -type ip -vlnv [latest_ip axi_dma] dma]
set_property -dict [list \
    CONFIG.c_include_sg              {0}   \
    CONFIG.c_sg_length_width         {26}  \
    CONFIG.c_m_axi_mm2s_data_width   {128} \
    CONFIG.c_m_axis_mm2s_tdata_width {128} \
    CONFIG.c_m_axi_s2mm_data_width   {128} \
    CONFIG.c_s_axis_s2mm_tdata_width {64}  \
    CONFIG.c_mm2s_burst_size         {64}  \
    CONFIG.c_s2mm_burst_size         {64}  \
] $dma

# Ground capture: S2MM only, 1024-byte records in transfers of GR.XFER records
set dma_gr [create_bd_cell -type ip -vlnv [latest_ip axi_dma] dma_gr]
set_property -dict [list \
    CONFIG.c_include_sg              {0}   \
    CONFIG.c_include_mm2s            {0}   \
    CONFIG.c_sg_length_width         {26}  \
    CONFIG.c_m_axi_s2mm_data_width   {128} \
    CONFIG.c_s_axis_s2mm_tdata_width {64}  \
    CONFIG.c_s2mm_burst_size         {64}  \
] $dma_gr

# ---------------------------------------------------------------------------
# HPDP core (RTL module reference)
# ---------------------------------------------------------------------------
set core [create_bd_cell -type module -reference hpdp_core_bd hpdp]
set_property CONFIG.BUILD_ID $::build_id $core
# Build-time maxima (SYS-04): NX_MAX 512 by default; HPDP_NX_MAX=1024 scales the
# line and table memories (the calibration table goes to pairs above 512 x 256)
foreach {param var} {NX_MAX HPDP_NX_MAX NZ_MAX HPDP_NZ_MAX} {
    if {[info exists ::env($var)]} {
        set_property CONFIG.$param $::env($var) $core
        puts "$param = $::env($var) ($var)"
    }
}
# RC.TBL_ADDR (18 bits) addresses up to 2^18 table entries
if {[get_property CONFIG.NX_MAX $core] * [get_property CONFIG.NZ_MAX $core] > 262144} {
    error "NX_MAX * NZ_MAX above 2^18 (the table address RC.TBL_ADDR is 18 bits)"
}

connect_bd_intf_net [get_bd_intf_pins dma/M_AXIS_MM2S] [get_bd_intf_pins hpdp/s_axis_dma]
connect_bd_intf_net [get_bd_intf_pins hpdp/m_axis_cmp] [get_bd_intf_pins dma/S_AXIS_S2MM]
connect_bd_intf_net [get_bd_intf_pins hpdp/m_axis_gr]  [get_bd_intf_pins dma_gr/S_AXIS_S2MM]
connect_bd_net [get_bd_pins clk_sensor_gen/locked] [get_bd_pins hpdp/sensor_locked]

# Transceiver interface: block design ports with the pin names, wired in hpdp_top
foreach pin {
    clk_link gt_tx_done gt_rx_done gt_cplllock gt_powergood gt_reset_all gt_reset_rx sfp_tx_enable
    gt_txdata gt_txcharisk gt_prbs_sel gt_prbs_force_err gt_prbs_cnt_reset gt_loopback
    gt_txpolarity gt_rxpolarity gt_rxbufreset gt_rxdata gt_rxcharisk gt_rxdisperr gt_rxnotintable
    gt_rxbyteisaligned gt_rxprbserr gt_rxprbslocked gt_rxclkcorcnt gt_rxbufstatus
    clk_sem sem_status sem_cmd_busy sem_icap_avail sem_mon_data sem_mon_write
    sem_cmd_strobe sem_cmd_code sem_grant
} {
    make_bd_pins_external -name $pin [get_bd_pins hpdp/$pin]
}
set_property CONFIG.FREQ_HZ 62500000 [get_bd_ports clk_link]
set_property CONFIG.FREQ_HZ 31250000 [get_bd_ports clk_sem]

# ---------------------------------------------------------------------------
# Interconnect: control (PS -> core and DMA registers) and data (DMA -> PS DDR)
# ---------------------------------------------------------------------------
set sc_ctrl [create_bd_cell -type ip -vlnv [latest_ip smartconnect] sc_ctrl]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] $sc_ctrl
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins sc_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M00_AXI]   [get_bd_intf_pins dma/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M01_AXI]   [get_bd_intf_pins hpdp/s_axil]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M02_AXI]   [get_bd_intf_pins dma_gr/S_AXI_LITE]

set sc_data [create_bd_cell -type ip -vlnv [latest_ip smartconnect] sc_data]
set_property -dict [list CONFIG.NUM_SI {3} CONFIG.NUM_MI {1}] $sc_data
connect_bd_intf_net [get_bd_intf_pins dma/M_AXI_MM2S]    [get_bd_intf_pins sc_data/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins dma/M_AXI_S2MM]    [get_bd_intf_pins sc_data/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins dma_gr/M_AXI_S2MM] [get_bd_intf_pins sc_data/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_data/M00_AXI]   [get_bd_intf_pins ps/S_AXI_HP0_FPD]

# ---------------------------------------------------------------------------
# Clocks and resets
# ---------------------------------------------------------------------------
connect_bd_net $clk_ctrl \
    [get_bd_pins ps/maxihpm0_fpd_aclk] \
    [get_bd_pins sc_ctrl/aclk] \
    [get_bd_pins dma/s_axi_lite_aclk] \
    [get_bd_pins dma_gr/s_axi_lite_aclk] \
    [get_bd_pins hpdp/clk_ctrl]
connect_bd_net $clk_sys \
    [get_bd_pins ps/saxihp0_fpd_aclk] \
    [get_bd_pins sc_data/aclk] \
    [get_bd_pins dma/m_axi_mm2s_aclk] \
    [get_bd_pins dma/m_axi_s2mm_aclk] \
    [get_bd_pins dma_gr/m_axi_s2mm_aclk] \
    [get_bd_pins hpdp/clk_sys]
connect_bd_net $clk_sensor [get_bd_pins hpdp/clk_sensor]

connect_bd_net [get_bd_pins rst_ctrl/interconnect_aresetn]  [get_bd_pins sc_ctrl/aresetn]
connect_bd_net [get_bd_pins rst_ctrl/peripheral_aresetn]    [get_bd_pins dma/axi_resetn] \
                                                            [get_bd_pins dma_gr/axi_resetn] \
                                                            [get_bd_pins hpdp/rst_ctrl_n]
connect_bd_net [get_bd_pins rst_sys/interconnect_aresetn]   [get_bd_pins sc_data/aresetn]
connect_bd_net [get_bd_pins rst_sys/peripheral_aresetn]     [get_bd_pins hpdp/rst_sys_n]
connect_bd_net [get_bd_pins rst_sensor/peripheral_aresetn]  [get_bd_pins hpdp/rst_sensor_n]

# ---------------------------------------------------------------------------
# Interrupts -> pl_ps_irq0: [0] DMA MM2S, [1] DMA S2MM, [2] HPDP, [3] ground DMA S2MM
# ---------------------------------------------------------------------------
# ilconcat (inline HDL) replaces xlconcat from Vivado 2025.2; fall back for older releases
if {[llength [get_ipdefs -all -filter {NAME == ilconcat}]]} {
    set irq [create_bd_cell -type inline_hdl -vlnv [latest_ip ilconcat] irq_concat]
} else {
    set irq [create_bd_cell -type ip -vlnv [latest_ip xlconcat] irq_concat]
}
set_property CONFIG.NUM_PORTS {4} $irq
connect_bd_net [get_bd_pins dma/mm2s_introut]    [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins dma/s2mm_introut]    [get_bd_pins irq_concat/In1]
connect_bd_net [get_bd_pins hpdp/irq]            [get_bd_pins irq_concat/In2]
connect_bd_net [get_bd_pins dma_gr/s2mm_introut] [get_bd_pins irq_concat/In3]
connect_bd_net [get_bd_pins irq_concat/dout]     [get_bd_pins ps/pl_ps_irq0]

# ---------------------------------------------------------------------------
# Address map (SPEC.md section 8.1). The DMAs use 32-bit addresses, so they
# reach the low 2 GB of PS DDR, where all buffers live.
# ---------------------------------------------------------------------------
assign_bd_address -offset 0xA0000000 -range 1M \
    -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs hpdp/s_axil/reg0]
assign_bd_address -offset 0xA0100000 -range 64K \
    -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs dma/S_AXI_LITE/Reg]
assign_bd_address -offset 0xA0110000 -range 64K \
    -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs dma_gr/S_AXI_LITE/Reg]
foreach space {dma/Data_MM2S dma/Data_S2MM dma_gr/Data_S2MM} {
    assign_bd_address -target_address_space [get_bd_addr_spaces $space] \
        [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_LOW]
}

validate_bd_design
save_bd_design
