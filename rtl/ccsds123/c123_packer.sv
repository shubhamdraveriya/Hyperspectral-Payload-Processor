`timescale 1ns / 1ps

// Packs codewords MSB-first into 64-bit AXI-Stream words.
//   - before the first codeword of a scene: the 19-byte header (not in raw mode)
//   - after the last codeword: zero fill to a byte boundary, then to a multiple
//     of B bytes for the whole compressed image (5.3 / 5.4.4), tlast on its last word
// Byte order: tdata[7:0] is the first byte of the stream.
module c123_packer import c123_pkg::*; (
    input  logic         clk,
    input  logic         rst,
    input  cfg_t         cfg,
    input  logic [151:0] header,        // MSB first

    input  cw_t          cw,
    input  logic         cw_valid,
    output logic         cw_pop,

    output logic [63:0]  m_tdata,
    output logic [7:0]   m_tkeep,
    output logic         m_tlast,
    output logic         m_tvalid,
    input  logic         m_tready
);
    typedef enum logic [2:0] {RUN, HDR, PAD, FLUSH_HOLD, FLUSH_LAST} state_t;
    state_t state;

    logic [127:0] acc;                  // pending bits, left aligned
    logic [6:0]   n;                    // number of pending bits (< 64 between steps)
    logic [1:0]   hdr_idx;
    logic         hdr_done;
    logic [2:0]   bits_mod8;            // image length mod 8 bits
    logic [3:0]   phase;                // image length in bytes mod B
    logic [63:0]  hold;                 // last full word, sent once the next one exists
    logic         hold_v;

    // ---------------------------------------------------------------------
    // Output FIFO
    // ---------------------------------------------------------------------
    logic [72:0] of_din;
    logic        of_wr;
    logic        of_empty;
    logic [3:0]  of_count;
    logic [72:0] of_dout;

    sync_fifo #(.WIDTH(73), .DEPTH(8)) u_out (
        .clk   (clk),
        .rst   (rst),
        .din   (of_din),
        .wr_en (of_wr),
        .rd_en (m_tvalid && m_tready),
        .dout  (of_dout),
        .empty (of_empty),
        .full  (),
        .count (of_count)
    );

    function automatic logic [63:0] byte_swap(logic [63:0] w);
        for (int b = 0; b < 8; b++) byte_swap[8 * b +: 8] = w[63 - 8 * b -: 8];
    endfunction

    assign m_tvalid = !of_empty;
    assign m_tdata  = of_dout[63:0];
    assign m_tkeep  = of_dout[71:64];
    assign m_tlast  = of_dout[72];

    // x mod B for x < 32 and B = 1..8 (constant divisors: small LUT logic)
    function automatic logic [3:0] mod_b(logic [4:0] x, logic [3:0] b);
        case (b)
            4'd2:    return {3'd0, x[0]};
            4'd3:    return 4'(x % 5'd3);
            4'd4:    return {2'd0, x[1:0]};
            4'd5:    return 4'(x % 5'd5);
            4'd6:    return 4'(x % 5'd6);
            4'd7:    return 4'(x % 5'd7);
            4'd8:    return {1'b0, x[2:0]};
            default: return 4'd0;       // B = 1
        endcase
    endfunction

    // ---------------------------------------------------------------------
    // One step per cycle: append up to 48 bits, emit at most one 64-bit word
    // ---------------------------------------------------------------------
    wire room = (of_count <= 4'd6);     // the step may write the FIFO once

    logic [5:0]   app_len;              // bits appended this step
    logic [47:0]  app_val;
    logic         step;
    logic [127:0] acc_a;
    logic [7:0]   n_a;
    logic [6:0]   pad_bits;

    always_comb begin
        // zero fill at the end of the image
        logic [3:0] ph;
        ph = mod_b({1'b0, phase} + (bits_mod8 != 3'd0), cfg.word_bytes);
        pad_bits = 7'((8 - bits_mod8) & 7) + 7'({3'd0, mod_b(5'(cfg.word_bytes) - 5'(ph), cfg.word_bytes)} << 3);

        app_len = '0;
        app_val = '0;
        step    = 1'b0;
        cw_pop  = 1'b0;
        case (state)
            RUN: if (cw_valid && room) begin
                if (cw.sos && !cfg.raw && !hdr_done) begin
                    step = 1'b0;            // header first, see HDR
                end else begin
                    step    = 1'b1;
                    cw_pop  = 1'b1;
                    app_len = cw.len;
                    app_val = cw.val;
                end
            end
            HDR: if (room) begin
                step = 1'b1;
                case (hdr_idx)
                    2'd0: begin app_len = 6'd48; app_val = header[151:104]; end
                    2'd1: begin app_len = 6'd48; app_val = header[103:56];  end
                    2'd2: begin app_len = 6'd48; app_val = header[55:8];    end
                    default: begin app_len = 6'd8; app_val = {40'd0, header[7:0]}; end
                endcase
            end
            default: ;
        endcase

        // append (val has at most app_len significant bits)
        acc_a = acc | ({80'd0, app_val} << (8'd128 - {1'b0, n} - {2'b00, app_len}));
        n_a   = {1'b0, n} + {2'b00, app_len};
        if (state == PAD) n_a = {1'b0, n} + {1'b0, pad_bits};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state     <= RUN;
            acc       <= '0;
            n         <= '0;
            hdr_idx   <= '0;
            hdr_done  <= 1'b0;
            bits_mod8 <= '0;
            phase     <= '0;
            hold_v    <= 1'b0;
            of_wr     <= 1'b0;
        end else begin
            of_wr <= 1'b0;

            // RUN / HDR / PAD: append, emit a word when 64 bits are pending
            if ((step || (state == PAD && room))) begin
                if (n_a >= 8'd64) begin
                    if (hold_v) begin
                        of_din <= {1'b0, 8'hFF, byte_swap(hold)};
                        of_wr  <= 1'b1;
                    end
                    hold   <= acc_a[127:64];
                    hold_v <= 1'b1;
                    acc    <= acc_a << 64;
                    n      <= 7'(n_a - 8'd64);
                end else begin
                    acc <= acc_a;
                    n   <= 7'(n_a);
                end
                if (state != PAD) begin
                    bits_mod8 <= 3'(bits_mod8 + app_len);
                    phase     <= mod_b(5'(phase) + 5'((bits_mod8 + app_len) >> 3), cfg.word_bytes);
                end
            end

            case (state)
                RUN: if (cw_pop) begin
                         hdr_done <= 1'b0;
                         if (cw.eos) state <= PAD;
                     end else if (cw_valid && room && cw.sos && !cfg.raw && !hdr_done) begin
                         state   <= HDR;
                         hdr_idx <= '0;
                     end
                HDR: if (room) begin
                         hdr_idx <= hdr_idx + 2'd1;
                         if (hdr_idx == 2'd3) begin
                             state    <= RUN;
                             hdr_done <= 1'b1;
                         end
                     end
                PAD: if (room) state <= FLUSH_HOLD;
                FLUSH_HOLD: if (room) begin
                    // n is a whole number of bytes now
                    if (n != 7'd0) begin
                        if (hold_v) begin
                            of_din <= {1'b0, 8'hFF, byte_swap(hold)};
                            of_wr  <= 1'b1;
                        end
                        hold   <= acc[127:64];
                        hold_v <= 1'b1;
                        state  <= FLUSH_LAST;
                    end else begin
                        of_din <= {1'b1, 8'hFF, byte_swap(hold)};
                        of_wr  <= 1'b1;
                        hold_v <= 1'b0;
                        state  <= RUN;
                        acc    <= '0;
                        bits_mod8 <= '0;
                        phase     <= '0;
                    end
                end
                FLUSH_LAST: if (room) begin
                    of_din    <= {1'b1, 8'((1 << (n >> 3)) - 1), byte_swap(hold)};
                    of_wr     <= 1'b1;
                    hold_v    <= 1'b0;
                    acc       <= '0;
                    n         <= '0;
                    bits_mod8 <= '0;
                    phase     <= '0;
                    state     <= RUN;
                end
                default: state <= RUN;
            endcase
        end
    end
endmodule
