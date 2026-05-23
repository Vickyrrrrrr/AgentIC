(* blackbox *)
module sky130_sram_2kbyte_1rw1r_32x512_8 (
    input  wire        clk0,
    input  wire        csb0,
    input  wire        web0,
    input  wire [3:0]  wmask0,
    input  wire [8:0]  addr0,
    input  wire [31:0] din0,
    output wire [31:0] dout0,
    
    input  wire        clk1,
    input  wire        csb1,
    input  wire [8:0]  addr1,
    output wire [31:0] dout1
);
endmodule

module sram_macro_wrapper #(
    parameter DATA_WIDTH = 32,
    parameter ADDR_WIDTH = 13,
    parameter DEPTH = 8192
) (
    input wire clk,
    input wire rst_n,
    input wire [12:0] mem_addr,
    input wire mem_en,
    output wire [31:0] mem_rdata,
    input wire [31:0] mem_wdata,
    input wire mem_we,
    
    input wire wr_en,
    input wire rd_en,
    input wire [ADDR_WIDTH-1:0] addr,
    input wire [DATA_WIDTH-1:0] wr_data,
    output wire [DATA_WIDTH-1:0] rd_data,
    
    input wire ce,
    input wire we,
    input wire [31:0] wdata,
    output wire [31:0] rdata
);

    // Instantiate 2KB SRAM (512 x 32)
    // We tie off port 1 (Read Only) and use port 0 (Read/Write)
    sky130_sram_2kbyte_1rw1r_32x512_8 u_sram_macro (
        .clk0   (clk),
        .csb0   (~mem_en),     // Active low chip select
        .web0   (~mem_we),     // Active low write enable
        .wmask0 (4'b1111),     // 32-bit write mask
        .addr0  (mem_addr[8:0]), // Map lower 9 bits to 512-word depth
        .din0   (mem_wdata),
        .dout0  (mem_rdata),
        
        .clk1   (clk),
        .csb1   (1'b1),        // Disable port 1
        .addr1  (9'd0),
        .dout1  ()             // Unconnected
    );

    // Dummy ties for remaining unused ports to prevent Yosys warnings
    assign rd_data = 32'd0;
    assign rdata = 32'd0;

endmodule
