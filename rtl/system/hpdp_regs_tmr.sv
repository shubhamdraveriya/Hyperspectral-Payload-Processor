`timescale 1ns / 1ps

// The register block (regs/hpdp.rdl) in three copies (SPEC RAD-03:
// configuration registers protected by TMR). Every AXI4-Lite transaction goes
// to all three, which stay cycle for cycle identical; every output (the
// configuration and pulses to the hardware, read data, the AXI handshake) is
// the bitwise majority, so an upset in one copy's registers is masked, also on
// reads. `mismatch` is high while the copies disagree; a copy is healed when
// software writes the register again (hpdp_run rewrites the configuration for
// every job). The copies are kept apart through synthesis (DONT_TOUCH).
module hpdp_regs_tmr (
    input  logic        clk,
    input  logic        rst,

    output logic        s_axil_awready,
    input  logic        s_axil_awvalid,
    input  logic [19:0] s_axil_awaddr,
    input  logic [2:0]  s_axil_awprot,
    output logic        s_axil_wready,
    input  logic        s_axil_wvalid,
    input  logic [31:0] s_axil_wdata,
    input  logic [3:0]  s_axil_wstrb,
    input  logic        s_axil_bready,
    output logic        s_axil_bvalid,
    output logic [1:0]  s_axil_bresp,
    output logic        s_axil_arready,
    input  logic        s_axil_arvalid,
    input  logic [19:0] s_axil_araddr,
    input  logic [2:0]  s_axil_arprot,
    input  logic        s_axil_rready,
    output logic        s_axil_rvalid,
    output logic [31:0] s_axil_rdata,
    output logic [1:0]  s_axil_rresp,

    input  hpdp_regs_pkg::hpdp_regs__in_t  hwif_in,
    output hpdp_regs_pkg::hpdp_regs__out_t hwif_out,
    output logic        mismatch
);
    // AXI outputs of each copy, packed: awready, wready, bvalid, bresp, arready,
    // rvalid, rdata, rresp
    localparam int AW = 1 + 1 + 1 + 2 + 1 + 1 + 32 + 2;
    logic [AW-1:0] axo [3];
    hpdp_regs_pkg::hpdp_regs__out_t o [3];

    for (genvar c = 0; c < 3; c++) begin : g_copy
        (* DONT_TOUCH = "true", KEEP_HIERARCHY = "yes" *)
        hpdp_regs u_regs (
            .clk, .rst,
            .s_axil_awready (axo[c][40]), .s_axil_awvalid, .s_axil_awaddr, .s_axil_awprot,
            .s_axil_wready  (axo[c][39]), .s_axil_wvalid, .s_axil_wdata, .s_axil_wstrb,
            .s_axil_bready, .s_axil_bvalid (axo[c][38]), .s_axil_bresp (axo[c][37:36]),
            .s_axil_arready (axo[c][35]), .s_axil_arvalid, .s_axil_araddr, .s_axil_arprot,
            .s_axil_rready, .s_axil_rvalid (axo[c][34]), .s_axil_rdata (axo[c][33:2]),
            .s_axil_rresp   (axo[c][1:0]),
            .hwif_in, .hwif_out (o[c])
        );
    end

    logic [AW-1:0] ax;
    assign ax = (axo[0] & axo[1]) | (axo[0] & axo[2]) | (axo[1] & axo[2]);
    assign {s_axil_awready, s_axil_wready, s_axil_bvalid, s_axil_bresp, s_axil_arready,
            s_axil_rvalid, s_axil_rdata, s_axil_rresp} = ax;

    // (PeakRDL's hwif structs are unpacked: packed into vectors for the vote)
    localparam int OW = $bits(hpdp_regs_pkg::hpdp_regs__out_t);
    logic [OW-1:0] ov [3], ovote;

    always_comb begin
        for (int c = 0; c < 3; c++) ov[c] = {>>{o[c]}};
        ovote = (ov[0] & ov[1]) | (ov[0] & ov[2]) | (ov[1] & ov[2]);
        {>>{hwif_out}} = ovote;
    end

    // AXI response fields count only while valid: rdata / bresp keep the last value
    logic [AW-1:0] axl [3];
    always_comb
        for (int c = 0; c < 3; c++) begin
            axl[c] = axo[c];
            if (!axo[c][34]) axl[c][33:0]  = '0;    // rdata, rresp without rvalid
            if (!axo[c][38]) axl[c][37:36] = '0;    // bresp without bvalid
        end

    assign mismatch = (ov[0] != ov[1]) || (ov[0] != ov[2]) || (axl[0] != axl[1]) || (axl[0] != axl[2]);
endmodule
