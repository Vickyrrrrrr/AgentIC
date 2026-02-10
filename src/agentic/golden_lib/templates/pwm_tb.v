// Golden Reference: PWM Testbench
`timescale 1ns / 1ps
module pwm_tb;
    parameter RESOLUTION = 8;
    reg clk, rst_n, en;
    reg [RESOLUTION-1:0] duty;
    wire pwm_out, period_done;

    pwm #(.RESOLUTION(RESOLUTION)) dut (
        .clk(clk), .rst_n(rst_n), .en(en), .duty(duty),
        .pwm_out(pwm_out), .period_done(period_done)
    );

    initial clk = 0;
    always #5 clk = ~clk;
    integer errors = 0;
    integer high_count, total_count;

    task measure_duty(input [RESOLUTION-1:0] d, output integer measured_high, output integer measured_total);
        integer h, t;
        begin
            h = 0; t = 0;
            duty = d; en = 1;
            // Run for one full PWM period (2^RESOLUTION clocks)
            repeat(256) begin
                @(posedge clk); #1;
                if (pwm_out) h = h + 1;
                t = t + 1;
            end
            measured_high = h;
            measured_total = t;
        end
    endtask

    initial begin
        rst_n = 0; en = 0; duty = 0;
        #20; rst_n = 1; #1;
        if (pwm_out) begin $display("FAIL: PWM high when disabled"); errors = errors + 1; end
        
        // 50% duty
        measure_duty(128, high_count, total_count);
        if (high_count < 120 || high_count > 136) begin
            $display("FAIL: 50%% duty expected ~128 high, got %0d", high_count);
            errors = errors + 1;
        end
        
        // 25% duty
        measure_duty(64, high_count, total_count);
        if (high_count < 56 || high_count > 72) begin
            $display("FAIL: 25%% duty expected ~64 high, got %0d", high_count);
            errors = errors + 1;
        end
        
        // 0% duty
        measure_duty(0, high_count, total_count);
        if (high_count != 0) begin
            $display("FAIL: 0%% duty got %0d high", high_count);
            errors = errors + 1;
        end
        
        // Disable
        en = 0; @(posedge clk); @(posedge clk); #1;
        if (pwm_out) begin $display("FAIL: PWM high when disabled"); errors = errors + 1; end
        
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d errors", errors);
        $finish;
    end
endmodule
