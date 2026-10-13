`timescale 1ns / 1ps

// Verilog wrapper of hpdp_core for the block design (a module reference must be
// Verilog or VHDL). The bus interfaces are inferred from the port names; the
// attributes give their clocks and resets. The transceiver ports (gt_*,
// clk_link, sfp_tx_enable) become block design ports, wired to the GTH wizard in
// hpdp_top.
module hpdp_core_bd #(
    parameter [31:0] BUILD_ID = 32'h0
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk_ctrl CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axil, ASSOCIATED_RESET rst_ctrl_n" *)
    input  wire         clk_ctrl,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst_ctrl_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire         rst_ctrl_n,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk_sys CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axis_dma:m_axis_cmp:m_axis_gr, ASSOCIATED_RESET rst_sys_n" *)
    input  wire         clk_sys,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst_sys_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire         rst_sys_n,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk_sensor CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_RESET rst_sensor_n" *)
    input  wire         clk_sensor,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst_sensor_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire         rst_sensor_n,
    input  wire         sensor_locked,

    input  wire         s_axil_awvalid,
    output wire         s_axil_awready,
    input  wire [19:0]  s_axil_awaddr,
    input  wire [2:0]   s_axil_awprot,
    input  wire         s_axil_wvalid,
    output wire         s_axil_wready,
    input  wire [31:0]  s_axil_wdata,
    input  wire [3:0]   s_axil_wstrb,
    output wire         s_axil_bvalid,
    input  wire         s_axil_bready,
    output wire [1:0]   s_axil_bresp,
    input  wire         s_axil_arvalid,
    output wire         s_axil_arready,
    input  wire [19:0]  s_axil_araddr,
    input  wire [2:0]   s_axil_arprot,
    output wire         s_axil_rvalid,
    input  wire         s_axil_rready,
    output wire [31:0]  s_axil_rdata,
    output wire [1:0]   s_axil_rresp,

    input  wire [127:0] s_axis_dma_tdata,
    input  wire         s_axis_dma_tlast,
    input  wire         s_axis_dma_tvalid,
    output wire         s_axis_dma_tready,

    output wire [63:0]  m_axis_cmp_tdata,
    output wire [7:0]   m_axis_cmp_tkeep,
    output wire         m_axis_cmp_tlast,
    output wire         m_axis_cmp_tvalid,
    input  wire         m_axis_cmp_tready,

    output wire [63:0]  m_axis_gr_tdata,
    output wire [7:0]   m_axis_gr_tkeep,
    output wire         m_axis_gr_tlast,
    output wire         m_axis_gr_tvalid,
    input  wire         m_axis_gr_tready,

    input  wire         gt_tx_done,
    input  wire         gt_rx_done,
    input  wire [1:0]   gt_cplllock,
    input  wire [1:0]   gt_powergood,
    output wire         gt_reset_all,
    output wire         gt_reset_rx,
    output wire [1:0]   sfp_tx_enable,

    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk_link CLK" *)
    (* X_INTERFACE_PARAMETER = "FREQ_HZ 62500000" *)
    input  wire         clk_link,
    output wire [15:0]  gt_txdata,
    output wire [1:0]   gt_txcharisk,
    output wire [3:0]   gt_prbs_sel,
    output wire         gt_prbs_force_err,
    output wire         gt_prbs_cnt_reset,
    output wire [2:0]   gt_loopback,
    output wire         gt_txpolarity,
    output wire         gt_rxpolarity,
    output wire [1:0]   gt_rxbufreset,
    input  wire [31:0]  gt_rxdata,
    input  wire [3:0]   gt_rxcharisk,
    input  wire [3:0]   gt_rxdisperr,
    input  wire [3:0]   gt_rxnotintable,
    input  wire [1:0]   gt_rxbyteisaligned,
    input  wire [1:0]   gt_rxprbserr,
    input  wire [1:0]   gt_rxprbslocked,
    input  wire [3:0]   gt_rxclkcorcnt,
    input  wire [5:0]   gt_rxbufstatus,

    // Soft error mitigation controller (RAD-01, in hpdp_top)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk_sem CLK" *)
    (* X_INTERFACE_PARAMETER = "FREQ_HZ 31250000" *)
    input  wire         clk_sem,
    input  wire [9:0]   sem_status,
    input  wire         sem_cmd_busy,
    input  wire         sem_icap_avail,
    input  wire [7:0]   sem_mon_data,
    input  wire         sem_mon_write,
    output wire         sem_cmd_strobe,
    output wire [43:0]  sem_cmd_code,
    output wire         sem_grant,

    (* X_INTERFACE_INFO = "xilinx.com:signal:interrupt:1.0 irq INTERRUPT" *)
    (* X_INTERFACE_PARAMETER = "SENSITIVITY LEVEL_HIGH" *)
    output wire         irq
);
    hpdp_core #(
        .NX_MAX   (512),
        .NZ_MAX   (256),
        .BUILD_ID (BUILD_ID)
    ) u_core (
        .clk_ctrl           (clk_ctrl),
        .rst_ctrl_n         (rst_ctrl_n),
        .clk_sys            (clk_sys),
        .rst_sys_n          (rst_sys_n),
        .clk_sensor         (clk_sensor),
        .rst_sensor_n       (rst_sensor_n),
        .sensor_locked      (sensor_locked),
        .s_axil_awvalid     (s_axil_awvalid),
        .s_axil_awready     (s_axil_awready),
        .s_axil_awaddr      (s_axil_awaddr),
        .s_axil_awprot      (s_axil_awprot),
        .s_axil_wvalid      (s_axil_wvalid),
        .s_axil_wready      (s_axil_wready),
        .s_axil_wdata       (s_axil_wdata),
        .s_axil_wstrb       (s_axil_wstrb),
        .s_axil_bvalid      (s_axil_bvalid),
        .s_axil_bready      (s_axil_bready),
        .s_axil_bresp       (s_axil_bresp),
        .s_axil_arvalid     (s_axil_arvalid),
        .s_axil_arready     (s_axil_arready),
        .s_axil_araddr      (s_axil_araddr),
        .s_axil_arprot      (s_axil_arprot),
        .s_axil_rvalid      (s_axil_rvalid),
        .s_axil_rready      (s_axil_rready),
        .s_axil_rdata       (s_axil_rdata),
        .s_axil_rresp       (s_axil_rresp),
        .s_axis_dma_tdata   (s_axis_dma_tdata),
        .s_axis_dma_tlast   (s_axis_dma_tlast),
        .s_axis_dma_tvalid  (s_axis_dma_tvalid),
        .s_axis_dma_tready  (s_axis_dma_tready),
        .m_axis_cmp_tdata   (m_axis_cmp_tdata),
        .m_axis_cmp_tkeep   (m_axis_cmp_tkeep),
        .m_axis_cmp_tlast   (m_axis_cmp_tlast),
        .m_axis_cmp_tvalid  (m_axis_cmp_tvalid),
        .m_axis_cmp_tready  (m_axis_cmp_tready),
        .m_axis_gr_tdata    (m_axis_gr_tdata),
        .m_axis_gr_tkeep    (m_axis_gr_tkeep),
        .m_axis_gr_tlast    (m_axis_gr_tlast),
        .m_axis_gr_tvalid   (m_axis_gr_tvalid),
        .m_axis_gr_tready   (m_axis_gr_tready),
        .gt_tx_done         (gt_tx_done),
        .gt_rx_done         (gt_rx_done),
        .gt_cplllock        (gt_cplllock),
        .gt_powergood       (gt_powergood),
        .gt_reset_all       (gt_reset_all),
        .gt_reset_rx        (gt_reset_rx),
        .sfp_tx_enable      (sfp_tx_enable),
        .clk_link           (clk_link),
        .gt_txdata          (gt_txdata),
        .gt_txcharisk       (gt_txcharisk),
        .gt_prbs_sel        (gt_prbs_sel),
        .gt_prbs_force_err  (gt_prbs_force_err),
        .gt_prbs_cnt_reset  (gt_prbs_cnt_reset),
        .gt_loopback        (gt_loopback),
        .gt_txpolarity      (gt_txpolarity),
        .gt_rxpolarity      (gt_rxpolarity),
        .gt_rxbufreset      (gt_rxbufreset),
        .gt_rxdata          (gt_rxdata),
        .gt_rxcharisk       (gt_rxcharisk),
        .gt_rxdisperr       (gt_rxdisperr),
        .gt_rxnotintable    (gt_rxnotintable),
        .gt_rxbyteisaligned (gt_rxbyteisaligned),
        .gt_rxprbserr       (gt_rxprbserr),
        .gt_rxprbslocked    (gt_rxprbslocked),
        .gt_rxclkcorcnt     (gt_rxclkcorcnt),
        .gt_rxbufstatus     (gt_rxbufstatus),
        .clk_sem            (clk_sem),
        .sem_status         (sem_status),
        .sem_cmd_busy       (sem_cmd_busy),
        .sem_icap_avail     (sem_icap_avail),
        .sem_mon_data       (sem_mon_data),
        .sem_mon_write      (sem_mon_write),
        .sem_cmd_strobe     (sem_cmd_strobe),
        .sem_cmd_code       (sem_cmd_code),
        .sem_grant          (sem_grant),
        .irq                (irq)
    );
endmodule
