// Golden Reference: FSM Testbench
`timescale 1ns / 1ps
module fsm_tb;
    reg clk, rst_n, valid;
    reg [3:0] cmd;
    wire [3:0] status;
    wire ready, error, done;

    fsm dut (.clk(clk), .rst_n(rst_n), .cmd(cmd), .valid(valid),
             .status(status), .ready(ready), .error(error), .done(done));

    initial clk = 0;
    always #5 clk = ~clk;
    integer errors = 0;

    initial begin
        rst_n = 0; valid = 0; cmd = 0;
        #20; rst_n = 1; #1;
        if (!ready) begin $display("FAIL: not ready after reset"); errors = errors + 1; end
        
        // Normal operation flow
        cmd = 4'h5; valid = 1; @(posedge clk); #1; valid = 0;
        // Wait for EXECUTE phase
        repeat(2) @(posedge clk); #1;
        // Wait for DONE
        while (!done) @(posedge clk);
        #1;
        if (!done) begin $display("FAIL: done not asserted"); errors = errors + 1; end
        @(posedge clk); #1;
        if (!ready) begin $display("FAIL: not ready after done"); errors = errors + 1; end
        
        // Error path
        cmd = 4'hF; valid = 1; @(posedge clk); #1; valid = 0;
        repeat(3) @(posedge clk); #1;
        if (!error) begin $display("FAIL: error not set for cmd=0xF"); errors = errors + 1; end
        // Recover from error
        valid = 1; @(posedge clk); #1; valid = 0;
        repeat(2) @(posedge clk); #1;
        
        // Reset mid-execution
        cmd = 4'h3; valid = 1; @(posedge clk); #1; valid = 0;
        repeat(3) @(posedge clk);
        rst_n = 0; #10; rst_n = 1; #1;
        if (!ready) begin $display("FAIL: not ready after mid-reset"); errors = errors + 1; end
        
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
