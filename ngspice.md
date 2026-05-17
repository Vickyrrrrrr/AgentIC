# Integrate ngspice for Post-Layout Simulation

This plan outlines the architectural changes required to make AgentIC a complete one-stop solution for fabrication-ready chips across any node by adding full transistor-level SPICE simulation capabilities.

## User Review Required
> [!IMPORTANT]
> Running SPICE simulations on full digital chip netlists is extremely computationally expensive. Should we limit the simulation to specifically extracted critical paths (identified via OpenSTA), or provide the agent with the entire extracted netlist? The default plan extracts the entire block but tasks the AI with focusing on critical sub-circuits. Please confirm if you want to proceed with this architecture.

## Proposed Changes

### Docker / Infrastructure
#### [MODIFY] [Dockerfile](file:///home/vickynishad/AgentIC/Dockerfile)
- Add `ngspice` to the `apt-get install` block in the second-stage worker build so the backend container can natively execute SPICE simulations.

---

### Tools & Capabilities
#### [MODIFY] [physical_tools.py](file:///home/vickynishad/AgentIC/src/agentic/tools/physical_tools.py)
- Add a new function `extract_spice_netlist(gds_path, tech_file, output_dir)` that runs a Magic TCL script to extract a fully parasitic-aware SPICE netlist (`ext2spice cthresh 0 rthresh 0`) from the final GDS.

#### [NEW] [spice_tools.py](file:///home/vickynishad/AgentIC/src/agentic/tools/spice_tools.py)
- Create a new tool file wrapping `ngspice`.
- Add `run_ngspice(spice_deck: str, output_dir: str) -> dict`. This will execute `ngspice -b` and parse measurements (rise time, fall time, delay, peak power) from the simulation output to feed back to the LLM agent.

---

### Backend Pipeline Orchestration
#### [MODIFY] [orchestrator.py](file:///home/vickynishad/AgentIC/src/agentic/orchestrator.py)
- Add `POST_LAYOUT_SPICE` to the `BuildState` enum.
- Implement the `do_post_layout_spice(self)` stage. The Agent will:
  1. Call the new extraction tool to get the `.spice` netlist.
  2. Write a stimulus deck (`.sp`) testing the clock/critical path.
  3. Invoke `ngspice` and record the results into the orchestrator artifacts for final signoff.

#### [MODIFY] [api.py](file:///home/vickynishad/AgentIC/server/api.py)
- Inject `POST_LAYOUT_SPICE` into the `BUILD_STATES_ORDER` array immediately before `SIGNOFF`.
- Update `STAGE_META` to provide the frontend timeline with an icon (⚡) and description for the new SPICE simulation phase.

### Command-Line Interface (CLI)
#### [MODIFY] [cli.py](file:///home/vickynishad/AgentIC/src/agentic/cli.py)
- The main `agentic build <prompt>` command will automatically inherit the new `POST_LAYOUT_SPICE` stage since it shares the `VLSIOrchestrator` engine.
- Add an explicit `--skip-spice` flag to the `build` command (similar to `--skip-openlane`) so users can optionally bypass this computationally expensive step during early RTL iterations.
- Add a standalone `agentic spice <layout.gds>` command that allows users to quickly extract and simulate an existing GDS layout directly from the terminal without running a full build.

## Verification Plan
### Automated Tests
- Run the agent on a minimal counter circuit.
- Verify that `ngspice` executes correctly and the extracted metrics are successfully appended to the final signoff report.

### Manual Verification
- Observe the Design Studio web UI to ensure the "Spice Sim" state correctly appears in the timeline during physical implementation.
- Check the `artifacts/` folder of the job for the raw `sim.sp` and `sim.raw` outputs.
