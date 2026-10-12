`timescale 1ns / 1ps

// Self-checking test of error_injector -> frame_sync against the Python model
// (tb/ground/gen_vectors.py), plusarg +DIR=<test directory>.
//
// The stream enters the injector with random gaps (in_pct % of cycles, like
// the link), then 2100 filler bytes so that the frame sync can decide the
// last CADUs (it looks 1027 bytes ahead). The injector output is checked byte
// for byte; each record is matched by its sequence number against the model's
// record with that number (records past the model's, from the filler, are
// ignored). With random capture back-pressure, records may be dropped only in
// the test that expects it, and then as many as counted.
module tb_ground;
    import downlink_pkg::*;

    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;

    int mode, seed, threshold, burst_len, period, asm_bits, asm_tol, flywheel, in_pct, out_pct, drops;

    logic [7:0]  i_tdata = '0, e_tdata, g_tdata;
    logic        i_tuser = 1'b0, i_tlast = 1'b0, i_tvalid = 1'b0, i_tready;
    logic        e_tuser, e_tlast, e_tvalid;
    logic        g_tlast, g_tvalid, g_tready = 1'b0;
    logic [1:0]  sync_state;
    logic [31:0] injected_bits, cadus, sync_losses, asm_bit_errors, dropped_records;

    error_injector u_inj (
        .clk, .rst,
        .mode (2'(mode)), .threshold (32'(threshold)), .seed (32'(seed)), .seed_load (1'b0),
        .burst_len (16'(burst_len)), .period (32'(period)), .asm_bits (6'(asm_bits)), .cnt_clr (1'b0),
        .s_axis_tdata (i_tdata), .s_axis_tuser (i_tuser), .s_axis_tlast (i_tlast),
        .s_axis_tvalid (i_tvalid), .s_axis_tready (i_tready),
        .m_axis_tdata (e_tdata), .m_axis_tuser (e_tuser), .m_axis_tlast (e_tlast),
        .m_axis_tvalid (e_tvalid), .m_axis_tready (1'b1),
        .injected_bits
    );

    frame_sync u_fs (
        .clk, .rst,
        .asm_tol (6'(asm_tol)), .flywheel (4'(flywheel)), .cnt_clr (1'b0),
        .s_tdata (e_tdata), .s_tvalid (e_tvalid),
        .m_axis_tdata (g_tdata), .m_axis_tlast (g_tlast), .m_axis_tvalid (g_tvalid),
        .m_axis_tready (g_tready),
        .sync_state, .cadus, .sync_losses, .asm_bit_errors, .dropped_records
    );

    // -----------------------------------------------------------------
    // Vectors (xsim: keep push_back inside begin/end in while loops)
    // -----------------------------------------------------------------
    string      dir;
    logic [8:0] stream [$];                     // {first_of_cadu, byte}
    logic [7:0] injected [$], records [$];

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
        int fd, n, v, f;
        logic [8:0] e;
        fd = $fopen({dir, "/cfg.txt"}, "r");
        if (fd == 0) $fatal(1, "cannot open %s/cfg.txt", dir);
        n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d", mode, seed, threshold, burst_len, period,
                    asm_bits, asm_tol, flywheel, in_pct, out_pct, drops);
        if (n != 11) $fatal(1, "bad cfg.txt");
        $fclose(fd);
        fd = $fopen({dir, "/input.txt"}, "r");
        while ($fscanf(fd, "%h %d", v, f) == 2) begin
            e = {1'(f), 8'(v)};
            stream.push_back(e);
        end
        $fclose(fd);
        read_bytes({dir, "/injected.hex"}, injected);
        read_bytes({dir, "/records.hex"}, records);
    endtask

    // -----------------------------------------------------------------
    // Input with gaps, then filler
    // -----------------------------------------------------------------
    localparam int FILLER = 2100;
    int ii = 0;
    always @(posedge clk) begin
        if (rst) begin
            i_tvalid <= 1'b0;
        end else if (!i_tvalid || i_tready) begin
            if (ii < stream.size() + FILLER && $urandom_range(0, 99) < in_pct) begin
                i_tvalid <= 1'b1;
                i_tdata  <= (ii < stream.size()) ? stream[ii][7:0] : 8'h00;
                i_tuser  <= (ii < stream.size()) ? stream[ii][8] : 1'b0;
                ii <= ii + 1;
            end else begin
                i_tvalid <= 1'b0;
            end
        end
    end

    // injector output
    int ei = 0, e_err = 0;
    logic [7:0] e_exp;
    always @(posedge clk) begin
        if (!rst && e_tvalid) begin
            if (ei < injected.size()) begin
                e_exp = injected[ei];
                if (e_tdata !== e_exp) begin
                    e_err++;
                    if (e_err <= 5) $display("  injector byte %0d: got %02h, expected %02h", ei, e_tdata, e_exp);
                end
            end
            ei++;
        end
    end

    // records, matched by sequence number
    int nrec, rpos = 0, r_err = 0, received = 0, extra = 0;
    logic [7:0] rbuf [CADU_BYTES];
    bit seen [int];
    always @(posedge clk) begin
        if (!rst && g_tvalid && g_tready) begin
            rbuf[rpos] = g_tdata;
            rpos++;
            if (g_tlast) begin
                int s;
                s = {rbuf[1023], rbuf[1022]};
                if (rpos != CADU_BYTES) begin
                    r_err++;
                    $display("  record of %0d bytes", rpos);
                end else if (s < nrec) begin
                    for (int i = 0; i < CADU_BYTES; i++) begin
                        logic [7:0] e;
                        e = records[s * CADU_BYTES + i];
                        if (rbuf[i] !== e) begin
                            r_err++;
                            if (r_err <= 5) $display("  record %0d byte %0d: got %02h, expected %02h", s, i, rbuf[i], e);
                        end
                    end
                    if (seen.exists(s)) begin
                        r_err++;
                        $display("  record %0d received twice", s);
                    end
                    seen[s] = 1;
                    received++;
                end else begin
                    extra++;
                end
                rpos = 0;
            end
        end
        g_tready <= ($urandom_range(0, 99) < out_pct);
    end

    initial begin
        int errors, missing;
        if (!$value$plusargs("DIR=%s", dir)) $fatal(1, "+DIR=<test directory> missing");
        load();
        nrec = records.size() / CADU_BYTES;
        $display("tb_ground: %s, %0d bytes, mode %0d, %0d model records, tol %0d, flywheel %0d, in %0d%%, out %0d%%",
                 dir, stream.size(), mode, nrec, asm_tol, flywheel, in_pct, out_pct);
        repeat (20) @(posedge clk);
        rst <= 1'b0;

        // (a level wait on an expression with stream.size() never wakes up in xsim)
        while (ii < stream.size() + FILLER) @(posedge clk);
        repeat (200 * (2 * CADU_BYTES) / (out_pct + 1) + 5000) @(posedge clk);

        missing = nrec - received;
        errors  = e_err + r_err;
        if (ei < injected.size()) begin errors++; $display("  injector output %0d bytes", ei); end
        if (drops == 0 && missing != 0) begin
            errors++;
            $display("  %0d of %0d records missing (%0d dropped)", missing, nrec, dropped_records);
        end
        if (drops != 0 && (missing == 0 || dropped_records < missing)) begin
            errors++;
            $display("  overflow test: %0d missing, %0d counted as dropped", missing, dropped_records);
        end
        if (errors == 0)
            $display("PASS: tb_ground %s, %0d records (%0d dropped, %0d past the model), %0d flipped bits, %0d sync losses, %0d ASM bit errors",
                     dir, received, missing, extra, injected_bits, sync_losses, asm_bit_errors);
        else
            $display("FAIL: tb_ground %s: %0d errors (injector %0d, records %0d), %0d/%0d records",
                     dir, errors, e_err, r_err, received, nrec);
        $finish;
    end

    initial begin
        #20ms;
        $display("FAIL: tb_ground timeout (%0d of %0d input bytes)", ii, stream.size() + FILLER);
        $finish;
    end
endmodule
