"""
AgentIC VLSI Tools
==================
All EDA tool wrappers and analysis modules.

Modules:
    vlsi_tools.py         - Core simulation, synthesis validation, OpenLane
    synth_tools.py        - Direct Yosys synthesis (RTL → gate-level netlist)
    sta_tools.py          - OpenSTA static timing analysis + multi-corner STA
    dft_tools.py          - Experimental/advisory DFT helpers; production DFT needs commercial adapters
    power_tools.py         - SPEF parsing, power analysis, IR-drop
    physical_tools.py      - Magic DRC, Netgen LVS, antenna checking
    signoff_reporter.py    - Structured QOR/DFM report generation
    checkpoint.py          - Build checkpoint/resume for interrupted builds
    sdf_tools.py          - SDF generation for GLS timing annotation
    ipxact_packager.py    - IP-XACT packaging and ATPG pattern export
"""

from .vlsi_tools import (
    write_verilog,
    run_syntax_check,
    run_lint_check,
    run_simulation,
    run_formal_verification,
    run_openlane,
    check_physical_metrics,
    run_gls_simulation,
    signoff_check_tool,
    run_verification,
    SecurityCheck,
    syntax_check_tool,
    read_file_content,
    read_file_tool,
    write_config,
    write_sby_config,
    startup_self_check,
)

from .synth_tools import (
    run_yosys_synth,
    synth_tool,
    sta_from_synth,
    parse_yosys_timing_report,
    read_synth_checkpoint,
    SynthesisResult,
)

from .sta_tools import (
    run_opensta,
    run_multi_corner_sta,
    sta_tool,
    parse_sdc_file,
    STAReport,
    MultiCornerSTAResult,
)

from .dft_tools import (
    run_scan_insertion,
    run_atpg,
    run_testability_analysis,
    generate_mbist_wrapper,
    generate_jtag_infrastructure,
    dft_tool,
    DFTResult,
    ATPGResult,
    MBISTConfig,
)

from .power_tools import (
    parse_spef,
    run_power_analysis,
    parse_upf_file,
    power_tool,
    compute_net_delay_from_spef,
    PowerAnalysisResult,
    IRDropResult,
    SPEFNet,
)

from .physical_tools import (
    extract_spice_netlist,
    run_magic_drc,
    run_netgen_lvs,
    run_antenna_check,
    drc_tool,
    lvs_tool,
    DRCResult,
    LVSResult,
    AntennaResult,
    SpiceExtractionResult,
)

from .spice_tools import (
    run_ngspice,
    build_basic_post_layout_deck,
    parse_ngspice_measurements,
    NgspiceResult,
)

from .signoff_reporter import (
    generate_qor_report,
    SignoffReporter,
    QORSummary,
    SignoffChecklist,
)

__all__ = [
    # Core VLSI
    "write_verilog",
    "run_syntax_check",
    "run_lint_check",
    "run_simulation",
    "run_formal_verification",
    "run_openlane",
    "check_physical_metrics",
    "run_gls_simulation",
    "signoff_check_tool",
    "run_verification",
    "SecurityCheck",
    "syntax_check_tool",
    "read_file_content",
    "read_file_tool",
    "write_config",
    "write_sby_config",
    "startup_self_check",
    # Synthesis
    "run_yosys_synth",
    "synth_tool",
    "sta_from_synth",
    "parse_yosys_timing_report",
    "read_synth_checkpoint",
    "SynthesisResult",
    # STA
    "run_opensta",
    "run_multi_corner_sta",
    "sta_tool",
    "parse_sdc_file",
    "STAReport",
    "MultiCornerSTAResult",
    # DFT
    "run_scan_insertion",
    "run_atpg",
    "run_testability_analysis",
    "generate_mbist_wrapper",
    "generate_jtag_infrastructure",
    "dft_tool",
    "DFTResult",
    "ATPGResult",
    "MBISTConfig",
    # Power
    "parse_spef",
    "run_power_analysis",
    "parse_upf_file",
    "power_tool",
    "compute_net_delay_from_spef",
    "PowerAnalysisResult",
    "IRDropResult",
    "SPEFNet",
    # Physical
    "extract_spice_netlist",
    "run_magic_drc",
    "run_netgen_lvs",
    "run_antenna_check",
    "drc_tool",
    "lvs_tool",
    "DRCResult",
    "LVSResult",
    "AntennaResult",
    "SpiceExtractionResult",
    # SPICE
    "run_ngspice",
    "build_basic_post_layout_deck",
    "parse_ngspice_measurements",
    "NgspiceResult",
    # Reports
    "generate_qor_report",
    "SignoffReporter",
    "QORSummary",
    "SignoffChecklist",
    # Checkpoint / Resume
    "CheckpointManager",
    "BuildCheckpoint",
    "StageCheckpoint",
    "checkpoint_tool",
    # SDF
    "generate_sdf",
    "generate_sdf_from_opensta",
    "annotate_gls_with_sdf",
    "sdf_tool",
    "SDFResult",
    # IP-XACT
    "generate_ipxact_component",
    "export_atpg_patterns",
    "ipxact_tool",
    "IPXACTComponent",
    "ATPGExportResult",
    # Rate Limiter
    "rate_limited_call",
    "rate_limited_crew_kickoff",
    "set_provider_rate",
    "get_provider_stats",
    # VLSI RAG
    "vlsi_search",
    "pdk_rule_lookup",
    "paper_search",
    # API Manager
    "ApiManager",
    "get_api_manager",
    "reset_api_manager",
    "ApiKeyInfo",
]

from .checkpoint import (
    CheckpointManager,
    BuildCheckpoint,
    StageCheckpoint,
    checkpoint_tool,
)

from .sdf_tools import (
    generate_sdf,
    generate_sdf_from_opensta,
    annotate_gls_with_sdf,
    sdf_tool,
    SDFResult,
)

from .ipxact_packager import (
    generate_ipxact_component,
    export_atpg_patterns,
    ipxact_tool,
    IPXACTComponent,
    ATPGExportResult,
)

from .rate_limiter import (
    rate_limited_call,
    rate_limited_crew_kickoff,
    set_provider_rate,
    get_provider_stats,
)

from .retrieval_tool import (
    vlsi_search,
    pdk_rule_lookup,
    paper_search,
)

from .api_manager import (
    ApiManager,
    get_api_manager,
    reset_api_manager,
    ApiKeyInfo,
)
