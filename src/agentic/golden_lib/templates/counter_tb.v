// Golden Reference: Counter Testbench
// Pre-verified by AgentIC Golden Library

`timescale 1ns / 1ps

module counter_tb;

    parameter WIDTH = 8;
    
    reg                clk;
    reg                rst_n;
    reg                en;
    reg                load;
    reg                up_down;
    reg  [WIDTH-1:0]   load_val;
    wire [WIDTH-1:0]   count;
    wire               overflow;
    wire               underflow;
    wire               zero;

    counter #(.WIDTH(WIDTH)) dut (
        .clk(clk), .rst_n(rst_n), .en(en), .load(load),
        .up_down(up_down), .load_val(load_val),
        .count(count), .overflow(overflow), .underflow(underflow), .zero(zero)
    );

    // Clock generation
    initial clk = 0;
    always #5 clk = ~clk;

    integer errors = 0;
    
    task check(input [WIDTH-1:0] expected, input [7:0] test_id);
        if (count !== expected) begin
            $display("FAIL test %0d: expected=%0d got=%0d", test_id, expected, count);
            errors = errors + 1;
        end
    endtask

    initial begin
        // Reset
        rst_n = 0; en = 0; load = 0; up_down = 1; load_val = 0;
        #20; rst_n = 1;
        check(0, 1);  // After reset = 0
        
        // Test 1: Count up
        en = 1; up_down = 1;
        repeat(5) @(posedge clk);
        #1; check(5, 2);
        
        // Test 2: Load value
        load = 1; load_val = 100;
        @(posedge clk); #1; load = 0;
        check(100, 3);
        
        // Test 3: Count down
        up_down = 0;
        repeat(3) @(posedge clk);
        #1; check(97, 4);
        
        // Test 4: Count to zero
        load = 1; load_val = 2; @(posedge clk); #1; load = 0;
        repeat(2) @(posedge clk);
        #1;
        if (!zero) begin $display("FAIL test 5: zero flag not set"); errors = errors + 1; end
        
        // Test 5: Overflow (count up from max)
        load = 1; load_val = {WIDTH{1'b1}}; @(posedge clk); #1; load = 0;
        up_down = 1;
        @(posedge clk); #1;
        if (!overflow) begin $display("FAIL test 6: overflow not set"); errors = errors + 1; end
        
        // Test 6: Reset mid-operation
        en = 1; up_down = 1;
        repeat(3) @(posedge clk);
        rst_n = 0; 
        en = 0; // Disable enable during reset to prevent race condition on release
        #10; rst_n = 1; #1;
        check(0, 7);
        
        // Results
        if (errors == 0)
            $display("TEST PASSED");
        else
            $display("TEST FAILED: %0d errors", errors);
        $finish;
    end

endmodule
