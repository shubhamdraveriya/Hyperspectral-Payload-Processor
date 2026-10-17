# Vivado project archive of the HPDP (File > Project > Archive), written to
# hpdp_vivado.xpr.zip at the repository root; open it with File > Project >
# Open Archived Project. Needs Vivado 2025.2.
#
#   vivado -mode batch -source vivado/archive_project.tcl
#
# Archives a project created afresh from the scripts (create_project.tcl, so
# HPDP_NX_MAX and HPDP_IBERT apply as for build.tcl), in build/archive or
# HPDP_BUILD_DIR/archive, so its IP matches this Vivado: a project from another
# release opens with every IP locked. Run results are left out, which keeps the zip
# small: the bitstreams and XSAs of the final builds are in build/m7h and
# build/m7i_1024b. HPDP_ARCHIVE_RUNS=1 archives the project of a build.tcl run
# instead, run results included; the zip may then pass GitHub's 100 MB file limit.

set repo [file normalize [file join [file dirname [info script]] ..]]
if {[info exists ::env(HPDP_BUILD_DIR)]} {
    set build_dir [file normalize $::env(HPDP_BUILD_DIR)]
} else {
    set build_dir [file join $repo build]
}
set zip [file join $repo hpdp_vivado.xpr.zip]
# archive_project copies the project here first; the default, .Xil under the current
# directory, can pass Windows' 260-character path limit
set tmp [file join $build_dir artmp]
file delete -force $tmp

if {[info exists ::env(HPDP_ARCHIVE_RUNS)] && $::env(HPDP_ARCHIVE_RUNS) eq "1"} {
    set xpr [file join $build_dir vivado hpdp.xpr]
    if {![file exists $xpr]} { error "HPDP_ARCHIVE_RUNS=1 needs a project built by build.tcl: $xpr" }
    open_project $xpr
    if {[llength [get_ips -all -quiet -filter {IS_LOCKED}]]} {
        error "$xpr has locked IP (made by another Vivado release?): delete build/vivado and run build.tcl"
    }
    archive_project -force -include_config_settings -temp_dir $tmp $zip
} else {
    set saved [expr {[info exists ::env(HPDP_BUILD_DIR)] ? $::env(HPDP_BUILD_DIR) : ""}]
    set ::env(HPDP_BUILD_DIR) [file join $build_dir archive]
    source [file join $repo vivado create_project.tcl]
    if {$saved eq ""} { unset ::env(HPDP_BUILD_DIR) } else { set ::env(HPDP_BUILD_DIR) $saved }
    archive_project -force -include_config_settings -exclude_run_results -temp_dir $tmp $zip
}
file delete -force $tmp
puts "Archived [current_project] ([version -short]) to $zip ([format %.1f [expr {[file size $zip] / 1048576.0}]] MB)"
