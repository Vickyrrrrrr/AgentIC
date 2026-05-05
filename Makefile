# AgentIC Makefile - Structured Verification & Hardening Workflow
# Usage: make <target> [DESIGN=name]

DESIGN ?= simple_counter
OPENLANE_ROOT ?= $(HOME)/OpenLane
DESIGN_SRC = designs/$(DESIGN)/src
RTL = $(DESIGN_SRC)/$(DESIGN).v
TB = $(DESIGN_SRC)/$(DESIGN)_tb.v
PDK_ROOT ?= $(HOME)/.ciel
OPENLANE_IMAGE ?= ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-amd64

.PHONY: all lint sim verify harden clean help

# Complete flow
all: lint sim harden verify
	@echo "=========================================="
	@echo "  COMPLETE WORKFLOW FINISHED FOR $(DESIGN)"
	@echo "=========================================="

# Step 1: Lint (syntax check)
lint:
	@echo "━━━ [1/4] LINTING $(DESIGN) ━━━"
	iverilog -g2012 -t null $(RTL)
	@echo "✓ Syntax OK"

# Step 2: Simulate (functional verification)
sim: lint
	@echo "━━━ [2/4] SIMULATING $(DESIGN) ━━━"
	iverilog -g2012 -o $(DESIGN_SRC)/sim $(RTL) $(TB)
	cd $(DESIGN_SRC) && vvp sim | tee sim.log
	@grep -q "TEST PASSED" $(DESIGN_SRC)/sim.log && echo "✓ Simulation PASSED" || (echo "✗ Simulation FAILED" && exit 1)

# Step 3: Harden (RTL → GDSII via OpenLane Docker)
harden:
	@echo "━━━ [3/4] HARDENING $(DESIGN) → GDSII ━━━"
	@echo "Copying design to OpenLane..."
	cp -r designs/$(DESIGN) $(OPENLANE_ROOT)/designs/
	@echo "Running OpenLane flow (may take 5-15 min)..."
	docker run --rm \
		-v $(OPENLANE_ROOT):/openlane \
		-v $(PDK_ROOT):$(PDK_ROOT) \
		-e PDK_ROOT=$(PDK_ROOT) \
		-e PDK=sky130A \
		-e PWD=/openlane \
		$(OPENLANE_IMAGE) \
		./flow.tcl -design $(DESIGN) -tag agent_run_$(shell date +%Y%m%d_%H%M%S) -ignore_mismatches
	@echo "✓ Hardening complete. Check $(OPENLANE_ROOT)/designs/$(DESIGN)/runs/"

# Step 4: Verify (DRC/LVS)
verify:
	@echo "━━━ [4/4] VERIFYING $(DESIGN) (DRC/LVS) ━━━"
	bash scripts/verify_design.sh $(DESIGN)

# Utilities
clean:
	rm -f $(DESIGN_SRC)/sim $(DESIGN_SRC)/sim.log $(DESIGN_SRC)/*.vcd
	@echo "Cleaned simulation artifacts."

help:
	@echo "AgentIC Makefile Targets:"
	@echo "  make lint      - Check Verilog syntax"
	@echo "  make sim       - Run RTL simulation"
	@echo "  make harden    - Run OpenLane (RTL→GDSII)"
	@echo "  make verify    - Run DRC/LVS checks"
	@echo "  make all       - Run complete flow"
	@echo "  make clean     - Remove temp files"
	@echo ""
	@echo "Override design: make DESIGN=my_counter sim"
