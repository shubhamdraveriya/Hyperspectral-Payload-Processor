# LNK-10: 2-D eye scans of both SFP receivers with the In-System IBERT of an
# HPDP_IBERT=1 build, on live traffic (run a PRBS-31 job first, e.g.
# `python sw/host/run_m5.py --prbs 600 --xsa <ibert build>/hpdp.xsa`, which programs
# the board and keeps the link busy). This script does not program the device.
#
#   vivado -mode batch -source sw/host/ibert_scan.tcl -tclargs <build dir> <out dir>
#
# JTAG at 6 MHz: the debug hub runs on clk_fr (31.25 MHz), and a JTAG clock above
# 12.5 MHz drops the debug cores (JSB MARK 1/gth_sfp_link, same board).
# Scan settings as the JSB MARK 1 fiber scans: horizontal increment 2, vertical
# increment 4, dwell BER 1e-6, full UI (codes -128..+128 at this rate).
set build [file normalize [lindex $argv 0]]
set out   [file normalize [lindex $argv 1]]
file mkdir $out
set ltx [lindex [glob -nocomplain [file join $build vivado hpdp.runs impl_1 *.ltx]] 0]
if {$ltx eq ""} { error "no .ltx probes file in $build (not an HPDP_IBERT=1 build?)" }

open_hw_manager
if {[llength [get_hw_servers -quiet]] == 0} { connect_hw_server -allow_non_jtag }
set t [lindex [get_hw_targets] 0]
catch {close_hw_target $t}
set_property PARAM.FREQUENCY 6000000 $t
open_hw_target $t
puts "JTAG clock: [get_property PARAM.FREQUENCY $t] Hz"
set dev [lindex [get_hw_devices xczu7_0] 0]
current_hw_device $dev
set_property PROBES.FILE      $ltx $dev
set_property FULL_PROBES.FILE $ltx $dev
refresh_hw_device $dev
puts "IBERT RX: [get_hw_sio_rxs]"

# SFP0 TX -> fiber -> SFP1 RX (the HPDP receive direction), and the reverse
# direction, which only carries a signal with a duplex cable (open item O-1)
set stamp [clock format [clock seconds] -format %Y%m%d_%H%M]
foreach {tx rx name} {X0Y10 X0Y11 sfp0_to_sfp1  X0Y11 X0Y10 sfp1_to_sfp0} {
    set txo [get_hw_sio_txs -filter "NAME =~ *MGT_${tx}/TX"]
    set rxo [get_hw_sio_rxs -filter "NAME =~ *MGT_${rx}/RX"]
    if {$txo eq "" || $rxo eq ""} { puts "$name: TX or RX not found"; continue }
    set link [create_hw_sio_link -description "HPDP $name" $txo $rxo]
    refresh_hw_sio $link
    puts "$name: link status [get_property STATUS $link], RX [get_property RX_PATTERN $link]"
    set scan [create_hw_sio_scan -description "HPDP $name 2/4 1e-6" 2d_full_eye $link]
    set_property HORIZONTAL_INCREMENT 2 $scan
    set_property VERTICAL_INCREMENT 4 $scan
    set_property DWELL_BER 1e-6 $scan
    run_hw_sio_scan $scan
    wait_on_hw_sio_scan $scan
    set csv [file join $out "hpdp_eye_${name}_${stamp}.csv"]
    write_hw_sio_scan -force $csv $scan
    puts "$name: open area [get_property Open_Area $scan], saved $csv"
}
close_hw_target $t
