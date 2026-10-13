`timescale 1ns / 1ps

// AOS framer (SPEC.md 6.6, CCSDS 732.0, M_PDU), as framing.AosFramer:
// 892-byte frames = 6-byte header, 2-byte M_PDU header (first header
// pointer), 884-byte packet zone. Packets may span frames.
//
// Input side: packet bytes fill the zone of one of two frame buffers. After
// the last packet of a scene the frame is completed with an idle packet
// (APID 0x7FF, fill 0x55); if fewer than 7 bytes are left the idle packet
// spills over and fills the next frame too (frames(flush=True)).
// Output side: complete science frames in order; with idle_en, an idle frame
// (VCID 63, fill 0x55, own frame count) whenever the output can take a frame
// and no science frame may go. frame_gap > 0 spaces science frame starts by
// at least that many clock cycles (a lower downlink capacity, AOS-02).
//
// Input: tuser[0] first byte of a packet, tuser[1] packet is the last of its
// scene, tlast last byte of a packet. Output: tlast last byte of a frame.
module aos_framer import downlink_pkg::*; (
    input  logic        clk,
    input  logic        rst,
    input  logic [7:0]  scid,
    input  logic [5:0]  vcid,
    input  logic        idle_en,
    input  logic [31:0] frame_gap,
    input  logic        cnt_clr,                // clear the frame counters

    input  logic [7:0]  s_axis_tdata,
    input  logic [1:0]  s_axis_tuser,
    input  logic        s_axis_tlast,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    output logic [7:0]  m_axis_tdata,
    output logic        m_axis_tlast,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic [31:0] science_frames,
    output logic [31:0] idle_frames,
    output logic        tmr_mismatch    // the voted state copies disagree (RAD-04)
);
    localparam int OQ = 8;                      // output FIFO
    localparam int RL = 2;                      // zone RAM read latency

    // ---------------------------------------------------------------------
    // Input side: zone assembly
    // ---------------------------------------------------------------------
    (* ram_style = "block" *) logic [7:0] zone [2048];

    logic        wb;                            // buffer being filled
    logic [9:0]  wp;                            // next zone position
    logic [10:0] fhp;                           // first packet start in this zone
    logic [1:0]  full;
    logic [10:0] fhp_r [2];
    logic [23:0] vcc_r [2];
    logic [23:0] vc_count;

    // idle packet after a scene: hdr bytes 0-5, then fill
    logic        pad;                           // writing the idle packet
    logic [10:0] pad_i, pad_len;                // byte index, total length (7..890)

    logic [7:0]  pad_byte;
    always_comb begin
        logic [10:0] dl;
        dl = pad_len - 11'(PRIMARY_HDR + 1);    // data length field
        case (pad_i)
            11'd0:   pad_byte = {5'b00000, IDLE_APID[10:8]};
            11'd1:   pad_byte = IDLE_APID[7:0];
            11'd2:   pad_byte = 8'hC0;          // unsegmented, count 0
            11'd3:   pad_byte = 8'h00;
            11'd4:   pad_byte = {5'b00000, dl[10:8]};
            11'd5:   pad_byte = dl[7:0];
            default: pad_byte = IDLE_FILL;
        endcase
    end

    wire        room      = !full[wb];
    wire        in_take   = !pad && room && s_axis_tvalid;
    wire        pad_take  = pad && room;
    wire        wr        = in_take || pad_take;
    wire [7:0]  wr_byte   = pad ? pad_byte : s_axis_tdata;
    wire        pkt_start = pad ? (pad_i == 11'd0) : s_axis_tuser[0];
    wire        zone_end  = (wp == 10'(ZONE_BYTES - 1));
    wire [10:0] fhp_now   = (fhp == FHP_NONE && pkt_start) ? 11'(wp) : fhp;
    wire [10:0] space     = 11'(ZONE_BYTES) - 11'(wp) - 11'd1;   // left after this byte

    assign s_axis_tready = !pad && room;

    logic rd_free;                              // output side releases a buffer
    logic rd_buf;

    always_ff @(posedge clk) begin
        if (wr) zone[{wb, wp}] <= wr_byte;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            wb       <= 1'b0;
            wp       <= '0;
            fhp      <= FHP_NONE;
            full     <= '0;
            vc_count <= '0;
            pad      <= 1'b0;
        end else begin
            if (wr) begin
                if (zone_end) begin
                    full[wb]  <= 1'b1;
                    fhp_r[wb] <= fhp_now;
                    vcc_r[wb] <= vc_count;
                    vc_count  <= vc_count + 24'd1;
                    wb        <= !wb;
                    wp        <= '0;
                    fhp       <= FHP_NONE;
                end else begin
                    wp  <= wp + 10'd1;
                    fhp <= fhp_now;
                end
            end
            // after the last packet of a scene, pad the frame with an idle packet
            if (in_take && s_axis_tlast && s_axis_tuser[1] && !zone_end) begin
                pad     <= 1'b1;
                pad_i   <= '0;
                pad_len <= (space >= 11'd7) ? space : space + 11'(ZONE_BYTES);
            end
            if (pad_take) begin
                pad_i <= pad_i + 11'd1;
                if (pad_i == pad_len - 11'd1) pad <= 1'b0;
            end
            if (rd_free) full[rd_buf] <= 1'b0;
        end
    end

    // ---------------------------------------------------------------------
    // Output side: one byte per cycle into a small FIFO (credit-based)
    // ---------------------------------------------------------------------
    typedef enum logic [1:0] {O_WAIT, O_SCI, O_IDLE} ostate_t;
    // output state: three voted copies, kept apart through synthesis (RAD-04)
    (* DONT_TOUCH = "true" *) logic [$bits(ostate_t)-1:0] os_0, os_1, os_2;
    ostate_t os;
    assign os = ostate_t'((os_0 & os_1) | (os_0 & os_2) | (os_1 & os_2));
    logic [9:0]  oi;                            // byte in frame, 0..891
    logic [23:0] idle_count;
    logic [31:0] gap_cnt;
    logic [$clog2(OQ):0] oq_count;
    logic [$clog2(RL + 1):0] inflight;

    wire credit = (oq_count + inflight) < OQ;
    wire sci_ok = full[rd_buf] && (gap_cnt == 32'd0);
    wire issue  = (os != O_WAIT) && credit;
    wire f_end  = (oi == 10'(FRAME_BYTES - 1));

    // byte to send: from registers now, or from the zone RAM RL cycles later
    logic [7:0] hb;
    logic       from_ram;
    always_comb begin
        logic [10:0] f;
        logic [23:0] c;
        logic [5:0]  v;
        f = fhp_r[rd_buf];
        c = (os == O_IDLE) ? idle_count : vcc_r[rd_buf];
        v = (os == O_IDLE) ? IDLE_VCID : vcid;
        from_ram = (os == O_SCI) && (oi >= 10'(FRAME_HDR + MPDU_HDR));
        case (oi)
            10'd0:   hb = {2'b01, scid[7:2]};
            10'd1:   hb = {scid[1:0], v};
            10'd2:   hb = c[23:16];
            10'd3:   hb = c[15:8];
            10'd4:   hb = c[7:0];
            10'd5:   hb = 8'h00;
            10'd6:   hb = (os == O_IDLE) ? IDLE_FILL : {5'b00000, f[10:8]};
            10'd7:   hb = (os == O_IDLE) ? IDLE_FILL : f[7:0];
            default: hb = IDLE_FILL;
        endcase
    end

    assign rd_free = issue && f_end && (os == O_SCI);

    always_ff @(posedge clk) begin
        {os_0, os_1, os_2} <= {3{os}};   // refresh (RAD-04)
        if (rst) begin
            {os_0, os_1, os_2} <= {3{O_WAIT}};
            rd_buf         <= 1'b0;
            oi             <= '0;
            idle_count     <= '0;
            gap_cnt        <= '0;
            science_frames <= '0;
            idle_frames    <= '0;
        end else begin
            if (gap_cnt != 32'd0) gap_cnt <= gap_cnt - 32'd1;
            if (os == O_WAIT) begin
                oi <= '0;
                if (sci_ok) begin
                    {os_0, os_1, os_2} <= {3{O_SCI}};
                    gap_cnt <= frame_gap;
                end else if (idle_en) begin
                    {os_0, os_1, os_2} <= {3{O_IDLE}};
                end
            end else if (issue) begin
                oi <= f_end ? 10'd0 : oi + 10'd1;
                if (f_end) begin
                    {os_0, os_1, os_2} <= {3{O_WAIT}};
                    if (os == O_SCI) begin
                        rd_buf         <= !rd_buf;
                        science_frames <= science_frames + 32'd1;
                    end else begin
                        idle_count  <= idle_count + 24'd1;
                        idle_frames <= idle_frames + 32'd1;
                    end
                end
            end
            if (cnt_clr) begin
                science_frames <= '0;
                idle_frames    <= '0;
            end
        end
    end

    // read pipeline (the zone RAM has a registered output)
    logic [7:0] q1, q2;
    logic       v [RL];
    logic       r [RL];                         // byte comes from the RAM
    logic [7:0] b [RL];
    logic       l [RL];

    always_ff @(posedge clk) begin
        q1 <= zone[{rd_buf, oi - 10'(FRAME_HDR + MPDU_HDR)}];
        q2 <= q1;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < RL; i++) v[i] <= 1'b0;
            inflight <= '0;
        end else begin
            v[0] <= issue;
            for (int i = 1; i < RL; i++) v[i] <= v[i - 1];
            inflight <= inflight + issue - v[RL - 1];
        end
        r[0] <= from_ram;
        b[0] <= hb;
        l[0] <= f_end;
        for (int i = 1; i < RL; i++) begin
            r[i] <= r[i - 1];
            b[i] <= b[i - 1];
            l[i] <= l[i - 1];
        end
    end

    logic       oq_empty, sk_ready;
    logic [8:0] oq_dout;

    sync_fifo #(.WIDTH(9), .DEPTH(OQ)) u_out (
        .clk   (clk),
        .rst   (rst),
        .din   ({l[RL - 1], r[RL - 1] ? q2 : b[RL - 1]}),
        .wr_en (v[RL - 1]),
        .rd_en (!oq_empty && sk_ready),
        .dout  (oq_dout),
        .empty (oq_empty),
        .full  (),
        .count (oq_count)
    );

    axis_skid_buffer #(.DATA_W(8), .USER_W(1)) u_skid (
        .clk      (clk),
        .rst      (rst),
        .s_tdata  (oq_dout[7:0]),
        .s_tuser  (1'b0),
        .s_tlast  (oq_dout[8]),
        .s_tvalid (!oq_empty),
        .s_tready (sk_ready),
        .m_tdata  (m_axis_tdata),
        .m_tuser  (),
        .m_tlast  (m_axis_tlast),
        .m_tvalid (m_axis_tvalid),
        .m_tready (m_axis_tready)
    );

    assign tmr_mismatch = (os_0 != os_1) || (os_0 != os_2);
endmodule
