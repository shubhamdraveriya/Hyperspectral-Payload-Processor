`timescale 1ns / 1ps

// Simple dual-port RAM of WIDTH-bit words (a multiple of 64) with the memory
// primitive's built-in ECC (SPEC RAD-02, RAD-03): single-bit errors are
// corrected on read, double-bit errors detected (per 64-bit slice; the flags
// cover the whole word). Words are written whole (no byte writes with ECC).
//
// Reads take LATENCY cycles. With LATENCY 2, ce advances the read pipeline
// (memory latch and output register) and holds it otherwise; with a longer
// latency ce must stay high, because the XPM pipeline stages in between
// advance on their own. re marks the reads that count: sbe / dbe pulse in
// the cycle such a read's data appears, if it was corrected / is
// uncorrectable. inj_sbe / inj_dbe make the write in the same cycle store a
// single / double-bit error.
module ecc_ram #(
    parameter int WIDTH   = 64,         // a multiple of 64
    parameter int DEPTH   = 1024,       // words
    parameter     STYLE   = "ultra",    // "ultra" or "block"
    parameter int LATENCY = 2           // >= 2
) (
    input  logic                     clk,
    input  logic                     rst,
    input  logic                     ce,
    input  logic                     we,
    input  logic [$clog2(DEPTH)-1:0] waddr,
    input  logic [WIDTH-1:0]         wdata,
    input  logic                     inj_sbe,
    input  logic                     inj_dbe,
    input  logic                     re,
    input  logic [$clog2(DEPTH)-1:0] raddr,
    output logic [WIDTH-1:0]         rdata,
    output logic                     sbe,
    output logic                     dbe
);
    localparam int AB = $clog2(DEPTH);

    logic sbiterr, dbiterr;

    xpm_memory_sdpram #(
        .MEMORY_SIZE        (DEPTH * WIDTH),
        .MEMORY_PRIMITIVE   (STYLE),
        .CLOCKING_MODE      ("common_clock"),
        .ECC_MODE           ("both_encode_and_decode"),
        .MEMORY_INIT_FILE   ("none"),
        .MEMORY_INIT_PARAM  (""),
        .USE_MEM_INIT       (0),
        .WAKEUP_TIME        ("disable_sleep"),
        .MESSAGE_CONTROL    (0),
        .WRITE_DATA_WIDTH_A (WIDTH),
        .BYTE_WRITE_WIDTH_A (WIDTH),
        .ADDR_WIDTH_A       (AB),
        .READ_DATA_WIDTH_B  (WIDTH),
        .ADDR_WIDTH_B       (AB),
        .READ_LATENCY_B     (LATENCY),
        .WRITE_MODE_B       ("read_first")
    ) u_mem (
        .sleep          (1'b0),
        .clka           (clk),
        .ena            (we),
        .wea            (we),
        .addra          (waddr),
        .dina           (wdata),
        .injectsbiterra (inj_sbe),
        .injectdbiterra (inj_dbe),
        .clkb           (clk),
        .rstb           (1'b0),
        .enb            (ce),
        .regceb         (ce),
        .addrb          (raddr),
        .doutb          (rdata),
        .sbiterrb       (sbiterr),
        .dbiterrb       (dbiterr)
    );

    // Reads that count, along the pipeline; loaded: such a read's data (and
    // its error flags) reached the output in the last clock edge
    logic [LATENCY-2:0] rv;
    logic               loaded;

    always_ff @(posedge clk) begin
        if (rst) begin
            rv     <= '0;
            loaded <= 1'b0;
        end else begin
            if (ce) rv <= (LATENCY - 1)'({rv, re});
            loaded <= ce && rv[LATENCY - 2];
        end
    end

    assign sbe = loaded && sbiterr && !dbiterr;
    assign dbe = loaded && dbiterr;

    // synthesis translate_off
    always_ff @(posedge clk)
        if (!rst && LATENCY > 2 && !ce)
            $error("ecc_ram: ce must stay high with LATENCY %0d", LATENCY);
    // synthesis translate_on
endmodule
