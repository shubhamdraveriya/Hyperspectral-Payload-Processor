# RTL regression in the Vivado simulator (xsim). No project needed.
#
#   vivado -mode batch -source tb/run_sim.tcl
#   vivado -mode batch -source tb/run_sim.tcl -tclargs skid_buffer c123_basic
#
# Compiles every rtl/**/*.sv and tb/**/*.sv (packages first), generates the
# CCSDS 123 vectors from the Python model (one c123_<name> test each), then
# runs the tests. Builds in build/sim/. Each testbench prints PASS or FAIL; the
# script raises an error (non-zero exit in batch mode) if any test fails.

#            name          testbench top          plusargs
set tests {
    skid_buffer  tb_axis_skid_buffer  {}
}

set repo  [file normalize [file join [file dirname [info script]] ..]]
set build [file join $repo build sim]
file mkdir $build
cd $build

# The xsim tools are .bat files on Windows
proc tool {name} {
    if {$::tcl_platform(platform) eq "windows"} { return "$name.bat" }
    return $name
}

proc run {args} {
    puts ">> [join $args]"
    set rc [catch {exec {*}$args 2>@1} out]
    puts $out
    if {$rc} { error "[lindex $args 0] failed" }
    return $out
}

# Paths relative to build/sim keep spaces in the checkout path away from cmd.exe.
# Packages (*_pkg.sv) come first so the modules that import them compile.
proc sources {dir} {
    set root [file join $::repo $dir]
    set files [lsort [glob -nocomplain -directory $root -types f */*.sv *.sv]]
    set pkgs [lsearch -all -inline -glob $files *_pkg.sv]
    set rest [lsearch -all -inline -not -glob $files *_pkg.sv]
    set out {}
    foreach f [concat $pkgs $rest] {
        lappend out "../../$dir/[string range $f [string length $root/] end]"
    }
    return $out
}

# CCSDS 123 vectors from the reference model
set selected $argv
set want_c123 [expr {![llength $selected] || [llength [lsearch -all -glob $selected c123_*]]}]
if {$want_c123} {
    run python ../../tb/ccsds123/gen_vectors.py c123
    set fh [open c123/tests.txt]
    foreach t [split [string trim [read $fh]] "\n"] {
        lappend tests c123_$t tb_c123_core [list DIR=c123/$t]
    }
    close $fh
}

file copy -force [file join $::env(XILINX_VIVADO) data verilog src glbl.v] .
run [tool xvlog] --sv {*}[sources rtl] {*}[sources tb]
run [tool xvlog] glbl.v

set results {}
set failed 0
set built {}
foreach {name top plusargs} $tests {
    if {[llength $selected] && [lsearch -exact $selected $name] < 0} continue

    if {[lsearch -exact $built $top] < 0} {
        run [tool xelab] -L unisims_ver -L xpm -s $top work.$top work.glbl
        lappend built $top
    }

    # Options go through a file: cmd.exe splits command-line arguments at '='
    set opts [list $top -R -log ${name}.log]
    foreach p $plusargs { lappend opts -testplusarg $p }
    set fh [open ${name}.opts w]
    puts $fh [join $opts "\n"]
    close $fh

    set rc [catch {run [tool xsim] -f ${name}.opts} out]
    if {!$rc && [regexp -line {^PASS:} $out] && ![regexp {FAIL|Error:|ERROR:|Fatal:|FATAL} $out]} {
        lappend results [format "  %-18s PASS" $name]
    } else {
        lappend results [format "  %-18s FAIL   (see build/sim/%s.log)" $name $name]
        incr failed
    }
}

puts "\nRegression summary:"
puts [join $results "\n"]
if {$failed} { error "$failed test(s) failed" }
puts "All tests passed"
