`timescale 1ns / 1ps

// Ground receiver frame synchronizer (SPEC.md 6.10, GR-01..GR-05), as
// channel.frame_sync(): byte-aligned ASM search with up to asm_tol wrong bits,
// SEARCH -> VERIFY (two ASMs one CADU apart) -> LOCK, coasting through up to
// `flywheel` missed ASMs (FLYWHEEL) before returning to SEARCH.
//
// The received bytes run through a 1024-byte delay line with an ASM
// correlator at each end, so position p and p + 1024 are judged together and
// each position is examined in turn (the model's search loop). A delivered
// CADU leaves the delay line right after its decision: the 1020 codeblock
// bytes are derandomized and followed by the status word (little-endian:
// [5:0] ASM bit errors, [7:6] sync state, [31:16] sequence number), giving a
// 1024-byte record (tlast on its last byte).
//
// The input cannot be held back (it comes from the link). Records go through
// a 2-record output FIFO; a record that does not fit when its CADU is found
// is dropped and counted.
module frame_sync import downlink_pkg::*; (
    input  logic        clk,
    input  logic        rst,
    input  logic [5:0]  asm_tol,
    input  logic [3:0]  flywheel,
    input  logic        cnt_clr,                // clear the counters

    input  logic [7:0]  s_tdata,
    input  logic        s_tvalid,

    output logic [7:0]  m_axis_tdata,
    output logic        m_axis_tlast,           // last byte of a record
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic [1:0]  sync_state,             // 0 SEARCH, 1 VERIFY, 2 LOCK, 3 FLYWHEEL
    output logic [31:0] cadus,                  // records delivered
    output logic [31:0] sync_losses,
    output logic [31:0] asm_bit_errors,         // over delivered CADUs
    output logic [31:0] dropped_records
);
    localparam logic [1:0] SEARCH = 2'd0, VERIFY = 2'd1, LOCK = 2'd2, FLYWHEEL = 2'd3;
    localparam int OQ = 2048;                   // output FIFO, bytes

    function automatic logic [5:0] asm_err(input logic [31:0] w);
        logic [5:0] n = '0;
        for (int b = 0; b < 32; b++) n += 6'(w[b] ^ ASM[b]);
        return n;
    endfunction

    // ---------------------------------------------------------------------
    // A: input byte i -> delay line, head window (positions i-3 .. i)
    // ---------------------------------------------------------------------
    (* ram_style = "block" *) logic [7:0] dl [1024];
    logic [9:0]  wp;
    logic [31:0] head;
    logic [10:0] fill;                          // bytes seen, saturates at 1027
    logic [7:0]  dl_q;
    logic        vb, vc;
    logic [5:0]  e_head_b, e_head_c, e_tail_c;

    always_ff @(posedge clk) begin
        if (s_tvalid) begin
            dl_q   <= dl[wp];                   // byte i - 1024 (read first)
            dl[wp] <= s_tdata;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            wp   <= '0;
            fill <= '0;
            vb   <= 1'b0;
            head <= '0;
        end else begin
            vb <= s_tvalid;
            if (s_tvalid) begin
                wp       <= wp + 10'd1;
                head     <= {head[23:0], s_tdata};
                e_head_b <= asm_err({head[23:0], s_tdata});
                if (fill != 11'd1027) fill <= fill + 11'd1;
            end
        end
    end

    // ---------------------------------------------------------------------
    // B: byte i - 1024 leaves -> tail window (positions i-1027 .. i-1024)
    // ---------------------------------------------------------------------
    logic [31:0] tail;
    logic [7:0]  out_b, out_c;
    logic        full_b, full_c;

    always_ff @(posedge clk) begin
        if (rst) begin
            vc   <= 1'b0;
            tail <= '0;
        end else begin
            vc <= vb;
            if (vb) begin
                tail     <= {tail[23:0], dl_q};
                e_tail_c <= asm_err({tail[23:0], dl_q});
                e_head_c <= e_head_b;
                out_c    <= dl_q;
                full_c   <= (fill == 11'd1027);
            end
        end
    end

    // ---------------------------------------------------------------------
    // C: decision for position p = i - 1027 (the oldest ASM candidate);
    //    out_c is byte p + 3. A delivered CADU's codeblock is bytes p+4 ...
    // ---------------------------------------------------------------------
    logic [1:0]  state;
    logic [3:0]  misses;
    logic [9:0]  pos;                           // position within the current CADU (LOCK)
    logic        emit;                          // codeblock bytes leaving are output
    logic [9:0]  left;                          // codeblock bytes still to output
    logic [7:0]  pn;
    logic [15:0] seq;
    logic [2:0]  st_i;                          // status bytes to append (4 .. 1), 0 none
    logic [31:0] status;
    logic [$clog2(OQ):0] oq_count;

    wire tail_ok = (e_tail_c <= asm_tol);
    wire head_ok = (e_head_c <= asm_tol);
    // room for a whole record (margin: a status word in flight, count latency)
    wire room    = (oq_count + CADU_BYTES + 16 <= OQ);

    // output FIFO write port
    logic       oq_wr, oq_last;
    logic [7:0] oq_din;

    assign sync_state = state;

    always_ff @(posedge clk) begin
        if (rst) begin
            state   <= SEARCH;
            misses  <= '0;
            pos     <= '0;
            emit    <= 1'b0;
            left    <= '0;
            seq     <= '0;
            st_i    <= '0;
            oq_wr   <= 1'b0;
            cadus <= '0;  sync_losses <= '0;  asm_bit_errors <= '0;  dropped_records <= '0;
        end else begin
            logic       deliver;
            logic [1:0] dstate;
            deliver = 1'b0;
            dstate  = state;
            oq_wr   <= 1'b0;

            // status word after the codeblock (no codeblock byte is due then:
            // the next CADU's ASM bytes are passing)
            if (st_i != 3'd0) begin
                oq_wr   <= 1'b1;
                oq_din  <= status[8 * (4 - st_i) +: 8];
                oq_last <= (st_i == 3'd1);
                st_i    <= st_i - 3'd1;
            end

            if (vc && full_c) begin
                // codeblock byte leaving the delay line
                if (emit) begin
                    oq_wr   <= 1'b1;
                    oq_din  <= out_c ^ pn_byte(pn);
                    oq_last <= 1'b0;
                    pn      <= pn_next(pn);
                    left    <= left - 10'd1;
                    if (left == 10'd1) begin
                        emit <= 1'b0;
                        st_i <= 3'd4;
                    end
                end

                if (state == SEARCH) begin
                    if (tail_ok && head_ok) begin
                        deliver = 1'b1;
                        dstate  = VERIFY;
                        state   <= LOCK;
                        misses  <= '0;
                    end
                end else if (pos == 10'd0) begin
                    // CADU boundary in LOCK / FLYWHEEL
                    if (tail_ok) begin
                        deliver = 1'b1;
                        dstate  = LOCK;
                        state   <= LOCK;
                        misses  <= '0;
                    end else if (misses + 4'd1 > flywheel) begin
                        state       <= SEARCH;
                        misses      <= '0;
                        sync_losses <= sync_losses + 32'd1;
                    end else begin
                        deliver = 1'b1;
                        dstate  = FLYWHEEL;
                        state   <= FLYWHEEL;
                        misses  <= misses + 4'd1;
                    end
                end

                // position in the CADU: a CADU found in SEARCH starts here, so the
                // next position is 1; in LOCK / FLYWHEEL it counts on (mod 1024)
                pos <= (state == SEARCH) ? 10'd1 : pos + 10'd1;

                if (deliver) begin
                    if (room) begin
                        emit   <= 1'b1;
                        left   <= 10'(CODEBLOCK_BYTES);
                        pn     <= 8'hFF;
                        status <= {seq, 8'h00, dstate, (e_tail_c > 6'd63) ? 6'd63 : e_tail_c};
                        cadus          <= cadus + 32'd1;
                        asm_bit_errors <= asm_bit_errors + 32'(e_tail_c);
                    end else begin
                        dropped_records <= dropped_records + 32'd1;
                    end
                    seq <= seq + 16'd1;
                end
            end
            if (cnt_clr) begin
                cadus <= '0;  sync_losses <= '0;  asm_bit_errors <= '0;  dropped_records <= '0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Output FIFO
    // ---------------------------------------------------------------------
    logic       oq_empty, oq_full, oq_wr_busy, oq_rd_busy;
    logic [8:0] oq_dout;

    xpm_fifo_sync #(
        .FIFO_MEMORY_TYPE    ("block"),
        .FIFO_WRITE_DEPTH    (OQ),
        .WRITE_DATA_WIDTH    (9),
        .READ_DATA_WIDTH     (9),
        .READ_MODE           ("fwft"),
        .FIFO_READ_LATENCY   (0),
        .USE_ADV_FEATURES    ("0004"),          // wr_data_count
        .WR_DATA_COUNT_WIDTH ($clog2(OQ) + 1),
        .RD_DATA_COUNT_WIDTH ($clog2(OQ) + 1),
        .PROG_FULL_THRESH    (10),
        .PROG_EMPTY_THRESH   (10),
        .DOUT_RESET_VALUE    ("0"),
        .FULL_RESET_VALUE    (0),
        .ECC_MODE            ("no_ecc"),
        .SIM_ASSERT_CHK      (0),
        .WAKEUP_TIME         (0)
    ) u_out (
        .sleep (1'b0), .rst (rst), .wr_clk (clk),
        .wr_en (oq_wr), .din ({oq_last, oq_din}), .full (oq_full), .wr_rst_busy (oq_wr_busy),
        .rd_en (m_axis_tvalid && m_axis_tready), .dout (oq_dout), .empty (oq_empty),
        .rd_rst_busy (oq_rd_busy),
        .prog_full (), .wr_data_count (oq_count), .overflow (), .almost_full (), .wr_ack (),
        .prog_empty (), .rd_data_count (), .underflow (), .almost_empty (), .data_valid (),
        .injectsbiterr (1'b0), .injectdbiterr (1'b0), .sbiterr (), .dbiterr ()
    );

    assign m_axis_tvalid = !oq_empty && !oq_rd_busy;
    assign m_axis_tdata  = oq_dout[7:0];
    assign m_axis_tlast  = oq_dout[8];
endmodule
