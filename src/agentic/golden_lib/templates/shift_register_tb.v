// Golden Reference: Shift Register Testbench
`timescale 1ns / 1ps
module shift_register_tb;
    parameter WIDTH = 8;
    reg clk, rst_n, en, load, dir, serial_in;
    reg [WIDTH-1:0] parallel_in;
    wire serial_out;
    wire [WIDTH-1:0] parallel_out;

    shift_register #(.WIDTH(WIDTH)) dut (
        .clk(clk), .rst_n(rst_n), .en(en), .load(load), .dir(dir),
        .serial_in(serial_in), .parallel_in(parallel_in),
        .serial_out(serial_out), .parallel_out(parallel_out)
    );

    initial clk = 0;
    always #5 clk = ~clk;
    integer errors = 0;

    initial begin
        rst_n = 0; en = 0; load = 0; dir = 0; serial_in = 0; parallel_in = 0;
        #20; rst_n = 1; #1;
        if (parallel_out !== 0) begin $display("FAIL: not 0 after reset"); errors = errors + 1; end

        // Parallel load
        parallel_in = 8'hA5; load = 1;
        @(posedge clk); #1; load = 0;
        if (parallel_out !== 8'hA5) begin $display("FAIL: load got %02h", parallel_out); errors = errors + 1; end

        // Left shift
        dir = 0; en = 1; serial_in = 1;
        @(posedge clk); #1;
        if (parallel_out !== 8'h4B) begin $display("FAIL: left shift got %02h expected 4B", parallel_out); errors = errors + 1; end

        // Right shift
        parallel_in = 8'hA5; load = 1; @(posedge clk); #1; load = 0;
        dir = 1; serial_in = 0;
        @(posedge clk); #1;
        if (parallel_out !== 8'h52) begin $display("FAIL: right shift got %02h expected 52", parallel_out); errors = errors + 1; end

        // Serial in/out
        parallel_in = 8'h01; load = 1; @(posedge clk); #1; load = 0;
        dir = 0; serial_in = 0; en = 1;
        repeat(8) @(posedge clk);
        #1;
        if (parallel_out !== 8'h00) begin $display("FAIL: serial shift out got %02h", parallel_out); errors = errors + 1; end

        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
