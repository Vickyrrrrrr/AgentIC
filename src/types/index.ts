/**
 * Core type definitions for the AgentIC VLSI pipeline modules.
 *
 * These types mirror the Python contracts.py and orchestrator.py enums,
 * adapted for TypeScript consumption by AI agents.
 */

// ── Build State Machine ────────────────────────────────────────────────

export enum BuildState {
  INIT = "INIT",
  SPEC = "SPEC",
  SPEC_VALIDATE = "SPEC_VALIDATE",
  HIERARCHY_EXPAND = "HIERARCHY_EXPAND",
  VERIFICATION_PLAN = "VERIFICATION_PLAN",
  RTL_GEN = "RTL_GEN",
  RTL_FIX = "RTL_FIX",
  CDC_ANALYZE = "CDC_ANALYZE",
  VERIFICATION = "VERIFICATION",
  FORMAL_VERIFY = "FORMAL_VERIFY",
  COVERAGE_CHECK = "COVERAGE_CHECK",
  REGRESSION = "REGRESSION",
  SDC_GEN = "SDC_GEN",
  SYNTHESIS = "SYNTHESIS",
  FEASIBILITY_CHECK = "FEASIBILITY_CHECK",
  DFT_SCAN = "DFT_SCAN",
  DFT_ATPG = "DFT_ATPG",
  MBIST = "MBIST",
  GLS_SIMULATION = "GLS_SIMULATION",
  FLOORPLAN = "FLOORPLAN",
  HARDENING = "HARDENING",
  TIMING_ANALYSIS = "TIMING_ANALYSIS",
  CONVERGENCE_REVIEW = "CONVERGENCE_REVIEW",
  ECO_PATCH = "ECO_PATCH",
  POWER_ANALYSIS = "POWER_ANALYSIS",
  PHYSICAL_VERIFY = "PHYSICAL_VERIFY",
  POST_LAYOUT_SPICE = "POST_LAYOUT_SPICE",
  SIGNOFF = "SIGNOFF",
  IP_PACKAGE = "IP_PACKAGE",
  SUCCESS = "SUCCESS",
  FAIL = "FAIL",
}

export enum BuildStrategy {
  SV_MODULAR = "SV_MODULAR",
  VERILOG_CLASSIC = "VERILOG_CLASSIC",
}

export enum StageStatus {
  PASS = "PASS",
  FAIL = "FAIL",
  RETRY = "RETRY",
  SKIP = "SKIP",
  ERROR = "ERROR",
}

export enum FailureClass {
  EDA_TOOL_ERROR = "EDA_TOOL_ERROR",
  LLM_FORMAT_ERROR = "LLM_FORMAT_ERROR",
  LLM_SEMANTIC_ERROR = "LLM_SEMANTIC_ERROR",
  ORCHESTRATOR_ROUTING_ERROR = "ORCHESTRATOR_ROUTING_ERROR",
  RETRY_BUDGET_ERROR = "RETRY_BUDGET_ERROR",
  INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR",
  UNKNOWN = "UNKNOWN",
}

// ── Hardware Specification Types ───────────────────────────────────────

export interface PortSpec {
  name: string;
  direction: "input" | "output" | "inout";
  data_type: string;
  description: string;
}

export interface SubModuleSpec {
  name: string;
  description: string;
  ports: PortSpec[];
  parameters: { name: string; default: string; description: string }[];
}

export interface BehavioralStatement {
  given: string;
  when: string;
  then: string;
  within: string;
}

export interface InferredField {
  field_name: string;
  inferred_value: string;
  reasoning: string;
}

export interface HardwareSpec {
  design_category: string;
  top_module_name: string;
  target_pdk: string;
  target_frequency_mhz: number;
  ports: PortSpec[];
  submodules: SubModuleSpec[];
  behavioral_contract: BehavioralStatement[];
  inferred_fields: InferredField[];
  warnings: string[];
  design_description: string;
  mandatory_fields_status: Record<string, { status: string; value: string; reasoning?: string }>;
}

// ── Artifact & Stage Result Types ─────────────────────────────────────

export interface ArtifactRef {
  key: string;
  producer: string;
  consumer: string;
  required: boolean;
  blocking: boolean;
  value: unknown;
}

export interface StageResult {
  stage: string;
  status: StageStatus;
  producer: string;
  failure_class: FailureClass;
  consumable_payload: Record<string, unknown>;
  diagnostics: string[];
  artifacts_written: string[];
  next_action: string;
}

export interface AgentResult {
  agent: string;
  ok: boolean;
  producer: string;
  payload: Record<string, unknown>;
  diagnostics: string[];
  failure_class: FailureClass;
  raw_output: string;
}

// ── PDK Configuration ─────────────────────────────────────────────────

export interface PdkProfile {
  pdk: string;
  std_cell_library: string;
  default_clock_period: string;
  voltage_vdd: string;
  min_cell_height: string;
  description: string;
  fabrication_ready: boolean;
  maturity: string;
  profile?: string;
}

export interface PdkToolConfig {
  pdk_dir: string;
  std_cell_library: string;
  default_clock_period: string;
  max_reliable_mhz: number;
  upper_limit_mhz: number;
  metal_layers: number;
  voltage_vdd: string;
  min_cell_height: string;
  lc_area_um2: number;
  openlane_max_routing_layer: string;
  enforce_openlane_max_routing_layer: boolean;
  min_die_um: number;
  default_core_util: number;
  max_core_util: number;
  grt_adjustment: number;
  drc_rules: string;
  lvs_rules: string;
  timing_libs: string[];
}

export interface PdkFlowCapabilities {
  profile: string;
  pdk: string;
  std_cell_library: string;
  node_nm: number | null;
  node_class: "advanced" | "legacy" | "generic";
  maturity: string;
  advanced_node: boolean;
  legacy_node: boolean;
  proprietary: boolean;
  custom: boolean;
  flow_status: string;
  fabrication_ready: boolean;
  collateral_ready: boolean;
  wire_rc_risk: string;
  metal_layers: number;
  max_routing_layer: string;
  enforce_max_routing_layer: boolean;
  lc_area_um2: number;
  min_die_um: number;
  default_core_util: number;
  max_core_util: number;
  grt_adjustment: number;
  memory_macro_threshold_bytes: number;
  rtl_guidance: string;
  tool_config: PdkToolConfig;
}

// ── LLM Configuration ─────────────────────────────────────────────────

export interface LlmConfig {
  model: string;
  base_url: string;
  api_key: string;
  temperature?: number;
  max_tokens?: number;
}

export interface RoleLlmConfig {
  architect: LlmConfig;
  designer: LlmConfig;
  verifier: LlmConfig;
  testbench: LlmConfig;
  sdc: LlmConfig;
  doc: LlmConfig;
  fixer: LlmConfig;
}

// ── EDA Tool Results ──────────────────────────────────────────────────

export interface EdaToolResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  exit_code: number;
  structured_errors?: StructuredError[];
  artifacts?: Record<string, string>;
}

export interface StructuredError {
  type: string;
  message: string;
  file?: string;
  line?: number;
  severity: "error" | "warning" | "info";
}

// ── Pipeline Build Context ────────────────────────────────────────────

export interface BuildContext {
  name: string;
  description: string;
  pdk_profile: PdkProfile;
  pdk_tool_config: PdkToolConfig;
  pdk_flow_capabilities: PdkFlowCapabilities;
  llm_config: LlmConfig;
  role_llms?: Partial<RoleLlmConfig>;
  strategy: BuildStrategy;
  artifacts: Record<string, unknown>;
  state: BuildState;
  state_history: BuildState[];
  retry_count: number;
  max_retries: number;
  workspace_root: string;
  skip_openlane: boolean;
  skip_spice: boolean;
  skip_coverage: boolean;
  min_coverage: number;
  strict_gates: boolean;
  event_sink?: (event: BuildEvent) => void;
}

export interface BuildEvent {
  type: "transition" | "log" | "checkpoint" | "error" | "success";
  state: string;
  message: string;
  data?: Record<string, unknown>;
}

// ── Stage Handler Interface ───────────────────────────────────────────

export interface StageHandler {
  name: string;
  state: BuildState;
  execute(ctx: BuildContext): Promise<StageResult>;
}

export type StageFactory = (ctx: BuildContext) => StageHandler;

// ── Recovery ──────────────────────────────────────────────────────────

export enum RecoveryAction {
  RETRY_SAME = "RETRY_SAME",
  FIX_RTL = "FIX_RTL",
  REGEN_SDC = "REGEN_SDC",
  RELAX_CLOCK = "RELAX_CLOCK",
  EXPAND_AREA = "EXPAND_AREA",
  REDUCE_UTIL = "REDUCE_UTIL",
  REGEN_CONFIG = "REGEN_CONFIG",
  SWITCH_STRATEGY = "SWITCH_STRATEGY",
  PIPELINE_CRITICAL = "PIPELINE_CRITICAL",
  FAIL = "FAIL",
}

export interface RecoveryResult {
  action: RecoveryAction;
  description: string;
  params: Record<string, unknown>;
  confidence: number;
  needs_rtl_fix: boolean;
  needs_sdc_regen: boolean;
  needs_config_regen: boolean;
}

// ── Design Category ───────────────────────────────────────────────────

export type DesignCategory =
  | "PROCESSOR"
  | "MEMORY"
  | "INTERFACE"
  | "ARITHMETIC"
  | "CONTROL"
  | "DATAPATH"
  | "MIXED";
