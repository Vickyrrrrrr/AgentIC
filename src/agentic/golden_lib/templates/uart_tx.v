// Golden Reference: UART Transmitter
// Pre-verified by AgentIC Golden Library
// 8N1 format, configurable baud rate

module uart_tx #(
    parameter CLK_FREQ  = 50000000,
    parameter BAUD_RATE = 115200
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       tx_start,
    input  wire [7:0] tx_data,
    output reg        tx,
    output reg        tx_busy,
    output reg        tx_done
);

    localparam CLKS_PER_BIT = CLK_FREQ / BAUD_RATE;
    localparam CNT_WIDTH    = $clog2(CLKS_PER_BIT);

    localparam [2:0] IDLE  = 3'd0,
                     START = 3'd1,
                     DATA  = 3'd2,
                     STOP  = 3'd3,
                     DONE  = 3'd4;

    reg [2:0]          state;
    reg [CNT_WIDTH-1:0] clk_cnt;
    reg [2:0]          bit_idx;
    reg [7:0]          tx_shift;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= IDLE;
            tx       <= 1'b1;
            tx_busy  <= 1'b0;
            tx_done  <= 1'b0;
            clk_cnt  <= 0;
            bit_idx  <= 0;
            tx_shift <= 0;
        end else begin
            tx_done <= 1'b0;
            
            case (state)
                IDLE: begin
                    tx <= 1'b1;
                    if (tx_start) begin
                        tx_shift <= tx_data;
                        tx_busy  <= 1'b1;
                        state    <= START;
                        clk_cnt  <= 0;
                    end
                end
                
                START: begin
                    tx <= 1'b0;  // Start bit
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        clk_cnt <= 0;
                        bit_idx <= 0;
                        state   <= DATA;
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end
                
                DATA: begin
                    tx <= tx_shift[bit_idx];
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        clk_cnt <= 0;
                        if (bit_idx == 7) begin
                            state <= STOP;
                        end else begin
                            bit_idx <= bit_idx + 1;
                        end
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end
                
                STOP: begin
                    tx <= 1'b1;  // Stop bit
                    if (clk_cnt == CLKS_PER_BIT - 1) begin
                        state   <= DONE;
                        clk_cnt <= 0;
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end
                
                DONE: begin
                    tx_busy <= 1'b0;
                    tx_done <= 1'b1;
                    state   <= IDLE;
                end
                
                default: state <= IDLE;
            endcase
        end
    end

endmodule
