`timescale 1ns / 1ps

module simple_counter_tb;

    // Inputs
    reg clk;
    reg rst_n;
    reg enable;

    // Outputs
    wire [7:0] count;

    // Instantiate the Unit Under Test (UUT)
    simple_counter uut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .count(count)
    );

    // Clock generation
    initial begin
        clk = 0;
        forever #5 clk = ~clk; // 100MHz clock
    end

    // Test Sequence
    initial begin
        // Initialize Inputs
        rst_n = 0;
        enable = 0;

        // Monitoring
        $monitor("Time=%0t | rst_n=%b | enable=%b | count=%d", $time, rst_n, enable, count);

        // Reset
        #20;
        rst_n = 1;
        #20;

        // Enable Counter
        enable = 1;
        #200;

        // Disable Counter
        enable = 0;
        #50;

        // Reset again
        rst_n = 0;
        #20;
        
        $display("TEST PASSED");
        $finish;
    end
    
    // Waveform dump for GTKWave
    initial begin
        $dumpfile("simple_counter.vcd");
        $dumpvars(0, simple_counter_tb);
    end

endmodule
