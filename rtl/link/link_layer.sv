`timescale 1ns / 1ps

// Link layer between the downlink chain (clk_sys, byte streams) and the GTH
// user interface (clk_link, 62.5 MHz, 16-bit words), SPEC.md 6.9:
//
//   TX: bytes -> pairs (first byte in [7:0]) -> xpm_fifo_async -> link_tx -> GTH
//       (the FIFO back-pressures the chain: the link is the rate limit)
//   RX: GTH -> link_rx -> xpm_fifo_async -> bytes (clk_sys drains faster than
//       the link fills; an overflow would be counted)
//
// Status outputs are in the clk_link domain.
module link_layer (
    input  logic        clk_sys,
    input  logic        rst_sys,
    input  logic        clk_link,
    input  logic        rst_link,
    input  logic [15:0] cc_interval,            // clk_link domain, static
    input  logic        cnt_clr,                // clk_link domain: clear the counters

    input  logic [7:0]  s_axis_tdata,           // CADU bytes to send
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    output logic [7:0]  m_tdata,                // received bytes (cannot be held back)
    output logic        m_tvalid,

    output logic [15:0] txdata,
    output logic [1:0]  txcharisk,
    input  logic [15:0] rxdata,
    input  logic [1:0]  rxcharisk,
    input  logic [1:0]  rxdisperr,
    input  logic [1:0]  rxnotintable,
    input  logic        rxbyteisaligned,

    output logic        locked,
    output logic [31:0] code_errors,
    output logic [31:0] lock_losses,
    output logic [31:0] rx_words,
    output logic [31:0] tx_data_words,
    output logic [31:0] tx_idle_words,
    output logic [31:0] rx_overflows
);
    // ---------------------------------------------------------------------
    // TX: byte pairs -> FIFO -> link_tx
    // ---------------------------------------------------------------------
    logic        have;
    logic [7:0]  b0;
    logic        tf_full, tf_wr_busy, tf_empty, tf_rd_busy, tf_rd;
    logic [15:0] tf_dout;

    wire tf_room = !tf_full && !tf_wr_busy;
    wire tf_wr   = s_axis_tvalid && have && tf_room;
    assign s_axis_tready = !have || tf_room;

    always_ff @(posedge clk_sys) begin
        if (rst_sys) begin
            have <= 1'b0;
        end else if (s_axis_tvalid && s_axis_tready) begin
            have <= !have;
            if (!have) b0 <= s_axis_tdata;
        end
    end

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE ("block"), .FIFO_WRITE_DEPTH (512), .WRITE_DATA_WIDTH (16),
        .READ_DATA_WIDTH (16), .READ_MODE ("fwft"), .FIFO_READ_LATENCY (0), .CDC_SYNC_STAGES (2),
        .USE_ADV_FEATURES ("0000"), .WR_DATA_COUNT_WIDTH (10), .RD_DATA_COUNT_WIDTH (10),
        .PROG_FULL_THRESH (10), .PROG_EMPTY_THRESH (10), .DOUT_RESET_VALUE ("0"),
        .FULL_RESET_VALUE (0), .ECC_MODE ("no_ecc"), .RELATED_CLOCKS (0), .SIM_ASSERT_CHK (0),
        .WAKEUP_TIME (0)
    ) u_tx_fifo (
        .sleep (1'b0), .rst (rst_sys),
        .wr_clk (clk_sys), .wr_en (tf_wr), .din ({s_axis_tdata, b0}), .full (tf_full),
        .wr_rst_busy (tf_wr_busy),
        .rd_clk (clk_link), .rd_en (tf_rd), .dout (tf_dout), .empty (tf_empty),
        .rd_rst_busy (tf_rd_busy),
        .prog_full (), .wr_data_count (), .overflow (), .almost_full (), .wr_ack (),
        .prog_empty (), .rd_data_count (), .underflow (), .almost_empty (), .data_valid (),
        .injectsbiterr (1'b0), .injectdbiterr (1'b0), .sbiterr (), .dbiterr ()
    );

    link_tx u_tx (
        .clk (clk_link), .rst (rst_link), .cc_interval, .cnt_clr,
        .s_data (tf_dout), .s_empty (tf_empty || tf_rd_busy), .s_rd (tf_rd),
        .txdata, .txcharisk,
        .data_words (tx_data_words), .idle_words (tx_idle_words)
    );

    // ---------------------------------------------------------------------
    // RX: link_rx -> FIFO -> bytes
    // ---------------------------------------------------------------------
    logic [15:0] rx_data;
    logic        rx_wr, rf_full, rf_wr_busy, rf_empty, rf_rd_busy, rf_rd;
    logic [15:0] rf_dout;

    link_rx u_rx (
        .clk (clk_link), .rst (rst_link),
        .rxdata, .rxcharisk, .rxdisperr, .rxnotintable, .rxbyteisaligned, .cnt_clr,
        .m_data (rx_data), .m_wr (rx_wr),
        .locked, .code_errors, .lock_losses, .data_words (rx_words)
    );

    always_ff @(posedge clk_link) begin
        if (rst_link || cnt_clr)
            rx_overflows <= '0;
        else if (rx_wr && (rf_full || rf_wr_busy))
            rx_overflows <= rx_overflows + 32'd1;
    end

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE ("block"), .FIFO_WRITE_DEPTH (512), .WRITE_DATA_WIDTH (16),
        .READ_DATA_WIDTH (16), .READ_MODE ("fwft"), .FIFO_READ_LATENCY (0), .CDC_SYNC_STAGES (2),
        .USE_ADV_FEATURES ("0000"), .WR_DATA_COUNT_WIDTH (10), .RD_DATA_COUNT_WIDTH (10),
        .PROG_FULL_THRESH (10), .PROG_EMPTY_THRESH (10), .DOUT_RESET_VALUE ("0"),
        .FULL_RESET_VALUE (0), .ECC_MODE ("no_ecc"), .RELATED_CLOCKS (0), .SIM_ASSERT_CHK (0),
        .WAKEUP_TIME (0)
    ) u_rx_fifo (
        .sleep (1'b0), .rst (rst_link),
        .wr_clk (clk_link), .wr_en (rx_wr && !rf_full && !rf_wr_busy), .din (rx_data),
        .full (rf_full), .wr_rst_busy (rf_wr_busy),
        .rd_clk (clk_sys), .rd_en (rf_rd), .dout (rf_dout), .empty (rf_empty),
        .rd_rst_busy (rf_rd_busy),
        .prog_full (), .wr_data_count (), .overflow (), .almost_full (), .wr_ack (),
        .prog_empty (), .rd_data_count (), .underflow (), .almost_empty (), .data_valid (),
        .injectsbiterr (1'b0), .injectdbiterr (1'b0), .sbiterr (), .dbiterr ()
    );

    // two bytes per word, first byte [7:0]
    logic hi;                                   // next byte is [15:8]
    assign rf_rd = !rf_empty && !rf_rd_busy && hi;

    always_ff @(posedge clk_sys) begin
        if (rst_sys) begin
            hi       <= 1'b0;
            m_tvalid <= 1'b0;
        end else begin
            m_tvalid <= !rf_empty && !rf_rd_busy;
            if (!rf_empty && !rf_rd_busy) begin
                m_tdata <= hi ? rf_dout[15:8] : rf_dout[7:0];
                hi      <= !hi;
            end
        end
    end
endmodule
