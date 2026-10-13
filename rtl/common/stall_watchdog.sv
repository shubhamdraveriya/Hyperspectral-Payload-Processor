`timescale 1ns / 1ps

// Pipeline watchdog (SPEC RAD-05): a stage that has work pending but makes no
// progress for `timeout` consecutive cycles is stalled. `stall` pulses once per
// stall; `stalled` stays high until the stage makes progress again or has
// nothing pending. timeout 0 turns the watchdog off.
module stall_watchdog (
    input  logic        clk,
    input  logic        rst,
    input  logic [31:0] timeout,
    input  logic        pending,        // the stage has work it cannot hand on
    input  logic        progress,       // a transfer happened this cycle
    output logic        stalled,
    output logic        stall
);
    logic [31:0] cnt;

    always_ff @(posedge clk) begin
        stall <= 1'b0;
        if (rst || timeout == 32'd0 || !pending || progress) begin
            cnt     <= '0;
            stalled <= 1'b0;
        end else if (!stalled) begin
            if (cnt == timeout - 32'd1) begin
                stalled <= 1'b1;
                stall   <= 1'b1;
            end else begin
                cnt <= cnt + 32'd1;
            end
        end
    end
endmodule
