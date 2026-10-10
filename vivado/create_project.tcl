# Create the HPDP Vivado project from the repository sources.
#
#   vivado -mode batch -source vivado/create_project.tcl
#
# The project goes to <repo>/build/vivado/hpdp.xpr. Set HPDP_BUILD_DIR to use a
# shorter path if Windows' 260-character path limit becomes a problem.

set repo [file normalize [file join [file dirname [info script]] ..]]
if {[info exists ::env(HPDP_BUILD_DIR)]} {
    set build_dir [file normalize $::env(HPDP_BUILD_DIR)]
} else {
    set build_dir [file join $repo build]
}
set proj_dir [file join $build_dir vivado]

create_project hpdp $proj_dir -part xczu7ev-ffvc1156-2-e -force
set_property board_part [lindex [get_board_parts -latest_file_version {*:zcu106:part0:*}] 0] [current_project]
set_property target_language Verilog [current_project]

# RTL and constraints (blocks are added here as they are written)
set rtl [glob -nocomplain [file join $repo rtl * *.sv]]
if {[llength $rtl]} { add_files -norecurse $rtl }
set xdc [glob -nocomplain [file join $repo vivado constraints *.xdc]]
if {[llength $xdc]} { add_files -fileset constrs_1 -norecurse $xdc }

# Block design and its wrapper as the top level
source [file join $repo vivado bd base.tcl]
make_wrapper -files [get_files base.bd] -top -import
set_property top base_wrapper [current_fileset]
update_compile_order -fileset sources_1

puts "Created [file join $proj_dir hpdp.xpr]"
