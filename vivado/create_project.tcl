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

# RTL and constraints
set rtl [concat [glob -nocomplain [file join $repo rtl * *.sv]] [glob -nocomplain [file join $repo rtl * *.v]]]
if {[llength $rtl]} { add_files -norecurse $rtl }
set xdc [glob -nocomplain [file join $repo vivado constraints *.xdc]]
if {[llength $xdc]} { add_files -fileset constrs_1 -norecurse $xdc }
# the waivers name cells inside the GTH IP, which is a black box in top-level synthesis
set_property USED_IN_SYNTHESIS false [get_files waivers.xdc]

# GTH transceivers of the SFP cages (instantiated in hpdp_top)
source [file join $repo vivado ip gt_link.tcl]

# Optional In-System IBERT on both channels for eye scans (LNK-10): HPDP_IBERT=1
set ibert [expr {[info exists ::env(HPDP_IBERT)] && $::env(HPDP_IBERT) eq "1"}]
if {$ibert} {
    source [file join $repo vivado ip isib_link.tcl]
    add_files -fileset constrs_1 -norecurse [file join $repo vivado ibert ibert_debug.xdc]
    set_property USED_IN_SYNTHESIS false [get_files ibert_debug.xdc]
    puts "In-System IBERT included (HPDP_IBERT=1)"
}

update_compile_order -fileset sources_1

# SYS.BUILD_ID: the commit the project was created from (0 outside git)
if {[catch {exec git -C $repo rev-parse --short=8 HEAD} hash]} { set hash 0 }
set build_id [format 0x%08X [expr {"0x$hash"}]]
puts "BUILD_ID $build_id"

# Block design and its wrapper, under hpdp_top (transceivers + block design)
source [file join $repo vivado bd system.tcl]
make_wrapper -files [get_files system.bd] -top -import
set_property top hpdp_top [current_fileset]
if {$ibert} { set_property generic {IBERT=1'b1} [current_fileset] }
update_compile_order -fileset sources_1

puts "Created [file join $proj_dir hpdp.xpr]"
