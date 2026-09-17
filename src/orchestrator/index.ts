/**
 * Pipeline orchestrator — wires all stages together with a state machine,
 * retry logic, and recovery strategies.
 *
 * Ported from Python: src/agentic/core/orchestrator.py
 */
import type {
  BuildContext,
  BuildEvent,
  StageResult,
  RecoveryResult,
  PdkProfile,
  PdkToolConfig,
  PdkFlowCapabilities,
  LlmConfig,
  RoleLlmConfig,
} from "../types/index.js";
import {
  BuildState,
  BuildStrategy,
  StageStatus,
  FailureClass,
  RecoveryAction,
} from "../types/index.js";
import { executeSpecStage } from "../stages/spec.js";
import { executeRtlGenStage, executeRtlFixStage } from "../stages/rtl.js";
import { executeVerificationStage, executeFormalVerifyStage } from "../stages/testbench.js";
import { executeSdcGenStage } from "../stages/sdc.js";
import { executeSynthesisStage } from "../stages/synthesis.js";
import { executeStaStage } from "../stages/sta.js";
import { executePhysicalVerifyStage } from "../stages/physical.js";
import { executeSignoffStage } from "../stages/signoff.js";

// ── State Transition Table ───────────────────────────────────────────
const STATE_TRANSITIONS: Record<BuildState, BuildState[]> = {
  [BuildState.INIT]:              [BuildState.SPEC],
  [BuildState.SPEC]:              [BuildState.RTL_GEN],
  [BuildState.SPEC_VALIDATE]:     [BuildState.RTL_GEN],
  [BuildState.HIERARCHY_EXPAND]:  [BuildState.RTL_GEN],
  [BuildState.VERIFICATION_PLAN]: [BuildState.RTL_GEN],
  [BuildState.RTL_GEN]:           [BuildState.RTL_FIX, BuildState.VERIFICATION],
  [BuildState.RTL_FIX]:           [BuildState.RTL_GEN, BuildState.VERIFICATION],
  [BuildState.CDC_ANALYZE]:       [BuildState.VERIFICATION],
  [BuildState.VERIFICATION]:      [BuildState.FORMAL_VERIFY, BuildState.RTL_FIX],
  [BuildState.FORMAL_VERIFY]:     [BuildState.SDC_GEN],
  [BuildState.COVERAGE_CHECK]:    [BuildState.SDC_GEN],
  [BuildState.REGRESSION]:        [BuildState.SDC_GEN],
  [BuildState.SDC_GEN]:           [BuildState.SYNTHESIS],
  [BuildState.SYNTHESIS]:         [BuildState.TIMING_ANALYSIS],
  [BuildState.FEASIBILITY_CHECK]: [BuildState.SYNTHESIS],
  [BuildState.DFT_SCAN]:          [BuildState.HARDENING],
  [BuildState.DFT_ATPG]:          [BuildState.HARDENING],
  [BuildState.MBIST]:             [BuildState.HARDENING],
  [BuildState.GLS_SIMULATION]:    [BuildState.HARDENING],
  [BuildState.FLOORPLAN]:         [BuildState.HARDENING],
  [BuildState.HARDENING]:         [BuildState.PHYSICAL_VERIFY],
  [BuildState.TIMING_ANALYSIS]:   [BuildState.DFT_SCAN, BuildState.HARDENING],
  [BuildState.CONVERGENCE_REVIEW]:[BuildState.ECO_PATCH],
  [BuildState.ECO_PATCH]:         [BuildState.TIMING_ANALYSIS],
  [BuildState.POWER_ANALYSIS]:    [BuildState.SIGNOFF],
  [BuildState.PHYSICAL_VERIFY]:   [BuildState.POWER_ANALYSIS],
  [BuildState.POST_LAYOUT_SPICE]: [BuildState.SIGNOFF],
  [BuildState.SIGNOFF]:           [BuildState.SUCCESS, BuildState.FAIL],
  [BuildState.IP_PACKAGE]:        [BuildState.SUCCESS],
  [BuildState.SUCCESS]:           [],
  [BuildState.FAIL]:              [],
};

// ── Stage Registry ───────────────────────────────────────────────────
type StageExecutor = (ctx: BuildContext) => Promise<StageResult>;

const STAGE_REGISTRY: Partial<Record<BuildState, StageExecutor>> = {
  [BuildState.SPEC]:            executeSpecStage,
  [BuildState.RTL_GEN]:         executeRtlGenStage,
  [BuildState.RTL_FIX]:         executeRtlFixStage,
  [BuildState.VERIFICATION]:    executeVerificationStage,
  [BuildState.FORMAL_VERIFY]:   executeFormalVerifyStage,
  [BuildState.SDC_GEN]:         executeSdcGenStage,
  [BuildState.SYNTHESIS]:       executeSynthesisStage,
  [BuildState.TIMING_ANALYSIS]: executeStaStage,
  [BuildState.PHYSICAL_VERIFY]: executePhysicalVerifyStage,
  [BuildState.SIGNOFF]:         executeSignoffStage,
};

// ── Config ───────────────────────────────────────────────────────────
export interface OrchestratorConfig {
  name: string;
  description: string;
  pdkProfile: PdkProfile;
  pdkToolConfig: PdkToolConfig;
  pdkFlowCapabilities: PdkFlowCapabilities;
  llmConfig: LlmConfig;
  roleLlms?: Partial<RoleLlmConfig>;
  strategy?: BuildStrategy;
  maxRetries?: number;
  skipOpenlane?: boolean;
  skipSpice?: boolean;
  skipCoverage?: boolean;
  minCoverage?: number;
  strictGates?: boolean;
  onEvent?: (event: BuildEvent) => void;
}

// ── Context Factory ──────────────────────────────────────────────────
export function createBuildContext(config: OrchestratorConfig): BuildContext {
  return {
    name: config.name,
    description: config.description,
    pdk_profile: config.pdkProfile,
    pdk_tool_config: config.pdkToolConfig,
    pdk_flow_capabilities: config.pdkFlowCapabilities,
    llm_config: config.llmConfig,
    role_llms: config.roleLlms,
    strategy: config.strategy ?? BuildStrategy.SV_MODULAR,
    artifacts: {},
    state: BuildState.INIT,
    state_history: [BuildState.INIT],
    retry_count: 0,
    max_retries: config.maxRetries ?? 3,
    workspace_root: "",
    skip_openlane: config.skipOpenlane ?? false,
    skip_spice: config.skipSpice ?? false,
    skip_coverage: config.skipCoverage ?? false,
    min_coverage: config.minCoverage ?? 80,
    strict_gates: config.strictGates ?? false,
    event_sink: config.onEvent,
  };
}

// ── Recovery Logic ───────────────────────────────────────────────────
function makeRecovery(
  action: RecoveryAction,
  description: string,
  confidence: number,
  opts: { rtl?: boolean; sdc?: boolean; config?: boolean } = {},
): RecoveryResult {
  return {
    action, description, params: {}, confidence,
    needs_rtl_fix: opts.rtl ?? false,
    needs_sdc_regen: opts.sdc ?? false,
    needs_config_regen: opts.config ?? false,
  };
}

const FAIL_RECOVERY: RecoveryResult = makeRecovery(
  RecoveryAction.FAIL, "Retry budget exhausted", 1.0,
);

export function chooseRecovery(result: StageResult, ctx: BuildContext): RecoveryResult {
  if (ctx.retry_count >= ctx.max_retries) return FAIL_RECOVERY;

  const { stage, failure_class: fc } = result;

  if (fc === FailureClass.EDA_TOOL_ERROR) {
    const rtlStages: string[] = [BuildState.RTL_GEN, BuildState.RTL_FIX, BuildState.VERIFICATION];
    if (rtlStages.includes(stage))
      return makeRecovery(RecoveryAction.FIX_RTL, "EDA error in RTL pipeline — fixing RTL", 0.6, { rtl: true });
    if (stage === BuildState.TIMING_ANALYSIS)
      return makeRecovery(RecoveryAction.RELAX_CLOCK, "Timing EDA error — relaxing clock", 0.5, { sdc: true });
    return makeRecovery(RecoveryAction.RETRY_SAME, "EDA tool error — retrying", 0.4);
  }

  if (fc === FailureClass.LLM_FORMAT_ERROR || fc === FailureClass.LLM_SEMANTIC_ERROR)
    return makeRecovery(RecoveryAction.RETRY_SAME, `LLM error (${fc}) — retrying`, 0.7);

  // Stage-specific recovery for non-EDA, non-LLM failures
  if (stage === BuildState.TIMING_ANALYSIS)
    return makeRecovery(RecoveryAction.RELAX_CLOCK, "Timing failure — relaxing clock", 0.5, { sdc: true });
  if (stage === BuildState.SDC_GEN)
    return makeRecovery(RecoveryAction.REGEN_SDC, "SDC failure — regenerating constraints", 0.6, { sdc: true });
  if (stage === BuildState.PHYSICAL_VERIFY || stage === BuildState.HARDENING)
    return makeRecovery(RecoveryAction.EXPAND_AREA, "Physical failure — expanding area", 0.5, { config: true });

  return makeRecovery(RecoveryAction.RETRY_SAME, `Unclassified failure in ${stage} — retrying`, 0.3);
}

// ── State Resolution ─────────────────────────────────────────────────
export function resolveNextState(
  current: BuildState,
  result: StageResult,
  recovery: RecoveryResult | null,
): BuildState {
  const allowed = STATE_TRANSITIONS[current] ?? [];

  // Happy path: pick the last successor (the "pass" branch)
  if (result.status === StageStatus.PASS || result.status === StageStatus.SKIP) {
    return allowed[allowed.length > 1 ? allowed.length - 1 : 0] ?? BuildState.FAIL;
  }
  if (!recovery || recovery.action === RecoveryAction.FAIL) return BuildState.FAIL;

  // Recovery-driven routing
  if (recovery.action === RecoveryAction.FIX_RTL) return BuildState.RTL_FIX;
  if (recovery.action === RecoveryAction.REGEN_SDC || recovery.action === RecoveryAction.RELAX_CLOCK)
    return BuildState.SDC_GEN;
  if (recovery.action === RecoveryAction.EXPAND_AREA || recovery.action === RecoveryAction.REDUCE_UTIL)
    return BuildState.HARDENING;
  if (recovery.action === RecoveryAction.RETRY_SAME) return current;

  return allowed[0] ?? BuildState.FAIL;
}

// ── Helpers ──────────────────────────────────────────────────────────
function emit(ctx: BuildContext, event: BuildEvent): void { ctx.event_sink?.(event); }
function isTerminal(s: BuildState): boolean { return s === BuildState.SUCCESS || s === BuildState.FAIL; }

// ── Single-Stage Runner ──────────────────────────────────────────────
export async function runSingleStage(state: BuildState, ctx: BuildContext): Promise<StageResult> {
  const handler = STAGE_REGISTRY[state];
  if (!handler) {
    return {
      stage: state, status: StageStatus.SKIP, producer: "orchestrator",
      failure_class: FailureClass.UNKNOWN, consumable_payload: {},
      diagnostics: [`No handler registered for state ${state} — skipping`],
      artifacts_written: [], next_action: "continue",
    };
  }
  ctx.state = state;
  return handler(ctx);
}

// ── Main Pipeline ────────────────────────────────────────────────────
export async function runPipeline(
  config: OrchestratorConfig,
): Promise<{ success: boolean; ctx: BuildContext; history: StageResult[] }> {
  const ctx = createBuildContext(config);
  const history: StageResult[] = [];

  ctx.state = BuildState.SPEC;
  ctx.state_history.push(BuildState.SPEC);
  emit(ctx, { type: "transition", state: BuildState.SPEC, message: "Pipeline started — entering SPEC" });

  while (!isTerminal(ctx.state)) {
    const current = ctx.state;
    const result = await runSingleStage(current, ctx);
    history.push(result);
    emit(ctx, { type: "log", state: current, message: `${current} → ${result.status}`, data: { failure_class: result.failure_class } });

    if (result.status === StageStatus.PASS || result.status === StageStatus.SKIP) {
      ctx.retry_count = 0;
      const next = resolveNextState(current, result, null);
      ctx.state = next;
      ctx.state_history.push(next);
      emit(ctx, { type: "transition", state: next, message: `Advancing ${current} → ${next}` });
      continue;
    }

    // Failure path
    const recovery = chooseRecovery(result, ctx);
    const next = resolveNextState(current, result, recovery);
    if (next === BuildState.FAIL) {
      ctx.state = BuildState.FAIL;
      ctx.state_history.push(BuildState.FAIL);
      emit(ctx, { type: "error", state: BuildState.FAIL, message: recovery.description });
      break;
    }
    ctx.retry_count += 1;
    ctx.state = next;
    ctx.state_history.push(next);
    emit(ctx, {
      type: "transition", state: next,
      message: `Recovery (${recovery.action}): ${current} → ${next} [retry ${ctx.retry_count}/${ctx.max_retries}]`,
    });
  }

  const success = ctx.state === BuildState.SUCCESS;
  emit(ctx, { type: success ? "success" : "error", state: ctx.state, message: success ? "Pipeline completed" : "Pipeline failed" });
  return { success, ctx, history };
}
