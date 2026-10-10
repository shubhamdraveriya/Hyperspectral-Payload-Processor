# Out-of-context implementation of the sensor front end (sensor emulator,
# radiometric correction, corner turn), for timing, resources and CDC.
#
#   vivado -mode batch -source vivado/ooc_frontend.tcl [-tclargs <clk_sys period ns>]
#
# Production sizes (NX_MAX = 512, NZ_MAX = 256) on the ZCU106 device. Reports go
# to build/ooc_frontend/.

set repo   [file normalize [file join [file dirname [info script]] ..]]
set out    [file join $repo build ooc_frontend]
set period [expr {[llength $argv] ? [lindex $argv 0] : 4.0}]
file mkdir $out

set src {}
foreach f {frontend_pkg sensor_emulator radiometric_corr corner_turn sensor_frontend} {
    lappend src [file join $repo rtl frontend $f.sv]
}
foreach f {sdp_ram sample_ram sync_fifo axis_skid_buffer} {
    lappend src [file join $repo rtl common $f.sv]
}
read_verilog -sv $src

synth_design -top sensor_frontend -part xczu7ev-ffvc1156-2-e -mode out_of_context \
    -generic NX_MAX=512 -generic NZ_MAX=256
create_clock -name clk_sys    -period $period [get_ports clk_sys]
create_clock -name clk_sensor -period 10.0    [get_ports clk_sensor]
# The two clocks only meet in the XPM FIFOs, which carry their own CDC
# constraints. cfg is static while the sensor runs (written between acquisitions).
set_false_path -from [get_ports -filter {DIRECTION == IN} cfg*]

opt_design
place_design
phys_opt_design
route_design

report_timing_summary -max_paths 20 -file [file join $out timing.rpt]
report_utilization -file [file join $out utilization.rpt]
report_utilization -hierarchical -file [file join $out utilization_hier.rpt]
report_cdc -details -file [file join $out cdc.rpt]
puts "WNS [get_property SLACK [get_timing_paths -max_paths 1 -setup]] ns, clk_sys at $period ns"
