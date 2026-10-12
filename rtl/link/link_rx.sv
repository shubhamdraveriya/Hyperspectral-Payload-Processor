`timescale 1ns / 1ps

// Link receiver (SPEC.md 6.9, LNK-04/LNK-05), clk_link domain, 16-bit words
// from the GTH after 8b/10b decoding, comma alignment (K28.5 in byte 0) and
// clock correction.
//
// Lock: after byte alignment, 1024 consecutive words without a code error
// (disparity error or not in table) -> LOCKED. In LOCKED, more than 8 code
// errors within a 1024-word window (fixed windows), or losing alignment,
// returns to hunting and counts a lock loss. Data words (no K character) are
// passed on only while LOCKED; idle and clock-correction words are dropped.
module link_rx (
    input  logic        clk,
    input  logic        rst,

    input  logic [15:0] rxdata,
    input  logic [1:0]  rxcharisk,
    input  logic [1:0]  rxdisperr,
    input  logic [1:0]  rxnotintable,
    input  logic        rxbyteisaligned,

    output logic [15:0] m_data,                 // to the FIFO towards clk_sys
    output logic        m_wr,

    output logic        locked,
    output logic [31:0] code_errors,            // words with a code error (saturating)
    output logic [31:0] lock_losses,
    output logic [31:0] data_words
);
    localparam int LOCK_WORDS = 1024;
    localparam int MAX_ERRORS = 8;

    typedef enum logic [1:0] {HUNT, CHECK, LOCKED} state_t;
    state_t      state;
    logic [9:0]  count;                         // good words (CHECK), window position (LOCKED)
    logic [3:0]  errs;                          // code errors in the window

    wire code_err = |(rxdisperr | rxnotintable);
    wire is_data  = (rxcharisk == 2'b00);

    assign locked = (state == LOCKED);

    always_ff @(posedge clk) begin
        if (rst) begin
            state       <= HUNT;
            count       <= '0;
            errs        <= '0;
            m_wr        <= 1'b0;
            code_errors <= '0;
            lock_losses <= '0;
            data_words  <= '0;
        end else begin
            m_wr <= 1'b0;
            if (code_err && code_errors != 32'hFFFF_FFFF) code_errors <= code_errors + 32'd1;

            case (state)
                HUNT: begin
                    count <= '0;
                    if (rxbyteisaligned) state <= CHECK;
                end
                CHECK: begin
                    if (!rxbyteisaligned) begin
                        state <= HUNT;
                    end else if (code_err) begin
                        count <= '0;
                    end else begin
                        count <= count + 10'd1;
                        if (count == 10'(LOCK_WORDS - 1)) begin
                            state <= LOCKED;
                            count <= '0;
                            errs  <= '0;
                        end
                    end
                end
                LOCKED: begin
                    // window: count 0 .. 1023 (count wraps), errors so far in errs
                    logic [3:0] e;
                    e = (count == 10'd0) ? 4'd0 : errs;
                    count <= count + 10'd1;
                    errs  <= e + 4'(code_err);
                    if (!rxbyteisaligned || (code_err && e == 4'(MAX_ERRORS))) begin
                        state       <= HUNT;
                        lock_losses <= lock_losses + 32'd1;
                    end else if (is_data) begin
                        m_wr       <= 1'b1;
                        m_data     <= rxdata;
                        data_words <= data_words + 32'd1;
                    end
                end
                default: state <= HUNT;
            endcase
        end
    end
endmodule
