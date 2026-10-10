`timescale 1ns / 1ps

// Corner turn (SPEC.md 6.3): reorders each line from BIL (band-major) to BIP
// (pixel-major) through two line buffers (ping-pong).
//
// Write side: never stalls. A line is written to the free buffer, sample i of
// the BIL line at address i = z*Nx + x. If no buffer is free when a line
// starts, the line is dropped (CT-04).
// Read side: AXI4-Stream, may be back-pressured. Reads x outer, z inner, i.e.
// address z*Nx + x, as each completed line's turn comes.
//
// Every line, written or dropped, queues a descriptor in arrival order. A
// dropped line is sent as a fill line of zeros, so scenes keep their geometry
// and the compressor stays aligned with scene boundaries; the drop is counted
// (SYS-05). If the descriptor queue is full too, the line is lost without fill
// (counted separately; the scene is then corrupt).
//
// Output: tuser[0] first sample of a scene, tuser[1] first sample of a line,
// tlast last sample of a line. cfg must be static while lines flow.
module corner_turn import frontend_pkg::*; #(
    parameter int NX_MAX = 512,
    parameter int NZ_MAX = 256
) (
    input  logic        clk,
    input  logic        rst,
    input  fe_cfg_t     cfg,

    input  logic        s_valid,
    input  logic [15:0] s_data,
    input  logic [1:0]  s_user,         // [0] first sample of a scene, [1] first of a line
    input  logic        s_last,

    output logic [15:0] m_axis_tdata,
    output logic [1:0]  m_axis_tuser,
    output logic        m_axis_tlast,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,

    output logic [31:0] lines_out,
    output logic [31:0] dropped_lines,  // includes unfilled_lines
    output logic [31:0] unfilled_lines
);
    localparam int EB = $clog2(NX_MAX * NZ_MAX);
    localparam int DQ = 16;             // line descriptors
    localparam int OQ = 16;             // output FIFO
    localparam int RL = 4;              // buffer read latency (UltraRAM cascade pipelining)

    typedef struct packed {
        logic fill;                     // dropped line: send zeros
        logic buffer;
        logic scene;                    // first line of a scene
    } desc_t;

    logic [1:0] full;                   // buffer holds a line not yet read out

    // ---------------------------------------------------------------------
    // Write side
    // ---------------------------------------------------------------------
    logic          wb;                  // buffer the next line goes to
    logic          wr_on, wr_drop, wr_scene;
    logic [EB-1:0] wcnt;

    wire           line_start = s_valid && s_user[1];
    wire           wr_now     = s_valid && (line_start ? !full[wb] : wr_on);
    wire           drop_now   = s_valid && (line_start ?  full[wb] : wr_drop);
    wire [EB-1:0]  widx       = line_start ? '0 : wcnt;
    wire           scene_now  = line_start ? s_user[0] : wr_scene;

    logic  dq_push, dq_pop, dq_empty;
    desc_t dq_din, dq_dout;
    logic [$clog2(DQ):0] dq_count;

    always_comb begin
        dq_push = 1'b0;
        dq_din  = '{fill: 1'b0, buffer: wb, scene: scene_now};
        if (wr_now && s_last) begin
            dq_push = 1'b1;
        end else if (drop_now && s_last && dq_count <= DQ - 3) begin
            // keep room for the two buffers' descriptors
            dq_push     = 1'b1;
            dq_din.fill = 1'b1;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            wb <= 1'b0;
            {wr_on, wr_drop} <= '0;
            dropped_lines  <= '0;
            unfilled_lines <= '0;
        end else if (s_valid) begin
            wcnt <= widx + 1'b1;
            if (line_start) begin
                wr_on    <= !full[wb];
                wr_drop  <=  full[wb];
                wr_scene <= s_user[0];
            end
            if (s_last) begin
                wr_on   <= 1'b0;
                wr_drop <= 1'b0;
                if (wr_now) wb <= !wb;
                if (drop_now) begin
                    dropped_lines <= dropped_lines + 1;
                    if (!dq_push) unfilled_lines <= unfilled_lines + 1;
                end
            end
        end
    end

    sync_fifo #(.WIDTH($bits(desc_t)), .DEPTH(DQ)) u_desc (
        .clk   (clk),
        .rst   (rst),
        .din   (dq_din),
        .wr_en (dq_push),
        .rd_en (dq_pop),
        .dout  (dq_dout),
        .empty (dq_empty),
        .full  (),
        .count (dq_count)
    );

    // ---------------------------------------------------------------------
    // Read side
    // ---------------------------------------------------------------------
    logic          rd_on;
    desc_t         rd;
    logic [15:0]   xr, zr;
    logic [EB-1:0] ra;
    logic [$clog2(OQ):0] oq_count;
    logic [$clog2(RL):0] inflight;      // reads issued, not yet in the output FIFO

    wire last_zr = (zr == cfg.nz - 16'd1);
    wire last_xr = (xr == cfg.nx - 16'd1);
    wire rd_end  = last_zr && last_xr;
    wire credit  = (oq_count + inflight) < OQ;
    wire issue   = rd_on && credit;
    assign dq_pop = !dq_empty && (!rd_on || (issue && rd_end));

    // full[]: set by the writer at the end of a line, cleared by the reader
    // after the line's last read (never the same buffer in the same cycle)
    always_ff @(posedge clk) begin
        if (rst) begin
            full <= '0;
        end else begin
            if (wr_now && s_last)               full[wb]        <= 1'b1;
            if (issue && rd_end && !rd.fill)    full[rd.buffer] <= 1'b0;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            rd_on     <= 1'b0;
            lines_out <= '0;
        end else begin
            if (issue) begin
                if (!last_zr) begin
                    zr <= zr + 16'd1;
                    ra <= ra + EB'(cfg.nx);
                end else begin
                    zr <= '0;
                    xr <= xr + 16'd1;
                    ra <= EB'(xr + 16'd1);
                end
                if (rd_end) begin
                    rd_on     <= 1'b0;
                    lines_out <= lines_out + 1;
                end
            end
            if (dq_pop) begin
                rd_on <= 1'b1;
                rd    <= dq_dout;
                xr    <= '0;
                zr    <= '0;
                ra    <= '0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Buffers and read pipeline (RL cycles)
    // ---------------------------------------------------------------------
    logic [15:0] rdata;

    sample_ram #(
        .DEPTH   (2 * NX_MAX * NZ_MAX),
        .STYLE   (NX_MAX * NZ_MAX >= 32768 ? "ultra" : "block"),
        .LATENCY (RL)
    ) u_buf (
        .clk   (clk),
        .ce    (1'b1),
        .we    (wr_now),
        .waddr ({wb, widx}),
        .wdata (s_data),
        .raddr ({rd.buffer, ra}),
        .rdata (rdata)
    );

    typedef struct packed {
        logic       fill;
        logic [1:0] user;
        logic       last;
    } rtag_t;

    logic  vp [RL];
    rtag_t gp [RL];
    wire   first = (xr == 16'd0) && (zr == 16'd0);
    wire   v_out = vp[RL - 1];
    rtag_t g_out;
    assign g_out = gp[RL - 1];

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < RL; i++) vp[i] <= 1'b0;
            inflight <= '0;
        end else begin
            vp[0] <= issue;
            for (int i = 1; i < RL; i++) vp[i] <= vp[i - 1];
            inflight <= inflight + issue - v_out;
        end
        gp[0] <= '{fill: rd.fill, user: {first, first && rd.scene}, last: rd_end};
        for (int i = 1; i < RL; i++) gp[i] <= gp[i - 1];
    end

    // Output FIFO (credit-based: never written when full) and register slice
    logic        oq_empty, oq_rd, sk_ready;
    logic [18:0] oq_dout;

    sync_fifo #(.WIDTH(19), .DEPTH(OQ)) u_out (
        .clk   (clk),
        .rst   (rst),
        .din   ({g_out.last, g_out.user, g_out.fill ? 16'd0 : rdata}),
        .wr_en (v_out),
        .rd_en (oq_rd),
        .dout  (oq_dout),
        .empty (oq_empty),
        .full  (),
        .count (oq_count)
    );

    assign oq_rd = !oq_empty && sk_ready;

    axis_skid_buffer #(.DATA_W(16), .USER_W(2)) u_skid (
        .clk      (clk),
        .rst      (rst),
        .s_tdata  (oq_dout[15:0]),
        .s_tuser  (oq_dout[17:16]),
        .s_tlast  (oq_dout[18]),
        .s_tvalid (!oq_empty),
        .s_tready (sk_ready),
        .m_tdata  (m_axis_tdata),
        .m_tuser  (m_axis_tuser),
        .m_tlast  (m_axis_tlast),
        .m_tvalid (m_axis_tvalid),
        .m_tready (m_axis_tready)
    );
endmodule
