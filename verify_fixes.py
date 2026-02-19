import re
import os
import sys

# Add src to path
sys.path.append(os.path.abspath("src"))

from agentic.tools import vlsi_tools

def check_duplicates(file_path):
    print(f"Checking for duplicates in {file_path}...")
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Find all function definitions
    funcs = re.findall(r'^def\s+(\w+)\s*\(', content, re.MULTILINE)
    seen = set()
    dupes = []
    for f in funcs:
        if f in seen:
            dupes.append(f)
        seen.add(f)
    
    if dupes:
        print(f"FAILED: Found duplicate functions: {dupes}")
        return False
    print("PASSED: No duplicate functions found.")
    return True

def test_validate_rtl():
    print("\nTesting validate_rtl_for_synthesis logic...")
    
    # Sample RTL with undriven signals (active high and low)
    rtl = """
    module test_module (
        input clk,
        input rst_n
    );
        reg [7:0] data_reg;
        wire valid_signal;
        wire [3:0] active_low_b;
        wire enable_n;
        
        always @(posedge clk) begin
            if (enable_n) begin
                 data_reg <= 8'h00;
            end
        end
        
        assign valid_signal = 1'b1; // Driven
        
        // active_low_b is used in expression but not driven
        wire check = &active_low_b; 
    endmodule
    """
    
    # Write to temp file
    test_file = "temp_test.v"
    with open(test_file, 'w') as f:
        f.write(rtl)
        
    try:
        fixed, report = vlsi_tools.validate_rtl_for_synthesis(test_file)
        
        with open(test_file, 'r') as f:
            fixed_rtl = f.read()
            
        print(f"Report:\n{report}")
        
        # Checks
        if "active_low_b = {4{1'b1}}" in fixed_rtl:
            print("PASSED: active_low_b tied to 1s")
        else:
            print(f"FAILED: active_low_b fix incorrect. Content:\n{fixed_rtl}")
            
        if "enable_n = 1'b1" in fixed_rtl:
            print("PASSED: enable_n tied to 1")
        else:
             print(f"FAILED: enable_n fix incorrect. Content:\n{fixed_rtl}")

    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == "__main__":
    vlsi_path = "src/agentic/tools/vlsi_tools.py"
    if check_duplicates(vlsi_path):
        test_validate_rtl()
    else:
        sys.exit(1)
