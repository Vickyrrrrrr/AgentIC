module simple_counter (
    input wire clk,
    input wire rst_n,
    input wire enable,
    output reg [7:0] count
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 8'b0;
        end else if (enable) begin
            count <= count + 1'b1;
        end
    end

endmodule
