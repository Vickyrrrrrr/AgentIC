// Golden Reference: UART TX Testbench
`timescale 1ns / 1ps
module uart_tx_tb;
    parameter CLK_FREQ = 50000000;
    parameter BAUD_RATE = 5000000; // Fast baud for simulation
    localparam CLKS_PER_BIT = CLK_FREQ / BAUD_RATE;
    
    reg clk, rst_n, tx_start;
    reg [7:0] tx_data;
    wire tx, tx_busy, tx_done;

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) dut (
        .clk(clk), .rst_n(rst_n), .tx_start(tx_start),
        .tx_data(tx_data), .tx(tx), .tx_busy(tx_busy), .tx_done(tx_done)
    );

    initial clk = 0;
    always #10 clk = ~clk;

    integer errors = 0;
    reg [7:0] captured;
    integer i;

    task send_byte(input [7:0] data);
        tx_data = data; tx_start = 1;
        @(posedge clk); #1; tx_start = 0;
        // Wait for start bit
        while (tx !== 1'b0) @(posedge clk);
        // Sample each data bit at mid-bit
        repeat(CLKS_PER_BIT/2) @(posedge clk);
        for (i = 0; i < 8; i = i + 1) begin
            repeat(CLKS_PER_BIT) @(posedge clk);
            captured[i] = tx;
        end
        // Wait for done
        while (!tx_done) @(posedge clk);
    endtask

    initial begin
        rst_n = 0; tx_start = 0; tx_data = 0;
        #100; rst_n = 1; #20;
        
        // Idle state check
        if (tx !== 1'b1) begin $display("FAIL: TX not idle high"); errors = errors + 1; end
        if (tx_busy) begin $display("FAIL: busy after reset"); errors = errors + 1; end
        
        // Send 0x55 (alternating bits)
        send_byte(8'h55);
        if (captured !== 8'h55) begin $display("FAIL: sent 0x55 got 0x%02h", captured); errors = errors + 1; end
        
        // Send 0xAA
        send_byte(8'hAA);
        if (captured !== 8'hAA) begin $display("FAIL: sent 0xAA got 0x%02h", captured); errors = errors + 1; end
        
        // Send 0x00
        send_byte(8'h00);
        if (captured !== 8'h00) begin $display("FAIL: sent 0x00 got 0x%02h", captured); errors = errors + 1; end
        
        // Send 0xFF
        send_byte(8'hFF);
        if (captured !== 8'hFF) begin $display("FAIL: sent 0xFF got 0x%02h", captured); errors = errors + 1; end
        
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
