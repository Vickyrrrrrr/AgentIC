# AgentIC — IP Safety, Data Privacy & Expansion Plan

## 1. Is Your IP Secure?

**Short answer: Yes, by design — with caveats to understand.**

Your chip designs (RTL, specs, build prompts) pass through external LLM APIs during the build. Here is precisely what leaves the system and what never does.

---

## 2. What Data Leaves AgentIC

| Data | Where it goes | Can you control it? |
|---|---|---|
| Design description + RTL prompts | NVIDIA NIM API (`integrate.api.nvidia.com`) | Yes — route to local Ollama instead |
| Spec + RTL during agent reasoning | Same NVIDIA endpoint | Yes — BYOK plan lets you use your own hosted API |
| Build logs | Stays on server only | N/A — never sent externally |
| VCD waveforms | Stays on server only | N/A |
| GDS layout files | Stays on server only | N/A |
| Training JSONL export | Stays on local disk (`training/agentic_sft_data.jsonl`) | N/A — never uploaded |
| API keys | HuggingFace Space Secrets (encrypted at rest by HF) | Yes — rotate at any time |
| User profiles + build counts | Supabase (only when auth is enabled) | Yes — opt-in, use your own Supabase project |

**Nothing is ever sold, shared, or used to train third-party models.** The training JSONL is written locally on your machine or HF Space persistent storage — it stays yours.

---

## 3. The LLM API Risk — And How to Eliminate It

When a user submits `"Design a 32-bit RISC-V processor with branch prediction"`, that prompt goes to NVIDIA's inference endpoint. NVIDIA's [API Terms of Service](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-api-trial-terms-of-service/) state they do not use API inputs to train their models.

**If your design is confidential, use the BYOK plan:**
- Set `plan = byok` for your account in Supabase
- Store your own API key (pointing to a **self-hosted** vLLM/Ollama endpoint)
- The build runs entirely on-premises — nothing leaves your network

**Self-hosted LLM options (zero data egress):**
```
# Ollama — local GPU inference
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=ollama/llama3.3:70b

# vLLM on your own server
LLM_BASE_URL=http://YOUR_SERVER:8000/v1
LLM_MODEL=meta-llama/Llama-3.3-70B-Instruct
```

---

## 4. What Stays Completely Private

These never leave the system under any configuration:

- **EDA tool execution** — `iverilog`, `verilator`, `yosys`, `sby` run 100% locally
- **VCD simulation waveforms** — generated and stored locally
- **GDS chip layouts** — OpenLane output, stays on disk
- **Training JSONL** — local fine-tuning data, never uploaded
- **Build logs** — streamed to your browser via SSE, never stored externally
- **Supabase data** — your own Supabase project, your data

---

## 5. Expansion Plan

### Phase 1 — Platform Foundation (Current)
- [x] Multi-agent RTL build pipeline (RTL → Verification → Formal → Coverage → GDSII)
- [x] Human-in-the-loop approval at each stage
- [x] Supabase auth + plan tiers (Free / Starter / Pro / BYOK)
- [x] Razorpay billing with webhook verification
- [x] HuggingFace Spaces deployment (Docker)
- [x] Training data export pipeline (local JSONL)

### Phase 2 — Scale & Monetize (Q1 2026)
- [ ] **Frontend auth UI** — login/signup pages using Supabase Auth JS SDK
- [ ] **Pricing page** — Razorpay checkout integration in React
- [ ] **User dashboard** — build history, plan status, upgrade prompts
- [ ] **BYOK key management UI** — set/update encrypted API key from browser
- [ ] **Team accounts** — shared plan, shared build quota

### Phase 3 — IP Hardening (Q2 2026)
- [ ] **On-premise mode** — single Docker Compose stack with bundled local LLM (Ollama)
- [ ] **Air-gapped deployment guide** — no internet required, all EDA tools + LLM in one stack
- [ ] **Design vault** — encrypted storage for completed RTL/GDS with per-user S3-compatible bucket
- [ ] **Differential privacy** on training export — strip user identifiers from JSONL before fine-tuning
- [ ] **Audit log** — every API call that contains design data is logged with timestamp + user

### Phase 4 — Enterprise (Q3 2026)
- [ ] **SSO** — SAML/OIDC via Supabase (works with Google Workspace, Okta)
- [ ] **NDA-grade deployment** — dedicated HF Space per enterprise tenant with isolated secrets
- [ ] **Custom PDK support** — bring your own standard cell library without submitting it to any cloud
- [ ] **Multi-project wafer slot reservation** — integration with Efabless / Skywater shuttle APIs
- [ ] **SLA agreement** — 99.9% uptime on HF Pro+ hardware (A10G GPU)

---

## 6. Security Architecture Summary

```
User Browser
     │
     │  HTTPS (TLS 1.3)
     ▼
HuggingFace Space (Docker container)
     │
     ├── FastAPI (server/api.py)
     │       ├── Supabase JWT verification  ← user never sees DB directly
     │       ├── Plan guard (402 on limit)
     │       └── BYOK key decrypt          ← key never logged
     │
     ├── LLM call  ──────────────────────► NVIDIA NIM API (or your private endpoint)
     │       └── Design prompt goes here   ← only this crosses the boundary
     │
     ├── EDA tools (iverilog, yosys, sby)  ← 100% local, no network calls
     │
     └── Build artifacts → local disk
             training/agentic_sft_data.jsonl  ← yours only

Supabase (your project)
     ├── profiles (plan, build count, encrypted BYOK key)
     ├── builds (job history)
     └── payments (Razorpay records)
```

---

## 7. Secrets You Control

| Secret | Stored where | How to rotate |
|---|---|---|
| `NVIDIA_API_KEY` | HF Space Secrets + local `.env` | NVIDIA dashboard → regenerate |
| `SUPABASE_SERVICE_KEY` | HF Space Secrets only | Supabase → Settings → API |
| `ENCRYPTION_KEY` | HF Space Secrets only | Change + re-encrypt stored BYOK keys |
| `RAZORPAY_KEY_SECRET` | HF Space Secrets only | Razorpay dashboard |
| `HF_TOKEN` | GitHub Actions Secrets | HuggingFace → Settings → Tokens |

**Rotate all keys immediately if:**
- A key appears in any public log, PR, or error message
- You suspect unauthorized use
- Any team member with access leaves

To update HF Space secrets programmatically:
```python
from huggingface_hub import HfApi
api = HfApi(token="your_new_hf_token")
api.add_space_secret("vxkyyy/AgentIC", "NVIDIA_API_KEY", "new_value")
```
