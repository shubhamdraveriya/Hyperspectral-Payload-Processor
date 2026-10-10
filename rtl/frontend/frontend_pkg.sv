`timescale 1ns / 1ps

// Configuration of the sensor front end (SPEC.md 6.1 - 6.3). Static while the
// sensor runs (written between acquisitions).
//
// Sample streams between the blocks: 16-bit data, user[0] = first sample of a
// scene, user[1] = first sample of a line, last = last sample of a line.
package frontend_pkg;

    localparam logic [1:0] SRC_DMA  = 2'd0;
    localparam logic [1:0] SRC_RAMP = 2'd1;
    localparam logic [1:0] SRC_LFSR = 2'd2;

    typedef struct packed {
        logic [15:0] nx, ny, nz;       // pixels per line, lines per scene, bands
        logic [4:0]  depth;            // sample depth D, 2..16
        logic [31:0] line_period;      // clk_sensor cycles between line starts
        logic [1:0]  source;           // SRC_DMA, SRC_RAMP, SRC_LFSR
        logic        run;              // emit lines while set (a started line completes)
        logic        rc_bypass;        // radiometric correction off
    } fe_cfg_t;

    // Status counters of the sensor emulator (clk_sensor domain)
    typedef struct packed {
        logic [23:0] line_timestamp;   // free-running count of line starts
        logic [23:0] scene_timestamp;  // line_timestamp when the current scene started
        logic [31:0] lines_emitted;
        logic [31:0] scenes_started;
        logic [31:0] underrun_samples; // sent as 0: DMA data not there in time
        logic [31:0] period_overruns;  // line period shorter than a line
        logic [31:0] dropped_samples;  // emulator output FIFO full
    } se_status_t;

    // Status counters of the correction and corner turn (clk_sys domain)
    typedef struct packed {
        logic [31:0] scene_clamped;    // current scene
        logic [31:0] scene_replaced;   // current scene
        logic [31:0] lines_out;
        logic [31:0] dropped_lines;    // sent as fill lines (or lost, below)
        logic [31:0] unfilled_lines;
    } fe_status_t;

endpackage
