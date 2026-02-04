#!/bin/bash

# Usage: ./verify_design.sh <design_name>

if [ -z "$1" ]; then
    echo "Usage: ./verify_design.sh <design_name>"
    exit 1
fi

DESIGN=$1
OPENLANE_ROOT="${OPENLANE_ROOT:-$HOME/OpenLane}"
DESIGN_DIR=$OPENLANE_ROOT/designs/$DESIGN

echo "========================================"
echo "Verifying Design: $DESIGN"
echo "========================================"

# 1. Functional Verification (RTL Simulation)
echo "[1] Functional Verification (Icarus Verilog)"
if [ -f "$DESIGN_DIR/src/${DESIGN}_tb.v" ]; then
    echo "    Found Testbench: ${DESIGN}_tb.v"
    cd $DESIGN_DIR/src
    iverilog -o sim ${DESIGN}.v ${DESIGN}_tb.v
    if [ $? -eq 0 ]; then
        echo "    Compilation Successful."
        echo "    Running Simulation..."
        vvp sim > simulation.log
        cat simulation.log
        echo "    (Check output above for expected behavior)"
    else
        echo "    [ERROR] Compilation Failed."
    fi
    cd $OPENLANE_ROOT
else
    echo "    [WARNING] No testbench found at $DESIGN_DIR/src/${DESIGN}_tb.v"
    echo "    Please create one to run functional simulation."
fi

echo ""
echo "========================================"

# 2. Physical Verification (OpenLane Reports)
echo "[2] Physical Verification (Latest Run)"
LATEST_RUN=$(ls -td $DESIGN_DIR/runs/* | head -1)

if [ -z "$LATEST_RUN" ]; then
    echo "    [ERROR] No runs found for $DESIGN."
else
    echo "    Checking run: $(basename $LATEST_RUN)"
    
    # Check DRC
    DRC_RPT=$(find $LATEST_RUN -name "*drc.rpt" | head -1)
    if [ -f "$DRC_RPT" ]; then
        echo "    --- DRC Report ($(basename $DRC_RPT)) ---"
        COUNT=$(grep -c "violation" $DRC_RPT)
        if [ "$COUNT" -eq "0" ]; then
             # Some reports might use different format, check content
             grep "Count: 0" $DRC_RPT > /dev/null
             if [ $? -eq 0 ]; then
                echo "    [PASS] No DRC Violations found."
             else
                # Print last few lines
                tail -n 5 $DRC_RPT
             fi
        else
            echo "    [FAIL] Found potential violations."
            tail -n 5 $DRC_RPT
        fi
    else
        echo "    [WARNING] DRC Report not found."
    fi

    echo ""
    
    # Check LVS
    LVS_RPT=$(find $LATEST_RUN -name "*lvs.log" | head -1)
    if [ -f "$LVS_RPT" ]; then
        echo "    --- LVS Report ($(basename $LVS_RPT)) ---"
        if grep -q "Total errors = 0" $LVS_RPT || grep -q "Circuits match uniquely" $LVS_RPT; then
            echo "    [PASS] LVS Clean. Layout matches Schematic."
        else
            echo "    [FAIL] LVS Mismatch."
            tail -n 3 $LVS_RPT
        fi
    else
         echo "    [WARNING] LVS Report not found."
    fi
fi

echo "========================================"
echo "Verification Complete."
