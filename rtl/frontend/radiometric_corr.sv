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
// Table: one entry {31'b0, bad, gain, dark} per 64-bit word, with the
// UltraRAM's built-in ECC (SPEC RAD-02), in two banks of even and odd entries
// so that a load beat writes its two entries in one cycle. The table-load
// stream carries the .cal records as stored in memory, two 8-byte records per
// 128-bit beat (dark [15:0], gain [31:16], bad [32]; next record at +64), from
// entry 0; tlast ends the table. Load only while no samples flow. tbl_cmd
// reads or writes one entry (debug, SPEC RC-02): a write takes a cycle without
// table-load beat, a read a cycle without sample; tbl_done pulses when it is
// complete (tbl_rdata for a read).
//
// Scrubbing: the table is static, so a single-bit upset would stay until a
// second one in the same word made it uncorrectable. A read that returns a
// corrected entry writes it back, in the next cycle without other table
// write; the write-back is dropped if the table was written since the read
// (the next read of the entry finds the error again). tbl_inj arms an error
// injection into the next write of entry tbl_inj.addr.
//
// Pipeline: S0 input, table read (TL = 4 cycles: the UltraRAM cascade wants 3
// output pipeline stages), then S3 x - dark, S4 product, S5 rounding, S6 clamp,
// S7 bad-pixel window -> output. A sample is held in the window until its right
// neighbour arrives (not needed for the last pixel of a band).
module radiometric_corr import frontend_pkg::*, ecc_pkg::*; #(
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
    output logic         m_ue,              // the sample (or a neighbour it was replaced
                                            // from) had an uncorrectable table entry (RAD-06)

    input  tbl_cmd_t     tbl_cmd,
    output logic [32:0]  tbl_rdata,         // {bad, gain, dark}
    output logic         tbl_done,
    input  ecc_inj_t     tbl_inj,
    output ecc_evt_t     tbl_ecc,

    output logic [31:0]  scene_clamped,     // clamp events in the current scene (saturating)
    output logic [31:0]  scene_replaced,    // bad pixels replaced in the current scene (saturating)
    output logic [31:0]  tbl_words          // 128-bit beats in the last table load
);
    localparam int EB = $clog2(NX_MAX * NZ_MAX);    // entry address
    localparam int WB = EB - 1;                     // word address
    localparam int TL = 4;                          // table read latency

    // ---------------------------------------------------------------------
    // Table: banks of even and odd entries
    // ---------------------------------------------------------------------
    logic [WB-1:0] load_addr;
    logic [EB-1:0] tbl_raddr;
    logic          tbl_re;

    assign s_tbl_tready = 1'b1;
    wire load = s_tbl_tvalid;

    always_ff @(posedge clk) begin
        if (rst) begin
            load_addr <= '0;
            tbl_words <= '0;
        end else if (load) begin
            load_addr <= s_tbl_tlast ? '0 : load_addr + 1'b1;
            if (s_tbl_tlast) tbl_words <= 32'(load_addr) + 32'd1;
        end
    end

    // Debug access: a pending command waits for a free port cycle
    logic          dbg_wr, dbg_rd;
    logic [EB-1:0] dbg_addr;
    logic [32:0]   dbg_wdata;
    wire           dbg_wr_go = dbg_wr && !load;
    wire           dbg_rd_go = dbg_rd && !s_valid;

    always_ff @(posedge clk) begin
        if (rst) begin
            dbg_wr <= 1'b0;
            dbg_rd <= 1'b0;
        end else if (tbl_cmd.valid) begin
            dbg_wr    <= tbl_cmd.write;
            dbg_rd    <= !tbl_cmd.write;
            dbg_addr  <= EB'(tbl_cmd.addr);
            dbg_wdata <= tbl_cmd.wdata;
        end else begin
            if (dbg_wr_go) dbg_wr <= 1'b0;
            if (dbg_rd_go) dbg_rd <= 1'b0;
        end
    end

    // Scrub write-back of a corrected entry (captured after the table read)
    logic          sc_pend, sc_bank;
    logic [WB-1:0] sc_addr;
    logic [32:0]   sc_data;
    wire           sc_go = sc_pend && !load && !dbg_wr;

    // Error injection, armed until the next write of the target entry
    logic          armed, armed_dbe;
    logic [EB-1:0] target;

    logic [1:0]    bank_we, bank_inj;
    logic [WB-1:0] tbl_waddr;
    logic [32:0]   bank_wdata [2];
    always_comb begin
        if (load) begin
            bank_we       = 2'b11;
            tbl_waddr     = load_addr;
            bank_wdata[0] = s_tbl_tdata[32:0];
            bank_wdata[1] = s_tbl_tdata[96:64];
            bank_inj      = (armed && load_addr == target[EB-1:1]) ? 2'b01 << target[0] : 2'b00;
        end else begin
            bank_we       = dbg_wr ? 2'b01 << dbg_addr[0] : sc_go ? 2'b01 << sc_bank : 2'b00;
            tbl_waddr     = dbg_wr ? dbg_addr[EB-1:1] : sc_addr;
            bank_wdata[0] = dbg_wr ? dbg_wdata : sc_data;
            bank_wdata[1] = dbg_wr ? dbg_wdata : sc_data;
            bank_inj      = (armed && tbl_waddr == target[EB-1:1]) ? bank_we & (2'b01 << target[0]) : 2'b00;
        end
    end
    wire tbl_write = |bank_we;

    always_ff @(posedge clk) begin
        if (rst) begin
            armed <= 1'b0;
        end else if (tbl_inj.arm) begin
            armed     <= 1'b1;
            armed_dbe <= tbl_inj.dbe;
            target    <= tbl_inj.addr[EB-1:0];
        end else if (|bank_inj) begin
            armed <= 1'b0;
        end
    end

    logic [63:0] bank_q [2];
    logic [1:0]  bank_sbe, bank_dbe;

    for (genvar b = 0; b < 2; b++) begin : g_bank
        ecc_ram #(
            .DEPTH   (NX_MAX * NZ_MAX / 2),
            .STYLE   (NX_MAX * NZ_MAX >= 65536 ? "ultra" : "block"),
            .LATENCY (TL)
        ) u_table (
            .clk     (clk),
            .rst     (rst),
            .ce      (1'b1),
            .we      (bank_we[b]),
            .waddr   (tbl_waddr),
            .wdata   ({31'd0, bank_wdata[b]}),
            .inj_sbe (bank_inj[b] && !armed_dbe),
            .inj_dbe (bank_inj[b] && armed_dbe),
            .re      (tbl_re && tbl_raddr[0] == 1'(b)),
            .raddr   (tbl_raddr[EB-1:1]),
            .rdata   (bank_q[b]),
            .sbe     (bank_sbe[b]),
            .dbe     (bank_dbe[b])
        );
    end

    assign tbl_ecc = '{pending: armed, sbe: |bank_sbe, dbe: |bank_dbe};

    // ---------------------------------------------------------------------
    // S0: position in the line
    // ---------------------------------------------------------------------
    logic [EB-1:0] idx_cnt;
    logic [15:0]   x_cnt;

    wire [EB-1:0] idx0   = s_user[1] ? '0 : idx_cnt;
    wire [15:0]   x0     = s_user[1] ? '0 : x_cnt;
    wire          last_x = (x0 == cfg.nx - 16'd1);
    assign tbl_raddr = dbg_rd_go ? dbg_addr : idx0;
    assign tbl_re    = s_valid || dbg_rd_go;

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
        logic        ue;                // uncorrectable table entry
    } tag_t;

    // delay line alongside the table read; stage TL-1 meets the table data
    logic vr [TL];                      // sample
    logic dr [TL];                      // debug read
    tag_t tr [TL];
    logic lr [TL];                      // bank of the entry
    logic [WB-1:0] ar [TL];             // word address of the entry
    logic [TL-1:0] wr_hist;             // table writes in the last TL cycles

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < TL; i++) begin
                vr[i] <= 1'b0;
                dr[i] <= 1'b0;
            end
            tbl_done <= 1'b0;
        end else begin
            vr[0] <= s_valid;
            dr[0] <= dbg_rd_go;
            for (int i = 1; i < TL; i++) begin
                vr[i] <= vr[i - 1];
                dr[i] <= dr[i - 1];
            end
            tbl_done <= dbg_wr_go || dr[TL - 1];
        end
        tr[0] <= '{raw: s_data, user: s_user, last: s_last, first_x: x0 == 16'd0, last_x: last_x, ue: 1'b0};
        lr[0] <= tbl_raddr[0];
        ar[0] <= tbl_raddr[EB-1:1];
        for (int i = 1; i < TL; i++) begin
            tr[i] <= tr[i - 1];
            lr[i] <= lr[i - 1];
            ar[i] <= ar[i - 1];
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
    wire [32:0] entry = bank_q[lane2][32:0];

    always_ff @(posedge clk) begin
        if (dr[TL - 1]) tbl_rdata <= entry;
    end

    // Scrub: a corrected entry is written back, unless the table was written
    // between its read (TL cycles ago) and now
    always_ff @(posedge clk) begin
        if (rst) begin
            sc_pend <= 1'b0;
            wr_hist <= '0;
        end else begin
            wr_hist <= TL'({wr_hist, tbl_write});
            if (load || dbg_wr || sc_go)
                sc_pend <= 1'b0;
            else if (|bank_sbe && !sc_pend && !(|wr_hist)) begin
                sc_pend <= 1'b1;
                sc_bank <= lane2;
                sc_addr <= ar[TL - 1];
                sc_data <= entry;
            end
        end
    end

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
        t3.ue <= v2 && |bank_dbe;       // the read of this sample's entry
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
            scene_clamped <= t6.user[0] ? 32'(clamp6) : clamp6 ? sat_inc(scene_clamped) : scene_clamped;
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
                m_ue   <= h.ue || (!h.last_x && v6 && t6.ue);
                m_data <= hg       ? hc :
                          lg && rg ? avg[15:0] :
                          lg       ? pc :
                          rg       ? c6 : 16'd0;
                pc <= hc;
                pg <= hg;
                scene_replaced <= h.user[0] ? 32'(!hg) : !hg ? sat_inc(scene_replaced) : scene_replaced;
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
