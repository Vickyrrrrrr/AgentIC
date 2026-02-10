// Golden Reference: Generic FSM Template
// Pre-verified by AgentIC Golden Library
// Mealy/Moore configurable, separate state/next_state pattern

module fsm #(
    parameter NUM_STATES = 4
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire [3:0] cmd,
    input  wire       valid,
    output reg  [3:0] status,
    output reg        ready,
    output reg        error,
    output reg        done
);

    // State encoding
    localparam [2:0] S_IDLE    = 3'd0,
                     S_SETUP   = 3'd1,
                     S_EXECUTE = 3'd2,
                     S_DONE    = 3'd3,
                     S_ERROR   = 3'd4;

    reg [2:0] state, next_state;
    reg [7:0] counter;

    // State register (sequential)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state <= S_IDLE;
        else
            state <= next_state;
    end

    // Next state logic (combinational)
    always @(*) begin
        next_state = state;  // Default: hold
        case (state)
            S_IDLE: begin
                if (valid)
                    next_state = S_SETUP;
            end
            S_SETUP: begin
                if (cmd == 4'hF)
                    next_state = S_ERROR;
                else
                    next_state = S_EXECUTE;
            end
            S_EXECUTE: begin
                if (counter >= 8'd10)
                    next_state = S_DONE;
            end
            S_DONE: begin
                next_state = S_IDLE;
            end
            S_ERROR: begin
                if (valid)
                    next_state = S_IDLE;
            end
            default: next_state = S_IDLE;
        endcase
    end

    // Output logic and datapath (sequential)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            status  <= 4'd0;
            ready   <= 1'b1;
            error   <= 1'b0;
            done    <= 1'b0;
            counter <= 8'd0;
        end else begin
            done <= 1'b0;
            case (state)
                S_IDLE: begin
                    ready   <= 1'b1;
                    error   <= 1'b0;
                    counter <= 8'd0;
                    status  <= 4'd0;
                end
                S_SETUP: begin
                    ready  <= 1'b0;
                    status <= cmd;
                end
                S_EXECUTE: begin
                    counter <= counter + 1;
                    status  <= 4'd1;
                end
                S_DONE: begin
                    done   <= 1'b1;
                    status <= 4'd2;
                end
                S_ERROR: begin
                    error  <= 1'b1;
                    status <= 4'hE;
                end
            endcase
        end
    end

endmodule
