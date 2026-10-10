# RTL regression in the Vivado simulator (xsim). No project needed.
#
#   vivado -mode batch -source tb/run_sim.tcl
#   vivado -mode batch -source tb/run_sim.tcl -tclargs skid_buffer
#
# Compiles every rtl/**/*.sv and tb/**/*.sv, then runs each test below.
# Builds in build/sim/. Each testbench prints PASS or FAIL; the script raises
# an error (non-zero exit in batch mode) if any test fails.

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

# Paths relative to build/sim keep spaces in the checkout path away from cmd.exe
proc sources {dir} {
    set out {}
    foreach f [lsort [glob -nocomplain -directory [file join $::repo $dir] -types f */*.sv *.sv]] {
        lappend out "../../$dir/[string range $f [string length [file join $::repo $dir]/] end]"
    }
    return $out
}

file copy -force [file join $::env(XILINX_VIVADO) data verilog src glbl.v] .
run [tool xvlog] --sv {*}[sources rtl] {*}[sources tb]
run [tool xvlog] glbl.v

set selected $argv
set results {}
set failed 0
foreach {name top plusargs} $tests {
    if {[llength $selected] && [lsearch -exact $selected $name] < 0} continue

    run [tool xelab] -L unisims_ver -L xpm -s $top work.$top work.glbl

    # Options go through a file: cmd.exe splits command-line arguments at '='
    set opts [list $top -R -log ${name}.log]
    foreach p $plusargs { lappend opts -testplusarg $p }
    set fh [open ${name}.opts w]
    puts $fh [join $opts "\n"]
    close $fh

    set rc [catch {run [tool xsim] -f ${name}.opts} out]
    if {!$rc && [regexp -line {^PASS:} $out] && ![regexp {FAIL|Error:|ERROR:|Fatal:|FATAL} $out]} {
        lappend results [format "  %-14s PASS" $name]
    } else {
        lappend results [format "  %-14s FAIL   (see build/sim/%s.log)" $name $name]
        incr failed
    }
}

puts "\nRegression summary:"
puts [join $results "\n"]
if {$failed} { error "$failed test(s) failed" }
puts "All tests passed"
