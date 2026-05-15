# VLSI Open Resources for AgentIC RAG

This curated seed file gives AgentIC a maintained public-source baseline for RTL-to-GDSII, open PDKs, verification, and recent AI-assisted silicon-flow research. It intentionally avoids proprietary foundry details.

## RTL-to-GDSII and Open-Source EDA

- OpenROAD documentation: https://openroad.readthedocs.io/en/latest/main/README2.html
  OpenROAD describes a modern RTL-to-GDSII flow that moves from RTL and SDC through synthesis, floorplanning, placement, CTS, routing, finishing, timing signoff, GDS generation, and physical checks. It is PDK-independent, while common packaged flows support open PDKs such as SkyWater 130nm and GF180.

- OpenROAD Flow Scripts: https://openroad-flow-scripts.readthedocs.io/en/stable/
  OpenROAD-flow-scripts are the reference open-source automation layer around Yosys and OpenROAD. Use this material for flow bring-up, platform conventions, floorplan variables, and examples when OpenLane is not the right abstraction.

- OpenLane 2 documentation: https://openlane2.readthedocs.io/en/latest/
  OpenLane 2 is the current Efabless flow framework for RTL-to-GDSII using Yosys, OpenROAD, KLayout, and Magic. Its Classic flow is closest to OpenLane 1 behavior and is useful for simpler digital designs. Treat OpenLane 2 defaults as actively evolving and verify generated layouts with DRC/LVS/timing reports.

- OpenLane PDK usage: https://openlane2.readthedocs.io/en/dev/usage/about_pdks.html
  OpenLane expects a fully qualified PDK variant such as `sky130A` rather than only the family name `sky130`. Efabless-supported SkyWater 130nm and GF180MCU builds are commonly managed through Volare.

- OSS CAD Suite: https://github.com/YosysHQ/oss-cad-suite-build
  OSS CAD Suite packages key open-source digital design tools including Yosys, Verilator, Icarus Verilog, SymbiYosys, nextpnr, and related utilities. After extraction, `OSS_CAD_SUITE_HOME` should point at the directory containing `bin/yosys`.

## Open PDKs and Process Collateral

- Google maintained open-source PDK docs: https://open-source-pdks.readthedocs.io/
  This documentation is the shared entry point for Google-maintained public PDK material, including SkyWater SKY130 and GF180MCU links. Prefer it for public PDK overview and known open collateral.

- SkyWater SKY130 PDK docs: https://skywater-pdk.readthedocs.io/
  SKY130 is the most mature public PDK for open-source ASIC work. Use its docs for library naming, primitive behavior, design rules, standard cells, I/O, and SPICE/model collateral.

- GF180MCU PDK docs: https://gdsfactory.github.io/gf180mcu/
  GF180MCU is an open 180nm platform suited to larger geometry, mixed-signal, and educational silicon work. Use its public docs for library/package orientation and flow constraints.

- Volare: https://pypi.org/project/volare/
  Volare manages prebuilt open PDK artifacts by open_pdks commit hash. It supports SkyWater SKY130 and GF180MCU families and uses `PDK_ROOT` or an explicit `--pdk-root` to activate builds.

## Advanced and Predictive Nodes

- ASAP7 GitHub release note: https://theopenroadproject.org/news/openroad-releases-asap7-7nm-predictive-pdk-on-github/
  ASAP7 is a 7nm predictive PDK released by OpenROAD on GitHub under BSD-3. It was developed at Arizona State University in collaboration with ARM Research and is intended for research/benchmarking rather than manufacturing.

- ASAP7 project page: https://asap.asu.edu/
  ASU describes ASAP7 as an academic and research aid only; resulting designs are not manufacturable. The public kit includes SPICE-compatible FinFET models, technology files, design-rule collateral, LVS/extraction collateral, and documentation. Treat any ASAP7 result as predictive PPA, not foundry signoff.

- ASAP7 paper: https://doi.org/10.1016/j.mejo.2016.04.006
  The foundational ASAP7 paper defines it as a 7nm FinFET predictive process design kit for academic benchmarking. AgentIC should use it to reason about advanced-node constraints such as low supply voltage, FinFET standard-cell libraries, dense routing, multiple patterning assumptions, and research comparisons.

- OpenROAD ASAP7 support: https://github.com/The-OpenROAD-Project/OpenROAD
  OpenROAD identifies ASAP7 as a predictive FinFET 7nm platform. In AgentIC, map `pdk_profile=asap7` to the `asap7` PDK directory and the `asap7sc7p5t` standard-cell library.

## Verification and Testbench Practice

- cocotb documentation: https://docs.cocotb.org/en/stable/index.html
  cocotb is a Python-based open-source verification framework that enables reusable, randomized, and self-checking testbenches without requiring a commercial SystemVerilog UVM simulator.

- SystemVerilog.io verification articles: https://www.systemverilog.io/verification/
  Use this as a public reference for SystemVerilog Assertions, UVM concepts, and formal/DV foundations. AgentIC should prefer synthesizable RTL plus portable SVA and self-checking tests for open-source flows.

- Accellera UVM standard: https://accellera.org/downloads/standards/uvm
  UVM is the standardized SystemVerilog verification methodology. Public open-source simulators still have limited full UVM support, so AgentIC should generate UVM-lite or cocotb-compatible benches unless a commercial simulator is explicitly available.

## Recent AI-Assisted Silicon-Flow Research

- NL2GDS: https://arxiv.org/abs/2603.05489
  NL2GDS, published in March 2026, explores translating natural-language hardware descriptions into synthesizable RTL and complete layouts through open-source flows such as OpenLane. Use it as motivation for natural-language-to-layout orchestration, not as a substitute for signoff checks.

- QiMeng-CodeV-SVA: https://arxiv.org/abs/2603.14239
  This March 2026 work focuses on training specialized LLMs for RTL-grounded SystemVerilog assertion generation. AgentIC should use retrieved design intent, interface contracts, and RTL structure when generating SVA rather than producing generic assertions.

## AgentIC Retrieval Guidance

- Always separate public open PDK facts from proprietary foundry assumptions.
- For PDK questions, retrieve by family and variant: `sky130` plus `sky130A`, `gf180mcu` plus `gf180mcuC`.
- For layout questions, include the flow name (`OpenLane2`, `OpenROAD-flow-scripts`, or `OpenROAD`) and the target signoff tool (`Magic`, `Netgen`, `KLayout`, `OpenSTA`).
- For verification questions, prefer portable self-checking tests, SVA, cocotb, and Verilator/Icarus constraints unless the user explicitly has a commercial UVM simulator.
- For AI-generated silicon flows, require artifact-grounded evidence: RTL, testbench results, synthesis logs, STA reports, DRC/LVS output, and final GDS location.
