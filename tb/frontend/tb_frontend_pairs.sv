`timescale 1ns / 1ps

// The front-end and fault-campaign benches with the calibration table in pairs
// (radiometric_corr TABLE_PAIRS = 1: two entries' dark and gain per ECC word,
// bad flags in a memory of their own), the layout of builds above
// NX_MAX * NZ_MAX = 512 * 256 (SYS-04), at the benches' small size.
module tb_frontend_pairs;
    tb_frontend #(.TABLE_PAIRS(1'b1)) t ();
endmodule

module tb_fault_pairs;
    tb_fault #(.TABLE_PAIRS(1'b1)) t ();
endmodule
