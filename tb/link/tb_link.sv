`timescale 1ns / 1ps

// Test of link_layer with a behavioural model of the GTH user interface: the
// TX words reach the RX after a delay; the "channel" can delete or insert idle
// words (as the elastic buffer does for clock correction), mark words with
// code errors and drop byte alignment. The 8b/10b coding, comma alignment and
// elastic buffer themselves are the GTH's (checked on the board).
//
// Sequence: alignment -> lock after 1024 good words -> data integrity with
// random input gaps and idle deletion/insertion -> clock-correction interval
// -> 8 code errors in a window (lock kept) -> 9 (lock lost, counted) ->
// relock -> data again -> loss of alignment.
module tb_link;
    logic clk_sys = 1'b0, clk_link = 1'b0, rst = 1'b1;
    always #2 clk_sys = ~clk_sys;              // 250 MHz
    initial begin
        #3.1;
        forever #8 clk_link = ~clk_link;       // 62.5 MHz
    end

    localparam int CC_INTERVAL = 64;

    logic [7:0]  s_tdata = '0, m_tdata;
    logic        s_tvalid = 1'b0, s_tready, m_tvalid;
    logic [15:0] txdata, rxdata = 16'h50BC;
    logic [1:0]  txcharisk, rxcharisk = 2'b01, rxdisperr = '0, rxnotintable = '0;
    logic        rxbyteisaligned = 1'b0, locked;
    logic [31:0] code_errors, lock_losses, rx_words, tx_data_words, tx_idle_words, rx_overflows;

    link_layer dut (
        .clk_sys, .rst_sys (rst), .clk_link, .rst_link (rst),
        .cc_interval (16'(CC_INTERVAL)),
        .s_axis_tdata (s_tdata), .s_axis_tvalid (s_tvalid), .s_axis_tready (s_tready),
        .m_tdata, .m_tvalid,
        .txdata, .txcharisk, .rxdata, .rxcharisk, .rxdisperr, .rxnotintable, .rxbyteisaligned,
        .locked, .code_errors, .lock_losses, .rx_words, .tx_data_words, .tx_idle_words, .rx_overflows
    );

    // -----------------------------------------------------------------
    // Channel model (clk_link)
    // -----------------------------------------------------------------
    logic [17:0] pipe [$];
    int  drop_pct = 0, ins_pct = 0, err_left = 0;
    always @(posedge clk_link) begin
        logic [17:0] w;
        bit idle;
        idle = (txcharisk == 2'b01);
        // elastic buffer: occasionally lose or repeat an idle word
        if (!(idle && $urandom_range(0, 99) < drop_pct)) pipe.push_back({txcharisk, txdata});
        if (idle && $urandom_range(0, 99) < ins_pct) pipe.push_back({txcharisk, txdata});
        // a word every cycle: when the buffer runs low (idles deleted), an idle
        // is inserted, as the elastic buffer does
        w = (pipe.size() > 6) ? pipe.pop_front() : {2'b01, 16'h50BC};
        rxcharisk <= w[17:16];
        rxdata    <= w[15:0];
        // code errors only on idle words, so the data stays comparable
        if (err_left > 0 && w[17:16] == 2'b01) begin
            rxdisperr <= 2'b01;
            err_left  <= err_left - 1;
        end else begin
            rxdisperr <= 2'b00;
        end
    end

    // -----------------------------------------------------------------
    // Data source and sink
    // -----------------------------------------------------------------
    logic [7:0] sent [$];
    int  to_send = 0, in_pct = 100;
    always @(posedge clk_sys) begin
        if (rst) begin
            s_tvalid <= 1'b0;
        end else if (!s_tvalid || s_tready) begin
            if (to_send > 0 && $urandom_range(0, 99) < in_pct) begin
                logic [7:0] b;
                b = $urandom;
                s_tvalid <= 1'b1;
                s_tdata  <= b;
                sent.push_back(b);
                to_send  <= to_send - 1;
            end else begin
                s_tvalid <= 1'b0;
            end
        end
    end

    int got = 0, data_err = 0;
    always @(posedge clk_sys) begin
        if (!rst && m_tvalid) begin
            logic [7:0] e;
            e = (got < sent.size()) ? sent[got] : 8'hxx;
            if (got >= sent.size() || m_tdata !== e) begin
                data_err++;
                if (data_err <= 5) $display("  rx byte %0d: got %02h, expected %02h", got, m_tdata, e);
            end
            got++;
        end
    end

    // clock-correction interval on the TX side: no run of data words longer than CC_INTERVAL - 1
    int run = 0, max_run = 0;
    always @(posedge clk_link) begin
        if (!rst) begin
            if (txcharisk == 2'b00) begin
                run++;
                if (run > max_run) max_run = run;
            end else begin
                run = 0;
            end
        end
    end

    // -----------------------------------------------------------------
    // Sequence
    // -----------------------------------------------------------------
    int errors = 0;
    task automatic check(string what, longint got_v, longint exp_v);
        if (got_v != exp_v) begin
            errors++;
            $display("  %s = %0d, expected %0d", what, got_v, exp_v);
        end
    endtask

    task automatic send(int n, int pct);
        in_pct = pct;
        to_send = n;
        wait (to_send == 0);
        while (got < sent.size()) @(posedge clk_sys);
    endtask

    initial begin
        int t;
        repeat (20) @(posedge clk_link);
        rst <= 1'b0;
        repeat (50) @(posedge clk_link);
        check("locked before alignment", locked, 0);
        rxbyteisaligned <= 1'b1;
        t = 0;
        while (!locked) begin
            @(posedge clk_link);
            t++;
        end
        if (t < 1024 || t > 1100) begin errors++; $display("  locked after %0d words", t); end

        // data, with gaps and elastic-buffer idle deletion / insertion
        drop_pct = 20;
        ins_pct  = 20;
        send(20000, 100);
        send(5000, 30);
        drop_pct = 0;
        ins_pct  = 0;
        check("rx bytes", got, 25000);
        if (max_run > CC_INTERVAL - 1) begin errors++; $display("  data run of %0d words", max_run); end
        if (max_run < CC_INTERVAL - 1) begin errors++; $display("  data never reached the CC interval (%0d)", max_run); end

        // 8 code errors in a window: lock kept
        repeat (2000) @(posedge clk_link);
        err_left = 8;
        repeat (300) @(posedge clk_link);
        check("lock losses after 8 errors", lock_losses, 0);
        check("locked after 8 errors", locked, 1);
        // 9 more in a fresh window: lock lost, then regained
        repeat (1100) @(posedge clk_link);
        err_left = 9;
        repeat (300) @(posedge clk_link);
        check("lock losses after 9 errors", lock_losses, 1);
        t = 0;
        while (!locked && t < 5000) begin
            @(posedge clk_link);
            t++;
        end
        check("relocked", locked, 1);
        check("code errors", code_errors, 17);

        send(4000, 70);
        check("rx bytes after relock", got, 29000);

        // loss of alignment
        rxbyteisaligned <= 1'b0;
        repeat (10) @(posedge clk_link);
        check("locked without alignment", locked, 0);
        check("lock losses", lock_losses, 2);
        check("rx overflows", rx_overflows, 0);

        errors += data_err;
        if (errors == 0)
            $display("PASS: tb_link, %0d bytes, %0d data / %0d idle words sent, longest data run %0d",
                     got, tx_data_words, tx_idle_words, max_run);
        else
            $display("FAIL: tb_link: %0d errors", errors);
        $finish;
    end

    initial begin
        #10ms;
        $display("FAIL: tb_link timeout");
        $finish;
    end
endmodule
