// Golden Reference: Shift Register
// Pre-verified by AgentIC Golden Library

module shift_register #(
    parameter WIDTH = 8
)(
    input  wire              clk,
    input  wire              rst_n,
    input  wire              en,
    input  wire              load,
    input  wire              dir,       // 0 = left, 1 = right
    input  wire              serial_in,
    input  wire [WIDTH-1:0]  parallel_in,
    output wire              serial_out,
    output reg  [WIDTH-1:0]  parallel_out
);

    assign serial_out = dir ? parallel_out[0] : parallel_out[WIDTH-1];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            parallel_out <= {WIDTH{1'b0}};
        end else if (load) begin
            parallel_out <= parallel_in;
        end else if (en) begin
            if (dir) // Right shift
                parallel_out <= {serial_in, parallel_out[WIDTH-1:1]};
            else     // Left shift
                parallel_out <= {parallel_out[WIDTH-2:0], serial_in};
        end
    end

endmodule
