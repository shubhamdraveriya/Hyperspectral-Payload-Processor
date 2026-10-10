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
        logic [31:0] lines;            // lines per run; 0: until run is cleared
        logic [1:0]  source;           // SRC_DMA, SRC_RAMP, SRC_LFSR
        logic        run;              // a rising edge starts a run (a started line completes)
        logic        rc_bypass;        // radiometric correction off
    } fe_cfg_t;

    // Status of the sensor emulator (clk_sensor domain). Counters saturate.
    typedef struct packed {
        logic        busy;             // a run is in progress
        logic        done;             // the run has emitted its `lines` lines
        logic [23:0] line_timestamp;   // free-running count of line starts
        logic [23:0] scene_timestamp;  // line_timestamp when the current scene started
        logic [31:0] lines_emitted;
        logic [31:0] scenes_started;
        logic [31:0] underrun_samples; // sent as 0: DMA data not there in time
        logic [31:0] period_overruns;  // line period shorter than a line
        logic [31:0] dropped_samples;  // emulator output FIFO full
    } se_status_t;

    // Status of the correction and corner turn (clk_sys domain). Counters saturate.
    typedef struct packed {
        logic [31:0] scene_clamped;    // current scene
        logic [31:0] scene_replaced;   // current scene
        logic [31:0] tbl_words;        // 128-bit beats in the last calibration table load
        logic [31:0] lines_out;
        logic [31:0] dropped_lines;    // sent as fill lines (or lost, below)
        logic [31:0] unfilled_lines;
    } fe_status_t;

    // Calibration table debug access (SPEC RC-02), clk_sys domain
    typedef struct packed {
        logic        valid;            // one-cycle request
        logic        write;            // 1: write wdata, 0: read
        logic [17:0] addr;             // entry z*Nx + x
        logic [32:0] wdata;            // {bad, gain, dark}
    } tbl_cmd_t;

    function automatic logic [31:0] sat_inc(input logic [31:0] v);
        return (v == 32'hFFFF_FFFF) ? v : v + 32'd1;
    endfunction

endpackage
