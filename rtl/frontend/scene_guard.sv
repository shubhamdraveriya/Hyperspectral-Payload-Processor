`timescale 1ns / 1ps

// Scene guard between the front end and the compressor: every scene the
// compressor sees has exactly Nx*Ny*Nz samples.
//
// The corner turn keeps scene geometry with fill lines (CT-04), but when even a
// fill line cannot be sent (sustained overload, CT.UNFILLED_LINES) a scene
// arrives short. The compressor restarts its position counters at the next
// first-of-scene marker but ends an image only after a full scene, so the short
// scene would merge with the following ones. Here, a first-of-scene marker that
// arrives before the current scene is complete holds the input while zero
// samples complete the short scene; the compressor then ends it normally and the
// framing recovers at once. The scene's packets already carry the lines-dropped
// flag. The corner turn moves the marker of a lost first line to the next line
// it sends, so a scene start is only missed when a whole scene is lost (its
// samples are then counted into the scene before, as the compressor does).
//
// Stream: tdata[15:0], tuser[0] first sample of a scene; tuser[1] and tlast pass
// through (zero on padding samples: the compressor does not use them).
module scene_guard (
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] nx, ny, nz,
    input  logic        cnt_clr,

    input  logic [15:0] s_tdata,
    input  logic [1:0]  s_tuser,
    input  logic        s_tlast,
    input  logic        s_tvalid,
    output logic        s_tready,

    output logic [15:0] m_tdata,
    output logic [1:0]  m_tuser,
    output logic        m_tlast,
    output logic        m_tvalid,
    input  logic        m_tready,

    output logic [31:0] padded_scenes,
    output logic [31:0] padded_samples
);
    logic [39:0] total, n;              // samples per scene, samples of this scene so far
    logic        pad;

    // Nx*Ny*Nz, pipelined (3 cycles; the geometry is static while scenes flow)
    logic [31:0] nxy;
    logic [39:0] nxyz;
    always_ff @(posedge clk) begin
        nxy   <= 32'(nx) * 32'(ny);
        nxyz  <= 40'(nxy) * 40'(nz);
        total <= nxyz;
    end

    // a new scene starts while this one is incomplete
    wire early = !pad && s_tvalid && s_tuser[0] && n != 40'd0;

    assign s_tready = !pad && !early && m_tready;
    assign m_tvalid = pad || (s_tvalid && !early);
    assign m_tdata  = pad ? 16'd0 : s_tdata;
    assign m_tuser  = pad ? 2'b00 : s_tuser;
    assign m_tlast  = pad ? 1'b0  : s_tlast;

    wire fire = m_tvalid && m_tready;
    wire done = (n + 40'd1 >= total);   // this sample completes the scene

    always_ff @(posedge clk) begin
        if (rst) begin
            n              <= '0;
            pad            <= 1'b0;
            padded_scenes  <= '0;
            padded_samples <= '0;
        end else begin
            if (early) begin
                pad <= 1'b1;
                if (padded_scenes != 32'hFFFF_FFFF) padded_scenes <= padded_scenes + 32'd1;
            end
            if (fire) begin
                n <= done ? 40'd0 : n + 40'd1;
                if (pad && done) pad <= 1'b0;
                if (pad && padded_samples != 32'hFFFF_FFFF) padded_samples <= padded_samples + 32'd1;
            end
            if (cnt_clr) begin
                padded_scenes  <= '0;
                padded_samples <= '0;
            end
        end
    end
endmodule
