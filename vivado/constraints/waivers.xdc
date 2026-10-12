# Waivers (VER-09): what remains in report_cdc / report_methodology, with reasons.
# CDC-11: inside the GTH wizard. The RX side runs on the TX user clock, so
# gtwiz_userclk_rx_active_in is tied to gtwiz_userclk_tx_active_out, and the
# one "active" flop feeds both reset-controller synchronizers (rx_active,
# tx_active) on the free-running clock. Both are levels that only rise after
# the user clock runs and stay up; the wizard's own false path covers them.
create_waiver -type CDC -id CDC-11 -from [get_pins -hier -filter {NAME =~ u_gt/*gtwiz_userclk_tx_active_sync_reg/C}] -to [get_pins -hier -filter {NAME =~ u_gt/*gtwiz_reset_inst/bit_synchronizer_gtwiz_reset_userclk_*_active_inst/i_in_meta_reg/D}] -description {GTH wizard: one TX-user-clock active flag feeds the rx_active and tx_active reset synchronizers (RX runs on the TX user clock); monotonic levels}
# LUTAR-1: LUTs driving asynchronous resets inside the GTH wizard (reset
# controller, CPLL calibration), and the user-clock helper reset built as
# ~&txpmaresetdone, as in AMD's multi-channel example design. Their inputs are
# transceiver status levels that change once per reset sequence.
create_waiver -type METHODOLOGY -id LUTAR-1 -objects [get_cells -hier -filter {NAME =~ u_gt* && REF_NAME =~ LUT*}] -description {GTH wizard reset structure and the user-clock helper reset ~&txpmaresetdone (AMD example design); inputs are once-per-reset transceiver status levels}
