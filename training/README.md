# VeriReason Training Tools for AgentIC

Train VeriReason to generate better Verilog using data from AgentIC builds.

## Complete Workflow

### Step 1: Run Builds with Cloud LLM
```bash
cd ~/AgentIC && source .venv-agentic/bin/activate

python main.py build -n "counter"    -d "8-bit counter with enable" --skip-openlane
python main.py build -n "uart_tx"    -d "UART transmitter 115200 baud" --skip-openlane
python main.py build -n "spi_master" -d "SPI master with CPOL/CPHA" --skip-openlane
python main.py build -n "fifo"       -d "sync FIFO depth 16, 8-bit data" --skip-openlane
python main.py build -n "pwm"        -d "PWM controller 8-bit duty cycle" --skip-openlane
```
Even failed builds are valuable — they produce error→fix training pairs.

### Step 2: Collect Training Data
```bash
python3 training/collect_training_data.py
# Output: training/agentic_sft_data.jsonl
```

### Step 3: Generate Log-Based Reasoning (Recommended)
```bash
ollama serve  # in another terminal
python3 training/generate_reasoning.py
# Output: training/agentic_sft_data_with_reasoning.jsonl
```

VeriReason **reads the actual build logs** and generates chain-of-thought reasoning about what happened:

```
Build log says:
  ERROR: MULTIDRIVEN on cnt (two always blocks)
  FIX: Merged into single always_ff with async reset
  SIM: Timing race detected, CLASS=D

VeriReason generates:
  <think>
  The initial RTL had two always blocks driving cnt — one for
  incrementing and one for reset. This is a MULTIDRIVEN violation.
  The correct approach is a single always_ff with async reset in
  the sensitivity list. The sim timing race happened because the
  TB released reset on the same clock edge...
  </think>
  module counter(...) // cloud's verified code
```

### Step 4: Fine-Tune VeriReason
```bash
pip install llamafactory
llamafactory-cli train training/agentic_sft_config.yaml
```
- **GPU**: 24GB+ VRAM (RTX 3090/4090/A100)
- **Time**: ~4-8 hrs (3B) or ~8-12 hrs (7B)
- **Output**: `training/checkpoints/agentic-sft/` (LoRA weights, ~200MB)

### Step 5: Deploy Fine-Tuned Model
```bash
# Merge LoRA into base model
llamafactory-cli export \
  --model_name_or_path Nellyw888/VeriReason-Qwen2.5-7b-SFT-Reasoning \
  --adapter_name_or_path training/checkpoints/agentic-sft \
  --export_dir training/merged-model --template qwen

# Import into Ollama
cat > training/Modelfile << 'EOF'
FROM training/merged-model
PARAMETER temperature 0.2
PARAMETER num_ctx 4096
SYSTEM You are a Verilog RTL expert. Generate synthesizable SystemVerilog.
EOF
ollama create verireason-agentic -f training/Modelfile

# Use with AgentIC
export LLM_MODEL="ollama/verireason-agentic"
export LLM_BASE_URL="http://localhost:11434"
python main.py build -n "my_chip" -d "your design" --skip-openlane
```

## Files

| File | Purpose |
|------|---------|
| `collect_training_data.py` | Extracts SFT pairs from build logs |
| `generate_reasoning.py` | VeriReason reads build logs → generates CoT reasoning |
| `agentic_sft_config.yaml` | LLamaFactory LoRA fine-tuning config |
| `verilog_rewards_enhanced.py` | GRPO reward function (6 signals) |

## Self-Improving Loop

```
Cloud builds → collect data → VeriReason reads logs → generates reasoning
→ fine-tune VeriReason → better local code → more builds → repeat
```
