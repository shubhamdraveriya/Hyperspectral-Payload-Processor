`timescale 1ns / 1ps

// Space packetizer (SPEC.md 6.5, CCSDS 133.0): splits each compressed scene
// into packets of pkt_data user bytes (the last one may be shorter), each with
// a 6-byte primary header and an 8-byte secondary header (scene ID, packet
// index, scene start timestamp, flags), as framing.packetize_scene().
//
// Store and forward: the length field precedes the data, so a packet's payload
// is buffered (up to 2 * PKT_MAX bytes) before its header goes out. The scene
// timestamps arrive on s_meta, one per scene in scene order (taken when the
// scene entered the front end).
//
// Flags: bit 1 = a line was dropped (lines_dropped pulse) since the scene's
// first packet header; bit 2 = RAW mode. The ground ORs them over the scene.
//
// Output: m_axis_tuser[0] first byte of a packet, tuser[1] packet is the last
// of its scene (every byte), tlast last byte of a packet.
module space_packetizer import downlink_pkg::*; #(
    parameter int PKT_MAX = 2048                // largest pkt_data, power of two
) (
    input  logic        clk,
    input  logic        rst,
    input  logic [10:0] apid,
    input  logic [$clog2(PKT_MAX):0] pkt_data,  // user bytes per packet, 1..PKT_MAX
    input  logic        raw_mode,
    input  logic        lines_dropped,
    input  logic        cnt_clr,                // clear packets / scenes

    input  logic [63:0] s_axis_tdata,           // first byte in [7:0]
    input  logic [7:0]  s_axis_tkeep,           // contiguous from byte 0
    input  logic        s_axis_tlast,           // end of scene
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    input  logic [23:0] s_meta_tdata,           // scene start timestamp
    input  logic        s_meta_tvalid,
    output logic        s_meta_tready,

    output logic [7:0]  m_axis_tdata,
    output logic [1:0]  m_axis_tuser,
    output logic        m_axis_tlast,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic [31:0] packets,
    output logic [31:0] scenes
);
    localparam int LB = $clog2(PKT_MAX) + 1;    // packet length bits

    // ---------------------------------------------------------------------
    // 64 -> 8 unpacker
    // ---------------------------------------------------------------------
    logic [63:0] w;
    logic [7:0]  wkeep;
    logic        wlast, wvalid;
    logic [2:0]  bi;
    logic [3:0]  nbytes;

    always_comb begin
        nbytes = '0;
        for (int b = 0; b < 8; b++) nbytes += 4'(wkeep[b]);
    end

    wire [7:0] byte_in   = w[8 * bi +: 8];
    wire       last_of_w = (4'(bi) + 4'd1 >= nbytes);
    wire       scene_end = wlast && last_of_w;

    // ---------------------------------------------------------------------
    // Packet assembly: payload FIFO + one descriptor per packet
    // ---------------------------------------------------------------------
    logic          pf_full, pf_empty, pf_wr_busy, pf_rd_busy, pf_rd;
    logic [7:0]    pf_dout;
    logic          dq_full, dq_empty, dq_rd;
    logic [LB:0]   dq_dout;                     // {last of scene, length}
    logic [LB-1:0] count;

    wire close    = (count + 1'b1 == pkt_data) || scene_end;
    wire take     = wvalid && !pf_full && !pf_wr_busy && !(close && dq_full);
    assign s_axis_tready = !wvalid || (take && last_of_w);

    always_ff @(posedge clk) begin
        if (rst) begin
            wvalid <= 1'b0;
            bi     <= '0;
            count  <= '0;
        end else begin
            if (s_axis_tvalid && s_axis_tready) begin
                w      <= s_axis_tdata;
                wkeep  <= s_axis_tkeep;
                wlast  <= s_axis_tlast;
                wvalid <= 1'b1;
                bi     <= '0;
            end else if (take) begin
                if (last_of_w) wvalid <= 1'b0;
                bi <= bi + 3'd1;
            end
            if (take) count <= close ? '0 : count + 1'b1;
        end
    end

    xpm_fifo_sync #(
        .FIFO_MEMORY_TYPE    ("block"),
        .FIFO_WRITE_DEPTH    (2 * PKT_MAX),
        .WRITE_DATA_WIDTH    (8),
        .READ_DATA_WIDTH     (8),
        .READ_MODE           ("fwft"),
        .FIFO_READ_LATENCY   (0),
        .USE_ADV_FEATURES    ("0000"),
        .WR_DATA_COUNT_WIDTH ($clog2(2 * PKT_MAX) + 1),
        .RD_DATA_COUNT_WIDTH ($clog2(2 * PKT_MAX) + 1),
        .PROG_FULL_THRESH    (10),
        .PROG_EMPTY_THRESH   (10),
        .DOUT_RESET_VALUE    ("0"),
        .FULL_RESET_VALUE    (0),
        .ECC_MODE            ("no_ecc"),
        .SIM_ASSERT_CHK      (0),
        .WAKEUP_TIME         (0)
    ) u_payload (
        .sleep (1'b0), .rst (rst), .wr_clk (clk),
        .wr_en (take), .din (byte_in), .full (pf_full), .wr_rst_busy (pf_wr_busy),
        .rd_en (pf_rd), .dout (pf_dout), .empty (pf_empty), .rd_rst_busy (pf_rd_busy),
        .prog_full (), .wr_data_count (), .overflow (), .almost_full (), .wr_ack (),
        .prog_empty (), .rd_data_count (), .underflow (), .almost_empty (), .data_valid (),
        .injectsbiterr (1'b0), .injectdbiterr (1'b0), .sbiterr (), .dbiterr ()
    );

    sync_fifo #(.WIDTH(LB + 1), .DEPTH(8)) u_desc (
        .clk   (clk),
        .rst   (rst),
        .din   ({scene_end, LB'(count + 1'b1)}),
        .wr_en (take && close),
        .rd_en (dq_rd),
        .dout  (dq_dout),
        .empty (dq_empty),
        .full  (dq_full),
        .count ()
    );

    // ---------------------------------------------------------------------
    // Packet output: 14 header bytes, then the payload
    // ---------------------------------------------------------------------
    logic          busy, in_hdr, plast;
    logic [3:0]    hi;                          // header byte index
    logic [LB-1:0] left;                        // payload bytes still to send
    logic [7:0]    hdr [14];
    logic [13:0]   seq_count;
    logic [15:0]   scene_id, pkt_index;
    logic [23:0]   ts;
    logic          drop_flag;

    wire first_pkt = (pkt_index == 16'd0);
    wire start     = !busy && !dq_empty && (!first_pkt || s_meta_tvalid);
    wire advance   = !m_axis_tvalid || m_axis_tready;
    wire send_hdr  = busy && in_hdr && advance;
    wire send_pay  = busy && !in_hdr && advance && !pf_empty && !pf_rd_busy;

    assign dq_rd         = start;
    assign s_meta_tready = start && first_pkt;
    assign pf_rd         = send_pay;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy          <= 1'b0;
            m_axis_tvalid <= 1'b0;
            seq_count     <= '0;
            scene_id      <= '0;
            pkt_index     <= '0;
            drop_flag     <= 1'b0;
            packets       <= '0;
            scenes        <= '0;
        end else begin
            if (lines_dropped) drop_flag <= 1'b1;
            if (advance) m_axis_tvalid <= 1'b0;

            if (start) begin
                logic [LB-1:0] len;
                logic [15:0]   dlen;
                logic [23:0]   t;
                logic          d;
                len  = dq_dout[LB-1:0];
                dlen = 16'(len) + 16'(SECONDARY_HDR - 1);
                t    = first_pkt ? s_meta_tdata : ts;
                d    = first_pkt ? lines_dropped : (drop_flag || lines_dropped);
                if (first_pkt) begin
                    ts        <= s_meta_tdata;
                    drop_flag <= lines_dropped;
                end
                hdr[0]  <= {3'b000, 1'b0, 1'b1, apid[10:8]};
                hdr[1]  <= apid[7:0];
                hdr[2]  <= {dq_dout[LB], first_pkt, seq_count[13:8]};
                hdr[3]  <= seq_count[7:0];
                hdr[4]  <= dlen[15:8];
                hdr[5]  <= dlen[7:0];
                hdr[6]  <= scene_id[15:8];
                hdr[7]  <= scene_id[7:0];
                hdr[8]  <= pkt_index[15:8];
                hdr[9]  <= pkt_index[7:0];
                hdr[10] <= t[23:16];
                hdr[11] <= t[15:8];
                hdr[12] <= t[7:0];
                hdr[13] <= {5'b00000, raw_mode, d, 1'b0};
                busy   <= 1'b1;
                in_hdr <= 1'b1;
                hi     <= '0;
                left   <= len;
                plast  <= dq_dout[LB];
            end

            if (send_hdr) begin
                m_axis_tvalid <= 1'b1;
                m_axis_tdata  <= hdr[hi];
                m_axis_tuser  <= {plast, hi == 4'd0};
                m_axis_tlast  <= 1'b0;
                hi            <= hi + 4'd1;
                if (hi == 4'(PRIMARY_HDR + SECONDARY_HDR - 1)) in_hdr <= 1'b0;
            end

            if (send_pay) begin
                m_axis_tvalid <= 1'b1;
                m_axis_tdata  <= pf_dout;
                m_axis_tuser  <= {plast, 1'b0};
                m_axis_tlast  <= (left == LB'(1));
                left          <= left - 1'b1;
                if (left == LB'(1)) begin
                    busy      <= 1'b0;
                    seq_count <= seq_count + 14'd1;
                    packets   <= packets + 32'd1;
                    if (plast) begin
                        pkt_index <= '0;
                        scene_id  <= scene_id + 16'd1;
                        scenes    <= scenes + 32'd1;
                    end else begin
                        pkt_index <= pkt_index + 16'd1;
                    end
                end
            end
            if (cnt_clr) begin
                packets <= '0;
                scenes  <= '0;
            end
        end
    end
endmodule
