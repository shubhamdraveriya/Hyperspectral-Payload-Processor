`timescale 1ns / 1ps

// Types and constants of the CCSDS 123.0-B-2 compressor (SPEC.md 6.4).
// Supported: lossless, unsigned samples, D <= 16, BIP order (BI with M = Nz),
// full / reduced prediction, all local-sum types, P <= 15, default weight
// initialization, sample-adaptive entropy coder.
package c123_pkg;

    localparam int MAX_P  = 15;              // previous bands used for prediction
    localparam int NWS    = 3 + MAX_P;       // weight slots: N, W, NW, then bands z-1 .. z-15
    localparam int DW     = 19;              // local difference, signed: |4s - sigma| < 2^18
    localparam int WW     = 22;              // weight, signed: omega + 3 bits for omega <= 19
    localparam int PRW    = DW + WW;         // product
    localparam int SUMW   = PRW + 5;         // sum of 18 products
    localparam int RAWW   = 48;              // prediction before the register-size wrap
    localparam int CWW    = 48;              // codeword value bits (header chunks use all 48)
    localparam int NZ_MIN = 16;              // weight RAM read -> write-back distance + 1

    typedef struct packed {
        logic [15:0]       nx, ny, nz;       // image size; nx <= NX_MAX, NZ_MIN <= nz <= NZ_MAX
        logic [4:0]        depth;            // D, 2..16
        logic [3:0]        bands;            // P, 0..15
        logic              full;             // full prediction mode (0: reduced)
        logic [1:0]        local_sum;        // 0 nbr wide, 1 nbr narrow, 2 col wide, 3 col narrow
        logic [4:0]        omega;            // weight resolution, 4..19
        logic [6:0]        reg_size;         // R, max(32, D + omega + 2)..64
        logic signed [4:0] v_min, v_max;     // weight update scaling exponent, -6..9
        logic [3:0]        t_inc_log2;       // 4..11
        logic [5:0]        u_max;            // 8..32
        logic [3:0]        gamma0;           // 1..8
        logic [3:0]        gamma_star;       // max(4, gamma0 + 1)..11
        logic [3:0]        k_init;           // accumulator initialization constant, 0..D-2
        logic [3:0]        word_bytes;       // output word size B, 1..8
        logic              raw;              // bypass: pack samples at D bits, no header
        logic [7:0]        user_data;
    } cfg_t;

    // Per-scene constants derived from cfg_t
    typedef struct packed {
        logic [15:0]       smid, smax;
        logic [RAWW-1:0]   hr_offset;        // 2^(omega+2) * smid + 2^(omega+1)
        logic [RAWW-1:0]   hr_max;           // 2^(omega+2) * smax + 2^(omega+1)
        logic [WW-1:0]     w_init;           // first spectral weight: 7/8 * 2^omega
        logic [10:0]       count_init;       // 2^gamma0
        logic [31:0]       acc_init;         // ((3 * 2^(K+6) - 49) * 2^gamma0) >> 7
        logic [10:0]       count_limit;      // 2^gamma_star - 1
    } consts_t;

    // Neighbourhood of one sample (c123_neighborhood -> c123_localdiff)
    typedef struct packed {
        logic [15:0]       s, n, ne, nw, w;
        logic [15:0]       w_prev;           // W of the previous band, same pixel
        logic [15:0]       s_prev;           // the previous band, same pixel
        logic [15:0]       z;
        logic              first_row, first_col, last_col;
        logic              t0, t1;           // spatial index t = 0 / t = 1
        logic              sos, eos;         // first / last sample of the scene
        logic signed [5:0] rho;              // weight update scaling exponent rho(t)
    } nb_t;

    // Local differences (c123_localdiff -> c123_predictor)
    typedef struct packed {
        logic [NWS-1:0][DW-1:0] u;           // local difference vector, unused slots zero
        logic [17:0]       sigma;
        logic [15:0]       s, s_prev, z;
        logic              t0, t1, sos, eos;
        logic signed [5:0] rho;
    } ld_t;

    // Mapped residual (c123_predictor -> c123_coder)
    typedef struct packed {
        logic [15:0]       delta, s, z;
        logic              t0, t1, sos, eos;
    } pr_t;

    // Codeword (c123_coder -> c123_packer): len bits of val, MSB first
    typedef struct packed {
        logic [5:0]        len;
        logic [CWW-1:0]    val;
        logic              sos, eos;
    } cw_t;

endpackage
