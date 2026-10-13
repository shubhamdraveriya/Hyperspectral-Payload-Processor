`timescale 1ns / 1ps

// Self-checking test of the downlink transmit chain against the Python model
// (tb/downlink/gen_vectors.py), plusarg +DIR=<test directory>:
//
//   compressed scenes -> space_packetizer -> aos_framer -> rs_encoder -> cadu_framer
//
// Checks the packet stream (bytes and markers), the frames (without idle
// frames) and every CADU: each must equal the next science CADU of the model
// or, with idle frames enabled, the next idle CADU. Random input gaps and
// output back-pressure.
module tb_downlink;
    import downlink_pkg::*;

    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;

    // configuration
    int apid, pkt_data, scid, vcid, idle_en, frame_gap, in_pct, out_pct, nscenes;

    // DUT streams
    logic [63:0] c_tdata = '0;
    logic [7:0]  c_tkeep = '0;
    logic        c_tlast = 1'b0, c_tvalid = 1'b0, c_tready;
    logic [23:0] meta_tdata = '0;
    logic        meta_tvalid = 1'b0, meta_tready;
    logic [7:0]  p_tdata, f_tdata, r_tdata, a_tdata;
    logic [1:0]  p_tuser;
    logic        p_tlast, p_tvalid, p_tready;
    logic        f_tlast, f_tvalid, f_tready;
    logic        r_tlast, r_tvalid, r_tready;
    logic        a_tuser, a_tlast, a_tvalid, a_tready = 1'b0;
    logic [31:0] packets, scenes, science_frames, idle_frames, cadus;

    space_packetizer #(.PKT_MAX(2048)) u_pkt (
        .clk, .rst,
        .apid (11'(apid)), .pkt_data (12'(pkt_data)), .raw_mode (1'b0), .scene_ld (1'b0),
        .scene_ue (1'b0), .cnt_clr (1'b0),
        .s_axis_tdata (c_tdata), .s_axis_tkeep (c_tkeep), .s_axis_tlast (c_tlast),
        .s_axis_tvalid (c_tvalid), .s_axis_tready (c_tready),
        .s_meta_tdata (meta_tdata), .s_meta_tvalid (meta_tvalid), .s_meta_tready (meta_tready),
        .m_axis_tdata (p_tdata), .m_axis_tuser (p_tuser), .m_axis_tlast (p_tlast),
        .m_axis_tvalid (p_tvalid), .m_axis_tready (p_tready),
        .packets, .scenes, .scene_id ()
    );

    aos_framer u_aos (
        .clk, .rst,
        .scid (8'(scid)), .vcid (6'(vcid)), .idle_en (idle_en != 0), .frame_gap (32'(frame_gap)), .cnt_clr (1'b0),
        .s_axis_tdata (p_tdata), .s_axis_tuser (p_tuser), .s_axis_tlast (p_tlast),
        .s_axis_tvalid (p_tvalid), .s_axis_tready (p_tready),
        .m_axis_tdata (f_tdata), .m_axis_tlast (f_tlast), .m_axis_tvalid (f_tvalid),
        .m_axis_tready (f_tready),
        .science_frames, .idle_frames
    );

    rs_encoder u_rs (
        .clk, .rst,
        .s_axis_tdata (f_tdata), .s_axis_tlast (f_tlast), .s_axis_tvalid (f_tvalid),
        .s_axis_tready (f_tready),
        .m_axis_tdata (r_tdata), .m_axis_tlast (r_tlast), .m_axis_tvalid (r_tvalid),
        .m_axis_tready (r_tready)
    );

    cadu_framer u_cadu (
        .clk, .rst, .cnt_clr (1'b0),
        .s_axis_tdata (r_tdata), .s_axis_tlast (r_tlast), .s_axis_tvalid (r_tvalid),
        .s_axis_tready (r_tready),
        .m_axis_tdata (a_tdata), .m_axis_tuser (a_tuser), .m_axis_tlast (a_tlast),
        .m_axis_tvalid (a_tvalid), .m_axis_tready (a_tready),
        .cadus
    );

    // -----------------------------------------------------------------
    // Vectors (xsim: keep push_back inside begin/end in while loops)
    // -----------------------------------------------------------------
    string      dir;
    logic [7:0] payload [$], frames [$], sci [$], idle [$];
    logic [23:0] meta [$];
    logic [10:0] pk [$];                        // {last, last_of_scene, first, byte}
    int         lens [$];

    task automatic read_bytes(string path, ref logic [7:0] q [$]);
        int fd, v;
        logic [7:0] w;
        fd = $fopen(path, "r");
        if (fd == 0) $fatal(1, "cannot open %s", path);
        while ($fscanf(fd, "%h", v) == 1) begin
            w = v;
            q.push_back(w);
        end
        $fclose(fd);
    endtask

    task automatic load();
        int fd, n, v, f, s, l;
        logic [10:0] e;
        logic [23:0] m;
        fd = $fopen({dir, "/cfg.txt"}, "r");
        if (fd == 0) $fatal(1, "cannot open %s/cfg.txt", dir);
        n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d", apid, pkt_data, scid, vcid, idle_en, frame_gap,
                    in_pct, out_pct, nscenes);
        if (n != 9) $fatal(1, "bad cfg.txt");
        $fclose(fd);
        read_bytes({dir, "/payload.hex"}, payload);
        read_bytes({dir, "/frames.hex"}, frames);
        read_bytes({dir, "/cadus.hex"}, sci);
        read_bytes({dir, "/idle_cadus.hex"}, idle);
        fd = $fopen({dir, "/lens.txt"}, "r");
        while ($fscanf(fd, "%d", v) == 1) begin
            lens.push_back(v);
        end
        $fclose(fd);
        fd = $fopen({dir, "/meta.hex"}, "r");
        while ($fscanf(fd, "%h", v) == 1) begin
            m = v;
            meta.push_back(m);
        end
        $fclose(fd);
        fd = $fopen({dir, "/packets.txt"}, "r");
        while ($fscanf(fd, "%h %d %d %d", v, f, s, l) == 4) begin
            e = {1'(l), 1'(s), 1'(f), 8'(v)};
            pk.push_back(e);
        end
        $fclose(fd);
    endtask

    // -----------------------------------------------------------------
    // Input: scenes as 64-bit words (first byte in [7:0]), tkeep on the last
    // -----------------------------------------------------------------
    int scene = 0, off = 0, base = 0;
    always @(posedge clk) begin
        if (rst) begin
            c_tvalid <= 1'b0;
        end else if (!c_tvalid || c_tready) begin
            if (scene < nscenes && $urandom_range(0, 99) < in_pct) begin
                int n;
                n = (lens[scene] - off >= 8) ? 8 : lens[scene] - off;
                for (int b = 0; b < 8; b++) begin
                    c_tdata[8 * b +: 8] <= (b < n) ? payload[base + off + b] : 8'h00;
                    c_tkeep[b]          <= (b < n);
                end
                c_tlast  <= (off + n == lens[scene]);
                c_tvalid <= 1'b1;
                if (off + n == lens[scene]) begin
                    base  += lens[scene];
                    off    = 0;
                    scene += 1;
                end else begin
                    off += n;
                end
            end else begin
                c_tvalid <= 1'b0;
            end
        end
    end

    int mi = 0;
    always @(posedge clk) begin
        if (rst) begin
            meta_tvalid <= 1'b0;
        end else if (!meta_tvalid || meta_tready) begin
            meta_tvalid <= (mi < meta.size());
            if (mi < meta.size()) begin
                meta_tdata <= meta[mi];
                mi <= mi + 1;
            end
        end
    end

    always @(posedge clk) a_tready <= ($urandom_range(0, 99) < out_pct);

    // -----------------------------------------------------------------
    // Checkers
    // -----------------------------------------------------------------
    int pi = 0, p_err = 0;
    logic [10:0] got_p, exp_p;
    always @(posedge clk) begin
        if (!rst && p_tvalid && p_tready) begin
            got_p = {p_tlast, p_tuser[1], p_tuser[0], p_tdata};
            exp_p = (pi < pk.size()) ? pk[pi] : 11'h7FF;
            if (pi >= pk.size() || got_p !== exp_p) begin
                p_err++;
                if (p_err <= 5) $display("  packet byte %0d: got %03h, expected %03h", pi, got_p, exp_p);
            end
            pi++;
        end
    end

    // frames: idle frames (VCID 63) are skipped here; science frames in order
    int fi = 0, f_err = 0, fpos = 0;
    logic [7:0] fbuf [FRAME_BYTES];
    always @(posedge clk) begin
        if (!rst && f_tvalid && f_tready) begin
            fbuf[fpos] = f_tdata;
            fpos++;
            if (f_tlast) begin
                if (fpos != FRAME_BYTES) begin
                    f_err++;
                    $display("  frame of %0d bytes", fpos);
                end else if (fbuf[1][5:0] != IDLE_VCID) begin
                    for (int i = 0; i < FRAME_BYTES; i++) begin
                        logic [7:0] e;
                        e = (fi * FRAME_BYTES + i < frames.size()) ? frames[fi * FRAME_BYTES + i] : 8'hxx;
                        if (fbuf[i] !== e) begin
                            f_err++;
                            if (f_err <= 5) $display("  frame %0d byte %0d: got %02h, expected %02h", fi, i, fbuf[i], e);
                        end
                    end
                    fi++;
                end
                fpos = 0;
            end
        end
    end

    // CADUs: next science CADU, or (idle frames on) next idle CADU
    int si = 0, ii = 0, a_err = 0, apos = 0;
    logic [7:0] abuf [CADU_BYTES];
    always @(posedge clk) begin
        if (!rst && a_tvalid && a_tready) begin
            if ((apos == 0) != a_tuser) begin
                a_err++;
                $display("  tuser at CADU byte %0d", apos);
            end
            abuf[apos] = a_tdata;
            apos++;
            if (a_tlast) begin
                bit is_sci, is_idle;
                is_sci  = (apos == CADU_BYTES);
                is_idle = (apos == CADU_BYTES) && idle_en != 0;
                for (int i = 0; i < CADU_BYTES; i++) begin
                    if (si * CADU_BYTES + i >= sci.size() || abuf[i] !== sci[si * CADU_BYTES + i]) is_sci = 0;
                    if (ii * CADU_BYTES + i >= idle.size() || abuf[i] !== idle[ii * CADU_BYTES + i]) is_idle = 0;
                end
                if (is_sci) si++;
                else if (is_idle) ii++;
                else begin
                    a_err++;
                    if (a_err <= 5) $display("  CADU %0d (%0d bytes) matches neither science CADU %0d nor idle CADU %0d",
                                             si + ii, apos, si, ii);
                    si++;
                end
                apos = 0;
            end
        end
    end

    initial begin
        int timeout, nsci, errors;
        if (!$value$plusargs("DIR=%s", dir)) $fatal(1, "+DIR=<test directory> missing");
        load();
        nsci = sci.size() / CADU_BYTES;
        $display("tb_downlink: %s, %0d scenes, %0d packet bytes, %0d science CADUs, idle %0d, gap %0d, in %0d%%, out %0d%%",
                 dir, nscenes, pk.size(), nsci, idle_en, frame_gap, in_pct, out_pct);
        repeat (20) @(posedge clk);
        rst <= 1'b0;

        timeout = 200 * (nsci + 4) * CADU_BYTES / (out_pct + 1) + nsci * frame_gap + 20000;
        fork
            wait (si >= nsci);
            repeat (timeout) @(posedge clk);
        join_any
        disable fork;
        repeat (100) @(posedge clk);

        errors = p_err + f_err + a_err;
        if (pi != pk.size())    begin errors++; $display("  %0d packet bytes, expected %0d", pi, pk.size()); end
        if (fi != nsci)         begin errors++; $display("  %0d science frames checked, expected %0d", fi, nsci); end
        if (si != nsci)         begin errors++; $display("  %0d science CADUs, expected %0d", si, nsci); end
        if (scenes != nscenes)  begin errors++; $display("  packetizer scenes %0d", scenes); end
        if (science_frames < nsci) begin errors++; $display("  framer science frames %0d", science_frames); end
        if (idle_en == 0 && ii != 0) begin errors++; $display("  idle CADUs with idle frames off"); end
        if (idle_en != 0 && ii == 0) begin errors++; $display("  no idle CADU seen"); end
        if (errors == 0)
            $display("PASS: tb_downlink %s, %0d packets, %0d science CADUs, %0d idle CADUs",
                     dir, packets, si, ii);
        else
            $display("FAIL: tb_downlink %s: %0d errors (packets %0d, frames %0d, CADUs %0d)",
                     dir, errors, p_err, f_err, a_err);
        $finish;
    end
endmodule
