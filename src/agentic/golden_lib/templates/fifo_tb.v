// Golden Reference: FIFO Testbench
// Pre-verified by AgentIC Golden Library

`timescale 1ns / 1ps

module fifo_tb;

    parameter DATA_WIDTH = 8;
    parameter DEPTH = 16;

    reg                    clk, rst_n;
    reg                    wr_en, rd_en;
    reg  [DATA_WIDTH-1:0]  wr_data;
    wire [DATA_WIDTH-1:0]  rd_data;
    wire                   full, empty, almost_full, almost_empty;
    wire [$clog2(DEPTH):0] count;

    fifo #(.DATA_WIDTH(DATA_WIDTH), .DEPTH(DEPTH)) dut (
        .clk(clk), .rst_n(rst_n), .wr_en(wr_en), .rd_en(rd_en),
        .wr_data(wr_data), .rd_data(rd_data),
        .full(full), .empty(empty),
        .almost_full(almost_full), .almost_empty(almost_empty),
        .count(count)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer errors = 0;
    integer i;

    initial begin
        rst_n = 0; wr_en = 0; rd_en = 0; wr_data = 0;
        #20; rst_n = 1; #1;
        
        // Check empty after reset
        if (!empty) begin $display("FAIL: not empty after reset"); errors = errors + 1; end
        
        // Fill FIFO
        for (i = 0; i < DEPTH; i = i + 1) begin
            wr_en = 1; wr_data = i[DATA_WIDTH-1:0];
            @(posedge clk); #1;
        end
        wr_en = 0;
        
        if (!full) begin $display("FAIL: not full after %0d writes", DEPTH); errors = errors + 1; end
        
        // Write when full (should be ignored)
        wr_en = 1; wr_data = 8'hFF;
        @(posedge clk); #1;
        wr_en = 0;
        if (count != DEPTH) begin $display("FAIL: count changed on full write"); errors = errors + 1; end
        
        // Read all and verify FIFO order
        for (i = 0; i < DEPTH; i = i + 1) begin
            rd_en = 1;
            @(posedge clk); #1;
            if (rd_data !== i[DATA_WIDTH-1:0]) begin
                $display("FAIL: read %0d expected %0d", rd_data, i);
                errors = errors + 1;
            end
        end
        rd_en = 0;
        
        if (!empty) begin $display("FAIL: not empty after full drain"); errors = errors + 1; end
        
        // Simultaneous read/write
        wr_en = 1; wr_data = 8'hAA;
        @(posedge clk); #1; wr_en = 0;
        wr_en = 1; rd_en = 1; wr_data = 8'hBB;
        @(posedge clk); #1;
        wr_en = 0; rd_en = 0;
        
        // Reset mid-operation
        wr_en = 1; wr_data = 8'h55;
        repeat(3) @(posedge clk);
        rst_n = 0; #10; rst_n = 1; #1;
        if (!empty) begin $display("FAIL: not empty after reset"); errors = errors + 1; end
        
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end

endmodule
