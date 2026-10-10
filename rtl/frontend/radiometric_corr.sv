`timescale 1ns / 1ps

// Radiometric correction (SPEC.md 6.2) on the BIL sample stream, one sample
// per clock, never stalls (the sensor stream cannot be back-pressured).
//
//   c = clamp(((x - dark) * gain + 2^13) >>> 14, 0, 2^D - 1)
//
// then bad pixels are replaced from their good neighbours in the same band and
// line (pre-replacement values; see SPEC 6.2). The table entry of sample x of
// band z sits at z*Nx + x, the sample's position in its BIL line, so a counter
// reset at every line start addresses it.
//
// Table: two 36-bit entries {3'b0, bad, gain, dark} per 72-bit word (one
// UltraRAM word). The table-load stream carries the .cal records as stored in
// memory, two 8-byte records per 128-bit beat (dark [15:0], gain [31:16],
// bad [32]; next record at +64), from entry 0; tlast ends the table. Load
// only while no samples flow.
//
// Pipeline: S0 input, table read (TL = 4 cycles: the UltraRAM cascade wants 3
// output pipeline stages), then S3 x - dark, S4 product, S5 rounding, S6 clamp,
// S7 bad-pixel window -> output. A sample is held in the window until its right
// neighbour arrives (not needed for the last pixel of a band).
module radiometric_corr import frontend_pkg::*; #(
    parameter int NX_MAX = 512,
    parameter int NZ_MAX = 256
) (
    input  logic         clk,
    input  logic         rst,
    input  fe_cfg_t      cfg,

    input  logic [127:0] s_tbl_tdata,
    input  logic         s_tbl_tlast,
    input  logic         s_tbl_tvalid,
    output logic         s_tbl_tready,

    input  logic         s_valid,
    input  logic [15:0]  s_data,
    input  logic [1:0]   s_user,        // [0] first sample of a scene, [1] first of a line
    input  logic         s_last,

    output logic         m_valid,
    output logic [15:0]  m_data,
    output logic [1:0]   m_user,
    output logic         m_last,

    output logic [31:0]  scene_clamped,     // clamp events in the current scene
    output logic [31:0]  scene_replaced     // bad pixels replaced in the current scene
);
    localparam int EB = $clog2(NX_MAX * NZ_MAX);    // entry address
    localparam int WB = EB - 1;                     // word address
    localparam int TL = 4;                          // table read latency

    // ---------------------------------------------------------------------
    // Table
    // ---------------------------------------------------------------------
    logic [WB-1:0] tbl_waddr;
    logic [EB-1:0] tbl_raddr;
    logic [71:0]   tbl_rdata;

    assign s_tbl_tready = 1'b1;
    wire tbl_we = s_tbl_tvalid;

    always_ff @(posedge clk) begin
        if (rst)
            tbl_waddr <= '0;
        else if (tbl_we)
            tbl_waddr <= s_tbl_tlast ? '0 : tbl_waddr + 1'b1;
    end

    sdp_ram #(
        .WIDTH (72),
        .DEPTH (NX_MAX * NZ_MAX / 2),
        .STYLE   (NX_MAX * NZ_MAX >= 65536 ? "ultra" : "block"),
        .LATENCY (TL)
    ) u_table (
        .clk   (clk),
        .ce    (1'b1),
        .we    (tbl_we),
        .waddr (tbl_waddr),
        .wdata ({3'b000, s_tbl_tdata[96], s_tbl_tdata[95:64],
                 3'b000, s_tbl_tdata[32], s_tbl_tdata[31:0]}),
        .raddr (tbl_raddr[EB-1:1]),
        .rdata (tbl_rdata)
    );

    // ---------------------------------------------------------------------
    // S0: position in the line
    // ---------------------------------------------------------------------
    logic [EB-1:0] idx_cnt;
    logic [15:0]   x_cnt;

    wire [EB-1:0] idx0   = s_user[1] ? '0 : idx_cnt;
    wire [15:0]   x0     = s_user[1] ? '0 : x_cnt;
    wire          last_x = (x0 == cfg.nx - 16'd1);
    assign tbl_raddr = idx0;

    always_ff @(posedge clk) begin
        if (rst) begin
            idx_cnt <= '0;
            x_cnt   <= '0;
        end else if (s_valid) begin
            idx_cnt <= idx0 + 1'b1;
            x_cnt   <= last_x ? '0 : x0 + 16'd1;
        end
    end

    // Tags carried down the pipeline
    typedef struct packed {
        logic [15:0] raw;
        logic [1:0]  user;
        logic        last;
        logic        first_x, last_x;
    } tag_t;

    // delay line alongside the table read; stage TL-1 meets the table data
    logic vr [TL];
    tag_t tr [TL];
    logic lr [TL];

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < TL; i++) vr[i] <= 1'b0;
        end else begin
            vr[0] <= s_valid;
            for (int i = 1; i < TL; i++) vr[i] <= vr[i - 1];
        end
        tr[0] <= '{raw: s_data, user: s_user, last: s_last, first_x: x0 == 16'd0, last_x: last_x};
        lr[0] <= idx0[0];
        for (int i = 1; i < TL; i++) begin
            tr[i] <= tr[i - 1];
            lr[i] <= lr[i - 1];
        end
    end

    logic v3, v4, v5, v6;
    tag_t t3, t4, t5, t6;
    wire  v2    = vr[TL - 1];
    tag_t t2;
    assign t2   = tr[TL - 1];
    wire  lane2 = lr[TL - 1];

    // ---------------------------------------------------------------------
    // S3 - S6 (after the table read): c = clamp(((x - dark) * gain + 2^13) >>> 14, 0, 2^D - 1)
    // ---------------------------------------------------------------------
    wire [35:0] entry = lane2 ? tbl_rdata[71:36] : tbl_rdata[35:0];

    logic signed [16:0] diff3;
    logic        [15:0] gain3;
    logic               good3, good4, good5, good6;
    logic signed [33:0] prod4, sum5;
    logic        [15:0] c6;
    logic               clamp6;
    logic        [15:0] top;

    wire signed [19:0] v_sh = 20'(sum5 >>> 14);

    always_ff @(posedge clk) begin
        if (rst) begin
            {v3, v4, v5, v6} <= '0;
        end else begin
            v3 <= v2;
            v4 <= v3;
            v5 <= v4;
            v6 <= v5;
        end
        top <= 16'((17'd1 << cfg.depth) - 17'd1);

        // S3
        t3    <= t2;
        diff3 <= $signed({1'b0, t2.raw}) - $signed({1'b0, entry[15:0]});
        gain3 <= entry[31:16];
        good3 <= !entry[32];
        // S4
        t4    <= t3;
        prod4 <= diff3 * $signed({1'b0, gain3});
        good4 <= good3;
        // S5
        t5    <= t4;
        sum5  <= prod4 + 34'sd8192;
        good5 <= good4;
        // S6
        t6 <= t5;
        if (cfg.rc_bypass) begin
            c6     <= t5.raw;
            good6  <= 1'b1;
            clamp6 <= 1'b0;
        end else begin
            c6     <= (v_sh < 0) ? 16'd0 : (v_sh > $signed({4'd0, top})) ? top : v_sh[15:0];
            good6  <= good5;
            clamp6 <= (v_sh < 0) || (v_sh > $signed({4'd0, top}));
        end
    end

    always_ff @(posedge clk) begin
        if (rst)
            scene_clamped <= '0;
        else if (v6)
            scene_clamped <= (t6.user[0] ? 32'd0 : scene_clamped) + 32'(clamp6);
    end

    // ---------------------------------------------------------------------
    // S7: bad-pixel replacement window
    //   h    : held sample, waiting for its right neighbour (the S6 sample)
    //   prev : the sample output before h (its left neighbour unless h.first_x)
    // ---------------------------------------------------------------------
    logic        hv;
    tag_t        h;
    logic [15:0] hc, pc;
    logic        hg, pg;

    wire         out_fire = hv && (h.last_x || v6);
    wire         lg       = !h.first_x && pg;
    wire         rg       = !h.last_x && good6;
    wire  [16:0] avg      = ({1'b0, pc} + {1'b0, c6} + 17'd1) >> 1;

    always_ff @(posedge clk) begin
        if (rst) begin
            hv      <= 1'b0;
            m_valid <= 1'b0;
            scene_replaced <= '0;
        end else begin
            m_valid <= out_fire;
            if (out_fire) begin
                m_user <= h.user;
                m_last <= h.last;
                m_data <= hg       ? hc :
                          lg && rg ? avg[15:0] :
                          lg       ? pc :
                          rg       ? c6 : 16'd0;
                pc <= hc;
                pg <= hg;
                scene_replaced <= (h.user[0] ? 32'd0 : scene_replaced) + 32'(!hg);
            end
            if (v6) begin
                h  <= t6;
                hc <= c6;
                hg <= good6;
            end
            hv <= v6 || (hv && !out_fire);
        end
    end
endmodule
