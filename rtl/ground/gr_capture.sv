`timescale 1ns / 1ps

// Ground capture (SPEC.md 6.10, GR-04): frame sync records (1020 derandomized
// codeblock bytes + 4-byte status word, tlast on the last byte) packed into
// 64-bit words (first byte in [7:0]) for the ground DMA S2MM.
//
// - skip_idle: a record whose frame is an idle frame is not captured. The VCID
//   field (codeblock byte 1, before RS decoding) decides: nearer to 63 than to
//   the science VCID, in bits. With VCID 1 (distance 5 from 63) a frame is
//   misjudged only with 3 or more bit errors in those 6 bits, so channel errors
//   neither let idle frames through nor drop science frames.
// - The DMA runs in simple mode, so a transfer ends on tlast: tlast comes after
//   every records_per_xfer captured records. The last word of a record that
//   does not complete a group is held back until the next captured record
//   starts (then it goes out without tlast) or until no record has been
//   captured for 2^16 cycles (then it goes out with tlast, ending a partial
//   group). Records are never split across the decision.
// - capture = 0: records are consumed and discarded.
//
// A 16 KB FIFO absorbs DMA re-arm gaps; when it is nearly full the input is
// held back, and frame_sync drops (and counts) whole records.
module gr_capture (
    input  logic        clk,
    input  logic        rst,
    input  logic        capture,
    input  logic        skip_idle,
    input  logic [5:0]  sci_vcid,               // VCID of the science frames
    input  logic [15:0] records_per_xfer,       // >= 1
    input  logic        cnt_clr,

    input  logic [7:0]  s_axis_tdata,
    input  logic        s_axis_tlast,           // last byte of a record
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    output logic [63:0] m_axis_tdata,
    output logic [7:0]  m_axis_tkeep,
    output logic        m_axis_tlast,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic [31:0] captured,               // records sent
    output logic [31:0] skipped                 // idle records not captured
);
    localparam int DEPTH   = 2048;              // 64-bit words
    localparam int TIMEOUT = 16;                // log2 cycles

    logic [9:0]  bi;                            // byte in record
    logic [63:0] w;
    logic        fwd;                           // this record is captured
    logic [15:0] group;                         // records in the current transfer
    logic        held;
    logic [63:0] held_w;
    logic [TIMEOUT-1:0] idle_cnt;

    logic        f_wr, f_last, f_full, f_empty, f_wr_busy, f_rd_busy;
    logic [63:0] f_din;
    logic [64:0] f_dout;
    logic [$clog2(DEPTH):0] f_count;

    assign s_axis_tready = !f_wr_busy && (f_count < DEPTH - 8);
    wire take = s_axis_tvalid && s_axis_tready;

    function automatic logic [2:0] ones(input logic [5:0] x);
        ones = '0;
        for (int b = 0; b < 6; b++) ones += 3'(x[b]);
    endfunction

    // decision at byte 1 (VCID); the word containing it is written at byte 7
    wire        idle_like = ones(s_axis_tdata[5:0] ^ 6'd63) < ones(s_axis_tdata[5:0] ^ sci_vcid);
    wire        dec_fwd   = capture && !(skip_idle && idle_like);
    wire        rec_fwd   = (bi == 10'd1) ? dec_fwd : fwd;
    wire [63:0] w_next    = (bi[2:0] == 3'd0) ? {56'd0, s_axis_tdata}
                                              : w | (64'(s_axis_tdata) << (8 * bi[2:0]));
    wire        word_done = take && bi[2:0] == 3'd7 && rec_fwd;
    wire        group_end = (group + 16'd1 >= records_per_xfer);
    wire        timeout   = held && (&idle_cnt);

    always_comb begin
        f_wr   = 1'b0;
        f_din  = w_next;
        f_last = 1'b0;
        if (word_done) begin
            // a record's last word completing a group goes out with tlast; one
            // that does not is held back
            f_wr   = !(s_axis_tlast && !group_end);
            f_last = s_axis_tlast;
        end else if (held && ((take && bi == 10'd1 && dec_fwd) || timeout)) begin
            // the held word: the next record is captured (no tlast), or timeout (tlast)
            f_wr   = 1'b1;
            f_din  = held_w;
            f_last = timeout && !(take && bi == 10'd1 && dec_fwd);
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            bi       <= '0;
            fwd      <= 1'b0;
            group    <= '0;
            held     <= 1'b0;
            idle_cnt <= '0;
            captured <= '0;
            skipped  <= '0;
        end else begin
            if (take) begin
                w  <= w_next;
                bi <= s_axis_tlast ? 10'd0 : bi + 10'd1;
                if (bi == 10'd1) begin
                    fwd <= dec_fwd;
                    if (skip_idle && idle_like) skipped <= skipped + 32'd1;
                end
            end

            if (held) idle_cnt <= idle_cnt + 1'b1;

            // held word released
            if (f_wr && !word_done) begin
                held <= 1'b0;
                if (f_last) group <= '0;
            end
            // record complete
            if (word_done && s_axis_tlast) begin
                captured <= captured + 32'd1;
                if (group_end) begin
                    group <= '0;
                end else begin
                    group    <= group + 16'd1;
                    held     <= 1'b1;
                    held_w   <= w_next;
                    idle_cnt <= '0;
                end
            end

            if (cnt_clr) begin
                captured <= '0;
                skipped  <= '0;
            end
        end
    end

    xpm_fifo_sync #(
        .FIFO_MEMORY_TYPE    ("block"),
        .FIFO_WRITE_DEPTH    (DEPTH),
        .WRITE_DATA_WIDTH    (65),
        .READ_DATA_WIDTH     (65),
        .READ_MODE           ("fwft"),
        .FIFO_READ_LATENCY   (0),
        .USE_ADV_FEATURES    ("0004"),          // wr_data_count
        .WR_DATA_COUNT_WIDTH ($clog2(DEPTH) + 1),
        .RD_DATA_COUNT_WIDTH ($clog2(DEPTH) + 1),
        .PROG_FULL_THRESH    (10),
        .PROG_EMPTY_THRESH   (10),
        .DOUT_RESET_VALUE    ("0"),
        .FULL_RESET_VALUE    (0),
        .ECC_MODE            ("no_ecc"),
        .SIM_ASSERT_CHK      (0),
        .WAKEUP_TIME         (0)
    ) u_fifo (
        .sleep (1'b0), .rst (rst), .wr_clk (clk),
        .wr_en (f_wr), .din ({f_last, f_din}), .full (f_full), .wr_rst_busy (f_wr_busy),
        .rd_en (m_axis_tvalid && m_axis_tready), .dout (f_dout), .empty (f_empty),
        .rd_rst_busy (f_rd_busy),
        .prog_full (), .wr_data_count (f_count), .overflow (), .almost_full (), .wr_ack (),
        .prog_empty (), .rd_data_count (), .underflow (), .almost_empty (), .data_valid (),
        .injectsbiterr (1'b0), .injectdbiterr (1'b0), .sbiterr (), .dbiterr ()
    );

    assign m_axis_tvalid = !f_empty && !f_rd_busy;
    assign m_axis_tdata  = f_dout[63:0];
    assign m_axis_tlast  = f_dout[64];
    assign m_axis_tkeep  = 8'hFF;
endmodule
