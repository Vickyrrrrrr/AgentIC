// Golden Reference: SPI Master Testbench
`timescale 1ns / 1ps
module spi_master_tb;
    parameter CLK_DIV = 2;
    reg clk, rst_n, start;
    reg [7:0] mosi_data;
    wire [7:0] miso_data;
    wire sclk, mosi, cs_n, busy, done;
    reg miso;

    spi_master #(.CLK_DIV(CLK_DIV)) dut (
        .clk(clk), .rst_n(rst_n), .start(start), .mosi_data(mosi_data),
        .miso_data(miso_data), .sclk(sclk), .mosi(mosi), .miso(miso),
        .cs_n(cs_n), .busy(busy), .done(done)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer errors = 0;

    // Simple SPI loopback: MISO mirrors MOSI with delay
    always @(posedge sclk) begin
        miso <= mosi;  // Echo back for testing
    end

    initial begin
        rst_n = 0; start = 0; mosi_data = 0; miso = 0;
        #50; rst_n = 1; #10;
        
        if (!cs_n) begin $display("FAIL: CS not high after reset"); errors = errors + 1; end
        if (busy) begin $display("FAIL: busy after reset"); errors = errors + 1; end
        
        // Transfer 0xA5
        mosi_data = 8'hA5; start = 1;
        @(posedge clk); #1; start = 0;
        while (!done) @(posedge clk);
        #1;
        $display("Sent: 0x%02h, Received: 0x%02h", 8'hA5, miso_data);
        
        // Transfer 0x3C
        #20;
        mosi_data = 8'h3C; start = 1;
        @(posedge clk); #1; start = 0;
        while (!done) @(posedge clk);
        #1;
        $display("Sent: 0x%02h, Received: 0x%02h", 8'h3C, miso_data);
        
        // Check CS toggling
        if (!cs_n) begin $display("FAIL: CS low when idle"); errors = errors + 1; end
        
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
