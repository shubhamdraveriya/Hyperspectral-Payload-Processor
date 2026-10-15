# Vivado project archive of the HPDP (File > Project > Archive), written to
# hpdp_vivado.xpr.zip at the repository root; open it with File > Project >
# Open Archived Project. Needs Vivado 2025.2.
#
#   vivado -mode batch -source vivado/archive_project.tcl
#
# Creates the project first if needed (create_project.tcl, so HPDP_NX_MAX and
# HPDP_IBERT apply as for build.tcl), in build/vivado or HPDP_BUILD_DIR. Run results
# are left out, which keeps the zip small: the bitstreams and XSAs of the final builds
# are in build/m7h and build/m7i_1024b. HPDP_ARCHIVE_RUNS=1 includes them after a
# build.tcl run, but the zip may then pass GitHub's 100 MB file limit.

set repo [file normalize [file join [file dirname [info script]] ..]]
if {[info exists ::env(HPDP_BUILD_DIR)]} {
    set build_dir [file normalize $::env(HPDP_BUILD_DIR)]
} else {
    set build_dir [file join $repo build]
}
set xpr [file join $build_dir vivado hpdp.xpr]

if {[file exists $xpr]} {
    open_project $xpr
} else {
    source [file join $repo vivado create_project.tcl]
}

set zip [file join $repo hpdp_vivado.xpr.zip]
if {[info exists ::env(HPDP_ARCHIVE_RUNS)] && $::env(HPDP_ARCHIVE_RUNS) eq "1"} {
    archive_project -force -include_config_settings $zip
} else {
    archive_project -force -include_config_settings -exclude_run_results $zip
}
puts "Archived [current_project] to $zip ([format %.1f [expr {[file size $zip] / 1048576.0}]] MB)"
