`timescale 1ns / 1ps

// Memory ECC (SPEC RAD-02): error injection requests and error events of the
// UltraRAM / block RAM memories that run with the primitives' built-in ECC.
package ecc_pkg;

    // Error injection: arm (one-cycle pulse) makes the next write of the word
    // that holds addr (the memory's sample or entry address) store a
    // single-bit error, or a double-bit error when dbe is set.
    typedef struct packed {
        logic        arm;
        logic        dbe;
        logic [19:0] addr;             // corner-turn buffers: 2 * 2^18 samples at NX_MAX 1024
    } ecc_inj_t;

    // Events: sbe / dbe pulse once per read that returned a corrected word /
    // an uncorrectable word; pending while an armed injection waits for a write.
    typedef struct packed {
        logic pending;
        logic sbe;
        logic dbe;
    } ecc_evt_t;

endpackage
