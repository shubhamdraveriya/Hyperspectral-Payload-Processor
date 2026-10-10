# Out-of-context implementation of the CCSDS 123 core, for timing and resources.
#
#   vivado -mode batch -source vivado/ooc_c123.tcl [-tclargs <clock period ns>]
#
# Production sizes (NX_MAX = 512, NZ_MAX = 256) on the ZCU106 device. Reports go
# to build/ooc_c123/.

set repo   [file normalize [file join [file dirname [info script]] ..]]
set out    [file join $repo build ooc_c123]
set period [expr {[llength $argv] ? [lindex $argv 0] : 4.0}]
file mkdir $out

# (lists built with lappend: the checkout path may contain spaces)
set src [list [file join $repo rtl ccsds123 c123_pkg.sv]]
foreach f [lsort [glob -directory [file join $repo rtl ccsds123] *.sv]] {
    if {![string match *_pkg.sv $f]} { lappend src $f }
}
foreach f {sdp_ram sample_ram sync_fifo} {
    lappend src [file join $repo rtl common $f.sv]
}
read_verilog -sv $src

synth_design -top c123_core -part xczu7ev-ffvc1156-2-e -mode out_of_context \
    -generic NX_MAX=512 -generic NZ_MAX=256
create_clock -name clk -period $period [get_ports clk]
# cfg is static while a scene is processed (written between scenes)
set_false_path -from [get_ports -filter {DIRECTION == IN} cfg*]

opt_design
place_design
phys_opt_design
route_design

report_timing_summary -max_paths 20 -file [file join $out timing.rpt]
report_utilization -file [file join $out utilization.rpt]
report_utilization -hierarchical -file [file join $out utilization_hier.rpt]
puts "WNS [get_property SLACK [get_timing_paths -max_paths 1 -setup]] ns at $period ns"
