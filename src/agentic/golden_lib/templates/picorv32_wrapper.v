module picorv32_wrapper (
    input wire clk,               // Clock signal
    input wire rst_n,             // Active-low reset signal
    input wire [31:0] instr_in,   // Not used by PicoRV32 (fetches its own)
    input wire [31:0] data_in,    // Data from memory/peripherals
    output wire [31:0] data_out,  // Address/Data requested by CPU
    output reg interrupt_flag     // Interrupt flag output
);

    wire        mem_valid;
    wire        mem_instr;
    reg         mem_ready;
    wire [31:0] mem_addr;
    wire [31:0] mem_wdata;
    wire [ 3:0] mem_wstrb;
    wire [31:0] mem_rdata;

    // We feed external data_in back as mem_rdata
    assign mem_rdata = data_in;

    // We output the requested address/data combo to the top-level
    // so the top-level can route it to SRAM or Peripherals
    assign data_out = mem_wstrb ? mem_wdata : mem_addr;

    // Simple 1-cycle memory readiness
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            mem_ready <= 0;
        else
            mem_ready <= mem_valid && !mem_ready;
    end

    // Instantiate the PicoRV32 Core
    picorv32 #(
        .ENABLE_COUNTERS(0),
        .ENABLE_COUNTERS64(0),
        .ENABLE_REGS_16_31(1),
        .ENABLE_REGS_DUALPORT(1),
        .LATCHED_MEM_RDATA(0),
        .TWO_STAGE_SHIFT(0),
        .BARREL_SHIFTER(0),
        .TWO_CYCLE_COMPARE(0),
        .TWO_CYCLE_ALU(0),
        .COMPRESSED_ISA(0),
        .CATCH_MISALIGN(0),
        .CATCH_ILLINSN(0)
    ) cpu (
        .clk       (clk),
        .resetn    (rst_n),
        .trap      (interrupt_flag),
        .mem_valid (mem_valid),
        .mem_instr (mem_instr),
        .mem_ready (mem_ready),
        .mem_addr  (mem_addr),
        .mem_wdata (mem_wdata),
        .mem_wstrb (mem_wstrb),
        .mem_rdata (mem_rdata)
    );

endmodule
