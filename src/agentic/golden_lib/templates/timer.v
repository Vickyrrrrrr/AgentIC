// Golden Reference: General-Purpose Timer
// Pre-verified by AgentIC Golden Library

module timer #(
    parameter WIDTH = 32
)(
    input  wire              clk,
    input  wire              rst_n,
    input  wire              en,
    input  wire              clear,
    input  wire [WIDTH-1:0]  compare_val,
    input  wire [7:0]        prescaler,
    output reg  [WIDTH-1:0]  counter,
    output reg               match,
    output reg               overflow
);

    reg [7:0] prescale_cnt;
    wire      tick;

    assign tick = (prescale_cnt == prescaler);

    // Prescaler
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prescale_cnt <= 8'd0;
        end else if (en) begin
            if (tick || clear)
                prescale_cnt <= 8'd0;
            else
                prescale_cnt <= prescale_cnt + 1;
        end else begin
            prescale_cnt <= 8'd0;
        end
    end

    // Main counter
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            counter  <= {WIDTH{1'b0}};
            match    <= 1'b0;
            overflow <= 1'b0;
        end else if (clear) begin
            counter  <= {WIDTH{1'b0}};
            match    <= 1'b0;
            overflow <= 1'b0;
        end else if (en && tick) begin
            if (counter == compare_val) begin
                match <= 1'b1;
            end else begin
                match <= 1'b0;
            end
            {overflow, counter} <= counter + 1;
        end else begin
            match    <= 1'b0;
            overflow <= 1'b0;
        end
    end

endmodule
