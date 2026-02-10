// Golden Reference: SPI Master
// Pre-verified by AgentIC Golden Library
// Mode 0 (CPOL=0, CPHA=0), configurable clock divider, 8-bit transfers

module spi_master #(
    parameter CLK_DIV = 4  // SPI clock = clk / (2 * CLK_DIV)
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       start,
    input  wire [7:0] mosi_data,
    output reg  [7:0] miso_data,
    output reg        sclk,
    output reg        mosi,
    input  wire       miso,
    output reg        cs_n,
    output reg        busy,
    output reg        done
);

    localparam CNT_WIDTH = $clog2(CLK_DIV);

    localparam [2:0] IDLE     = 3'd0,
                     CS_SETUP = 3'd1,
                     TRANSFER = 3'd2,
                     CS_HOLD  = 3'd3,
                     FINISH   = 3'd4;

    reg [2:0]          state;
    reg [CNT_WIDTH-1:0] clk_cnt;
    reg [2:0]          bit_cnt;
    reg [7:0]          shift_out;
    reg [7:0]          shift_in;
    reg                sclk_edge;  // 0 = rising, 1 = falling

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= IDLE;
            sclk      <= 1'b0;
            mosi      <= 1'b0;
            cs_n      <= 1'b1;
            busy      <= 1'b0;
            done      <= 1'b0;
            clk_cnt   <= 0;
            bit_cnt   <= 0;
            shift_out <= 0;
            shift_in  <= 0;
            miso_data <= 0;
            sclk_edge <= 0;
        end else begin
            done <= 1'b0;
            
            case (state)
                IDLE: begin
                    sclk <= 1'b0;
                    cs_n <= 1'b1;
                    if (start) begin
                        shift_out <= mosi_data;
                        shift_in  <= 0;
                        busy      <= 1'b1;
                        bit_cnt   <= 0;
                        state     <= CS_SETUP;
                        clk_cnt   <= 0;
                    end
                end
                
                CS_SETUP: begin
                    cs_n <= 1'b0;
                    mosi <= mosi_data[7];  // MSB first
                    if (clk_cnt == CLK_DIV - 1) begin
                        clk_cnt   <= 0;
                        sclk_edge <= 0;
                        state     <= TRANSFER;
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end
                
                TRANSFER: begin
                    if (clk_cnt == CLK_DIV - 1) begin
                        clk_cnt <= 0;
                        if (!sclk_edge) begin
                            // Rising edge: sample MISO
                            sclk <= 1'b1;
                            shift_in <= {shift_in[6:0], miso};
                            sclk_edge <= 1;
                        end else begin
                            // Falling edge: shift MOSI
                            sclk <= 1'b0;
                            sclk_edge <= 0;
                            if (bit_cnt == 7) begin
                                state <= CS_HOLD;
                            end else begin
                                bit_cnt   <= bit_cnt + 1;
                                shift_out <= {shift_out[6:0], 1'b0};
                                mosi      <= shift_out[6];
                            end
                        end
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end
                
                CS_HOLD: begin
                    sclk <= 1'b0;
                    if (clk_cnt == CLK_DIV - 1) begin
                        cs_n      <= 1'b1;
                        miso_data <= shift_in;
                        state     <= FINISH;
                    end else begin
                        clk_cnt <= clk_cnt + 1;
                    end
                end
                
                FINISH: begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= IDLE;
                end
                
                default: state <= IDLE;
            endcase
        end
    end

endmodule
