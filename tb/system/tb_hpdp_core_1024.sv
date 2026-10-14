`timescale 1ns / 1ps

// The system test at the NX_MAX 1024 build's size (SYS-04, docs/nx_max_1024.md):
// hpdp_core with NX_MAX 1024 and NZ_MAX 256, so the calibration table is in
// pairs and the corner-turn and line buffers have their full 1024-pixel size.
module tb_hpdp_core_1024;
    tb_hpdp_core #(.NX_MAX(1024), .NZ_MAX(256)) t ();
endmodule
