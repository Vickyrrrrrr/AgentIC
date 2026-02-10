// Golden Reference: Synchronous FIFO
// Pre-verified by AgentIC Golden Library
// Features: parameterizable width/depth, full/empty/almost flags

module fifo #(
    parameter DATA_WIDTH = 8,
    parameter DEPTH      = 16,
    parameter ADDR_WIDTH = $clog2(DEPTH)
)(
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    wr_en,
    input  wire                    rd_en,
    input  wire [DATA_WIDTH-1:0]   wr_data,
    output reg  [DATA_WIDTH-1:0]   rd_data,
    output wire                    full,
    output wire                    empty,
    output wire                    almost_full,
    output wire                    almost_empty,
    output reg  [ADDR_WIDTH:0]     count
);

    // Memory array
    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];
    
    // Pointers
    reg [ADDR_WIDTH-1:0] wr_ptr;
    reg [ADDR_WIDTH-1:0] rd_ptr;

    // Status flags
    assign full         = (count == DEPTH);
    assign empty        = (count == 0);
    assign almost_full  = (count >= DEPTH - 1);
    assign almost_empty = (count <= 1);

    // Write logic
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= 0;
        end else if (wr_en && !full) begin
            mem[wr_ptr] <= wr_data;
            wr_ptr <= (wr_ptr == DEPTH - 1) ? 0 : wr_ptr + 1;
        end
    end

    // Read logic
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_ptr  <= 0;
            rd_data <= 0;
        end else if (rd_en && !empty) begin
            rd_data <= mem[rd_ptr];
            rd_ptr <= (rd_ptr == DEPTH - 1) ? 0 : rd_ptr + 1;
        end
    end

    // Count logic
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 0;
        end else begin
            case ({wr_en && !full, rd_en && !empty})
                2'b10: count <= count + 1;  // Write only
                2'b01: count <= count - 1;  // Read only
                default: count <= count;     // Both or neither
            endcase
        end
    end

endmodule
