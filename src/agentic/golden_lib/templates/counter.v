// Golden Reference: Parameterizable N-bit Counter
// Pre-verified by AgentIC Golden Library
// Supports: up/down counting, enable, load, overflow detection

module counter #(
    parameter WIDTH = 8
)(
    input  wire                clk,
    input  wire                rst_n,
    input  wire                en,
    input  wire                load,
    input  wire                up_down,      // 1 = up, 0 = down
    input  wire [WIDTH-1:0]    load_val,
    output reg  [WIDTH-1:0]    count,
    output wire                overflow,
    output wire                underflow,
    output wire                zero
);

    reg overflow_r, underflow_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count      <= {WIDTH{1'b0}};
            overflow_r <= 1'b0;
            underflow_r <= 1'b0;
        end else if (load) begin
            count      <= load_val;
            overflow_r <= 1'b0;
            underflow_r <= 1'b0;
        end else if (en) begin
            if (up_down) begin
                // Count up
                {overflow_r, count} <= count + 1'b1;
                underflow_r <= 1'b0;
            end else begin
                // Count down
                underflow_r <= (count == {WIDTH{1'b0}});
                count <= count - 1'b1;
                overflow_r <= 1'b0;
            end
        end else begin
            overflow_r <= 1'b0;
            underflow_r <= 1'b0;
        end
    end

    assign overflow  = overflow_r;
    assign underflow = underflow_r;
    assign zero      = (count == {WIDTH{1'b0}});

endmodule
