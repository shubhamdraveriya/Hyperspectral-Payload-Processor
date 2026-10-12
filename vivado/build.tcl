# Synthesis, implementation, bitstream and hardware export for the HPDP.
#
#   vivado -mode batch -source vivado/build.tcl
#
# Creates the project first if needed (create_project.tcl). If a build was
# interrupted, Vivado may refuse to relaunch its runs ("needs to be reset"):
# delete build/vivado and run this again. Outputs:
#   build/hpdp.xsa          hardware platform for Vitis (includes the bitstream)
#   build/reports/*.rpt     timing, utilization, CDC and methodology reports

set repo [file normalize [file join [file dirname [info script]] ..]]
if {[info exists ::env(HPDP_BUILD_DIR)]} {
    set build_dir [file normalize $::env(HPDP_BUILD_DIR)]
} else {
    set build_dir [file join $repo build]
}
set xpr [file join $build_dir vivado hpdp.xpr]
set jobs 8

if {[file exists $xpr]} {
    open_project $xpr
} else {
    source [file join $repo vivado create_project.tcl]
}

reset_run synth_1
launch_runs synth_1 -jobs $jobs
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }

launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }

open_run impl_1
set rpt [file join $build_dir reports]
file mkdir $rpt
report_timing_summary -max_paths 10 -file [file join $rpt timing_summary.rpt]
report_utilization -file [file join $rpt utilization.rpt]
report_cdc -file [file join $rpt cdc.rpt]
report_methodology -file [file join $rpt methodology.rpt]

write_hw_platform -fixed -include_bit -force [file join $build_dir hpdp.xsa]

set wns [get_property STATS.WNS [get_runs impl_1]]
set whs [get_property STATS.WHS [get_runs impl_1]]
puts "Build done: WNS $wns ns, WHS $whs ns, XSA [file join $build_dir hpdp.xsa]"
if {$wns < 0 || $whs < 0} { error "timing not met" }
