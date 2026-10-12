`timescale 1ns / 1ps

// Test of scene_guard: scenes that are complete, cut short at random points
// (lines lost without fill lines), or missing their first-of-scene marker (first
// line lost), with random input gaps and output back-pressure. Checks every
// output sample against the reference rule (a marker before a scene is complete
// is preceded by zero samples up to Nx*Ny*Nz), that every output marker falls
// on a scene boundary, and the counters.
module tb_scene_guard;
    localparam int NX = 5, NY = 3, NZ = 4, TOTAL = NX * NY * NZ, SCENES = 60;

    logic clk = 1'b0, rst = 1'b1;
    always #2 clk = ~clk;

    logic [15:0] s_tdata = '0, m_tdata;
    logic [1:0]  s_tuser = '0, m_tuser;
    logic        s_tlast = 1'b0, s_tvalid = 1'b0, s_tready, m_tlast, m_tvalid, m_tready = 1'b0;
    logic [31:0] padded_scenes, padded_samples;

    scene_guard dut (
        .clk, .rst, .nx (16'(NX)), .ny (16'(NY)), .nz (16'(NZ)), .cnt_clr (1'b0),
        .s_tdata, .s_tuser, .s_tlast, .s_tvalid, .s_tready,
        .m_tdata, .m_tuser, .m_tlast, .m_tvalid, .m_tready,
        .padded_scenes, .padded_samples
    );

    // input items {first_of_scene, data} and the expected output
    typedef struct packed { logic first; logic [15:0] data; } item_t;
    item_t in_q [$], exp_q [$];
    int exp_scenes = 0, exp_samples = 0;

    initial begin
        int n = 0;
        logic [15:0] v = 16'd1;
        for (int s = 0; s < SCENES; s++) begin
            int len, kind;
            kind = $urandom_range(0, 9);
            len = (kind < 6) ? TOTAL : (kind < 9) ? $urandom_range(1, TOTAL - 1) : TOTAL;
            for (int i = 0; i < len; i++) begin
                // kind 9: the scene's marker was lost with its first line
                in_q.push_back('{first: (i == 0 && kind != 9), data: v});
                v = (v == 16'hFFFF) ? 16'd1 : v + 16'd1;     // never 0: padding is told apart
            end
        end
        // reference: the rule applied to the input
        foreach (in_q[k]) begin
            if (in_q[k].first && n != 0) begin
                exp_scenes++;
                for (int p = n; p < TOTAL; p++) begin
                    exp_q.push_back('{first: 1'b0, data: 16'd0});
                    exp_samples++;
                end
                n = 0;
            end
            exp_q.push_back(in_q[k]);
            n = (n + 1) % TOTAL;
        end
    end

    // source with random gaps
    int sent = 0;
    always @(posedge clk) begin
        if (rst) begin
            s_tvalid <= 1'b0;
        end else if (!s_tvalid || s_tready) begin
            if (sent < in_q.size() && $urandom_range(0, 99) < 70) begin
                s_tvalid <= 1'b1;
                s_tdata  <= in_q[sent].data;
                s_tuser  <= {1'b0, in_q[sent].first};
                sent     <= sent + 1;
            end else begin
                s_tvalid <= 1'b0;
            end
        end
    end

    // sink with random back-pressure
    int got = 0, errors = 0, pos = 0;
    always @(posedge clk) begin
        if (!rst && m_tvalid && m_tready) begin
            item_t e;
            e = (got < exp_q.size()) ? exp_q[got] : '{first: 1'bx, data: 16'hxxxx};
            if (got >= exp_q.size() || m_tdata !== e.data || m_tuser[0] !== e.first) begin
                errors++;
                if (errors <= 5) $display("  output %0d: got %04h first %b, expected %04h first %b",
                                          got, m_tdata, m_tuser[0], e.data, e.first);
            end
            if (m_tuser[0] && pos != 0) begin
                errors++;
                $display("  output %0d: scene marker at scene position %0d", got, pos);
            end
            pos = (pos + 1) % TOTAL;
            got++;
        end
        m_tready <= ($urandom_range(0, 99) < 60);
    end

    initial begin
        repeat (10) @(posedge clk);
        rst <= 1'b0;
        // (a clocked loop: xsim does not re-evaluate wait() on queue methods)
        while (!(in_q.size() > 0 && got == exp_q.size())) @(posedge clk);
        repeat (20) @(posedge clk);
        if (got != exp_q.size()) begin errors++; $display("  %0d outputs, expected %0d", got, exp_q.size()); end
        if (padded_scenes != exp_scenes || padded_samples != exp_samples) begin
            errors++;
            $display("  counters %0d scenes / %0d samples padded, expected %0d / %0d",
                     padded_scenes, padded_samples, exp_scenes, exp_samples);
        end
        if (errors == 0)
            $display("PASS: tb_scene_guard, %0d inputs, %0d outputs, %0d short scenes padded with %0d samples",
                     in_q.size(), got, padded_scenes, padded_samples);
        else
            $display("FAIL: tb_scene_guard: %0d errors", errors);
        $finish;
    end

    initial begin
        #5ms;
        $display("FAIL: tb_scene_guard timeout (%0d of %0d outputs)", got, exp_q.size());
        $finish;
    end
endmodule
