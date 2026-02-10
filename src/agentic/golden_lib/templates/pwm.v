// Golden Reference: PWM Generator
// Pre-verified by AgentIC Golden Library

module pwm #(
    parameter RESOLUTION = 8
)(
    input  wire                   clk,
    input  wire                   rst_n,
    input  wire                   en,
    input  wire [RESOLUTION-1:0]  duty,
    output reg                    pwm_out,
    output wire                   period_done
);

    reg [RESOLUTION-1:0] counter;
    reg period_done_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            counter      <= 0;
            pwm_out      <= 1'b0;
            period_done_r <= 1'b0;
        end else if (en) begin
            counter <= counter + 1;
            pwm_out <= (counter < duty) ? 1'b1 : 1'b0;
            period_done_r <= (counter == {RESOLUTION{1'b1}});
        end else begin
            counter       <= 0;
            pwm_out       <= 1'b0;
            period_done_r <= 1'b0;
        end
    end

    assign period_done = period_done_r;

endmodule
