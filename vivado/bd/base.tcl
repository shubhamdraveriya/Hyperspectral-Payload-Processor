# M0 base block design (sourced by create_project.tcl).
#
#   PS DDR4 ──HP0── AXI DMA MM2S ──► AXI-Stream FIFO ──► AXI DMA S2MM ──HP0── PS DDR4
#
# The loopback FIFO marks where the HPDP data path will be inserted. Clocks
# and addresses follow SPEC.md: clk_ctrl = pl_clk0 (100 MHz) for AXI4-Lite,
# clk_sys = pl_clk1 (250 MHz) for the data path, DMA registers at 0xA00A_0000.

proc latest_ip {name} {
    set defs [lsort -dictionary [get_ipdefs -all -filter "NAME == $name"]]
    if {![llength $defs]} { error "IP $name not found in the catalog" }
    return [lindex $defs end]
}

create_bd_design base

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
# for the older x8 module; with it the controller drives a bank-group bit
# the x16 devices lack and HIF address bit 14 aliases (0x4000 lands on 0x0).
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
# Resets, one per clock domain
# ---------------------------------------------------------------------------
create_bd_cell -type ip -vlnv [latest_ip proc_sys_reset] rst_ctrl
create_bd_cell -type ip -vlnv [latest_ip proc_sys_reset] rst_sys
connect_bd_net $clk_ctrl [get_bd_pins rst_ctrl/slowest_sync_clk]
connect_bd_net $clk_sys  [get_bd_pins rst_sys/slowest_sync_clk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst_ctrl/ext_reset_in] [get_bd_pins rst_sys/ext_reset_in]

# ---------------------------------------------------------------------------
# AXI DMA, simple mode, 128-bit, registers on clk_ctrl and data on clk_sys
# (Vivado detects the asynchronous clocks from the connections below)
# ---------------------------------------------------------------------------
set dma [create_bd_cell -type ip -vlnv [latest_ip axi_dma] dma]
set_property -dict [list \
    CONFIG.c_include_sg              {0}   \
    CONFIG.c_sg_length_width         {26}  \
    CONFIG.c_m_axi_mm2s_data_width   {128} \
    CONFIG.c_m_axis_mm2s_tdata_width {128} \
    CONFIG.c_m_axi_s2mm_data_width   {128} \
    CONFIG.c_s_axis_s2mm_tdata_width {128} \
    CONFIG.c_mm2s_burst_size         {64}  \
    CONFIG.c_s2mm_burst_size         {64}  \
] $dma

# Loopback FIFO: the future HPDP data path goes here
set fifo [create_bd_cell -type ip -vlnv [latest_ip axis_data_fifo] loopback_fifo]
set_property CONFIG.FIFO_DEPTH {1024} $fifo
connect_bd_intf_net [get_bd_intf_pins dma/M_AXIS_MM2S]     [get_bd_intf_pins loopback_fifo/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins loopback_fifo/M_AXIS] [get_bd_intf_pins dma/S_AXIS_S2MM]

# ---------------------------------------------------------------------------
# Interconnect: control (PS -> DMA registers) and data (DMA -> PS DDR)
# ---------------------------------------------------------------------------
set sc_ctrl [create_bd_cell -type ip -vlnv [latest_ip smartconnect] sc_ctrl]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc_ctrl
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins sc_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M00_AXI]   [get_bd_intf_pins dma/S_AXI_LITE]

set sc_data [create_bd_cell -type ip -vlnv [latest_ip smartconnect] sc_data]
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $sc_data
connect_bd_intf_net [get_bd_intf_pins dma/M_AXI_MM2S]  [get_bd_intf_pins sc_data/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins dma/M_AXI_S2MM]  [get_bd_intf_pins sc_data/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_data/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]

# ---------------------------------------------------------------------------
# Clocks and resets
# ---------------------------------------------------------------------------
connect_bd_net $clk_ctrl \
    [get_bd_pins ps/maxihpm0_fpd_aclk] \
    [get_bd_pins sc_ctrl/aclk] \
    [get_bd_pins dma/s_axi_lite_aclk]
connect_bd_net $clk_sys \
    [get_bd_pins ps/saxihp0_fpd_aclk] \
    [get_bd_pins sc_data/aclk] \
    [get_bd_pins dma/m_axi_mm2s_aclk] \
    [get_bd_pins dma/m_axi_s2mm_aclk] \
    [get_bd_pins loopback_fifo/s_axis_aclk]

connect_bd_net [get_bd_pins rst_ctrl/interconnect_aresetn] [get_bd_pins sc_ctrl/aresetn]
connect_bd_net [get_bd_pins rst_ctrl/peripheral_aresetn]   [get_bd_pins dma/axi_resetn]
connect_bd_net [get_bd_pins rst_sys/interconnect_aresetn]  [get_bd_pins sc_data/aresetn]
connect_bd_net [get_bd_pins rst_sys/peripheral_aresetn]    [get_bd_pins loopback_fifo/s_axis_aresetn]

# ---------------------------------------------------------------------------
# Interrupts -> pl_ps_irq0
# ---------------------------------------------------------------------------
# ilconcat (inline HDL) replaces xlconcat from Vivado 2025.2; fall back for older releases
if {[llength [get_ipdefs -all -filter {NAME == ilconcat}]]} {
    set irq [create_bd_cell -type inline_hdl -vlnv [latest_ip ilconcat] irq_concat]
} else {
    set irq [create_bd_cell -type ip -vlnv [latest_ip xlconcat] irq_concat]
}
set_property CONFIG.NUM_PORTS {2} $irq
connect_bd_net [get_bd_pins dma/mm2s_introut]  [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins dma/s2mm_introut]  [get_bd_pins irq_concat/In1]
connect_bd_net [get_bd_pins irq_concat/dout]   [get_bd_pins ps/pl_ps_irq0]

# ---------------------------------------------------------------------------
# Address map (SPEC.md section 8.1). The DMA uses 32-bit addresses, so it
# reaches the low 2 GB of PS DDR, where all buffers live.
# ---------------------------------------------------------------------------
assign_bd_address -offset 0xA00A0000 -range 64K \
    -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs dma/S_AXI_LITE/Reg]
foreach space {Data_MM2S Data_S2MM} {
    assign_bd_address -target_address_space [get_bd_addr_spaces dma/$space] \
        [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_LOW]
}

validate_bd_design
save_bd_design
