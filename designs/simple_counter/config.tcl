set ::env(DESIGN_NAME) "simple_counter"
set ::env(VERILOG_FILES) "$::env(DESIGN_DIR)/src/simple_counter.v"

# Die size 200um x 200um
set ::env(DIE_AREA) "0 0 200 200"
set ::env(FP_SIZING) absolute

# Core utilization
set ::env(FP_CORE_UTIL) 20
set ::env(PL_TARGET_DENSITY) 0.3

# Power Grid Settings
set ::env(FP_PDN_VPITCH) 50
set ::env(FP_PDN_HPITCH) 50
set ::env(FP_PDN_VOFFSET) 5
set ::env(FP_PDN_HOFFSET) 5

# Power Pins / Voltage Sources
set ::env(VDD_NETS) [list {vccd1}]
set ::env(GND_NETS) [list {vssd1}]
set ::env(SYNTH_USE_PG_PINS_DEFINES) "USE_POWER_PINS"

# Technology Setup
set ::env(PDK) "sky130A"
set ::env(STD_CELL_LIBRARY) "sky130_fd_sc_hd"
set ::env(CLOCK_PORT) "clk"
set ::env(CLOCK_PERIOD) "10.0"
