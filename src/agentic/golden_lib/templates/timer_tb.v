// Golden Reference: Timer Testbench
`timescale 1ns / 1ps
module timer_tb;
    parameter WIDTH = 8; // Smaller for testability
    reg clk, rst_n, en, clear;
    reg [WIDTH-1:0] compare_val;
    reg [7:0] prescaler;
    wire [WIDTH-1:0] counter;
    wire match, overflow;

    timer #(.WIDTH(WIDTH)) dut (
        .clk(clk), .rst_n(rst_n), .en(en), .clear(clear),
        .compare_val(compare_val), .prescaler(prescaler),
        .counter(counter), .match(match), .overflow(overflow)
    );

    initial clk = 0;
    always #5 clk = ~clk;
    integer errors = 0;

    initial begin
        rst_n = 0; en = 0; clear = 0; compare_val = 10; prescaler = 0;
        #20; rst_n = 1; #1;
        if (counter !== 0) begin $display("FAIL: counter not 0 after reset"); errors = errors + 1; end
        
        // Count with prescaler=0 (tick every clock)
        en = 1;
        repeat(10) @(posedge clk);
        #1;
        // Check match fires at compare_val
        @(posedge clk); #1;
        if (!match) begin $display("FAIL: match not set at compare_val=%0d, counter=%0d", compare_val, counter); errors = errors + 1; end
        
        // Clear
        clear = 1; @(posedge clk); #1; clear = 0;
        if (counter !== 0) begin $display("FAIL: counter not 0 after clear"); errors = errors + 1; end
        
        // Prescaler test (tick every 4 clocks)
        prescaler = 3; compare_val = 5;
        repeat(24) @(posedge clk); // Should count to ~5 with prescaler=3
        #1;
        $display("Prescaler test: counter=%0d (expected ~5)", counter);
        
        // Disable
        en = 0;
        @(posedge clk); @(posedge clk); #1;
        
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
