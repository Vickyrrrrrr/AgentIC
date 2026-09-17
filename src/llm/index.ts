/**
 * LLM client wrapper — thin abstraction over OpenAI-compatible endpoints.
 *
 * Supports any provider: OpenAI, Anthropic (via proxy), Groq, Ollama, vLLM, etc.
 * Agents call `callLlm()` to get raw text, or `callLlmJson()` for parsed JSON.
 */

import OpenAI from "openai";
import type { LlmConfig } from "../types/index.js";

export interface LlmMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface LlmResponse {
  content: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  model: string;
  finish_reason: string;
}

export class LlmClient {
  private client: OpenAI;
  private model: string;
  private temperature: number;
  private maxTokens: number;

  constructor(config: LlmConfig) {
    this.client = new OpenAI({
      apiKey: config.api_key || "dummy",
      baseURL: config.base_url || "https://api.openai.com/v1",
    });
    this.model = stripProviderPrefix(config.model || "gpt-4o");
    this.temperature = config.temperature ?? 0.2;
    this.maxTokens = config.max_tokens ?? 8192;
  }

  async call(
    messages: LlmMessage[],
    opts?: { temperature?: number; maxTokens?: number },
  ): Promise<LlmResponse> {
    const response = await this.client.chat.completions.create({
      model: this.model,
      messages: messages.map((m) => ({ role: m.role, content: m.content })),
      temperature: opts?.temperature ?? this.temperature,
      max_tokens: opts?.maxTokens ?? this.maxTokens,
    });

    const choice = response.choices[0];
    return {
      content: choice.message.content ?? "",
      usage: {
        prompt_tokens: response.usage?.prompt_tokens ?? 0,
        completion_tokens: response.usage?.completion_tokens ?? 0,
        total_tokens: response.usage?.total_tokens ?? 0,
      },
      model: response.model,
      finish_reason: choice.finish_reason ?? "stop",
    };
  }

  async callText(
    systemPrompt: string,
    userPrompt: string,
    opts?: { temperature?: number; maxTokens?: number },
  ): Promise<string> {
    const resp = await this.call(
      [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt },
      ],
      opts,
    );
    return resp.content;
  }

  async callJson<T = Record<string, unknown>>(
    systemPrompt: string,
    userPrompt: string,
    opts?: { temperature?: number; maxTokens?: number },
  ): Promise<{ data: T | null; raw: string }> {
    const raw = await this.callText(systemPrompt, userPrompt, opts);
    const data = extractJson<T>(raw);
    return { data, raw };
  }
}

// ── Helpers ───────────────────────────────────────────────────────────

function stripProviderPrefix(model: string): string {
  const providers = [
    "openai",
    "anthropic",
    "generic",
    "openrouter",
    "together_ai",
    "deepseek",
    "ollama",
    "infinity",
  ];
  if (model.includes("/")) {
    const prefix = model.split("/")[0];
    if (providers.includes(prefix)) {
      return model.split("/").slice(1).join("/");
    }
  }
  return model;
}

export function extractJson<T = Record<string, unknown>>(raw: string): T | null {
  if (!raw || !raw.trim()) return null;

  const text = raw.trim();

  // Strategy 1: Markdown fenced JSON
  const fenceMatch = text.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
  if (fenceMatch) {
    try {
      return JSON.parse(fenceMatch[1]) as T;
    } catch {
      // continue
    }
  }

  // Strategy 2: Direct parse
  if (text.startsWith("{") && text.endsWith("}")) {
    try {
      return JSON.parse(text) as T;
    } catch {
      // continue
    }
  }

  // Strategy 3: Brace depth extraction
  let depth = 0;
  let startIdx = -1;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "{" && depth === 0) {
      startIdx = i;
      depth = 1;
    } else if (text[i] === "{") {
      depth++;
    } else if (text[i] === "}") {
      depth--;
      if (depth === 0 && startIdx >= 0) {
        const candidate = text.slice(startIdx, i + 1);
        try {
          return JSON.parse(candidate) as T;
        } catch {
          // continue
        }
      }
    }
  }

  return null;
}

export function inferFailureClass(rawOutput: string): string {
  const text = rawOutput.toLowerCase();
  if (text.includes("syntax error") || text.includes("drc error") || text.includes("yosys") || text.includes("verilator")) {
    return "EDA_TOOL_ERROR";
  }
  if (text.includes("not valid json") || text.includes("missing required key") || text.includes("prose")) {
    return "LLM_FORMAT_ERROR";
  }
  if (text.includes("timed out") || text.includes("binary not found") || text.includes("no logic found")) {
    return "INFRASTRUCTURE_ERROR";
  }
  if (text.includes("handoff") || text.includes("missing artifact")) {
    return "ORCHESTRATOR_ROUTING_ERROR";
  }
  if (text.includes("retry") && text.includes("budget")) {
    return "RETRY_BUDGET_ERROR";
  }
  return "UNKNOWN";
}
