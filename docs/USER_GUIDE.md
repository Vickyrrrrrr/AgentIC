# AgentIC User Guide

This guide provides detailed instructions on how to switch between LLM backends and use the various commands available in AgentIC.

## 1. Switching LLM Backends

AgentIC operates with a **Strict Two-Model Policy**:
- **Primary**: NVIDIA Qwen Cloud (High Performance)
- **Fallback**: VeriReason Local (Privacy/Offline)

### To Use NVIDIA Cloud (Recommended)
Ensure your `NVIDIA_API_KEY` is set in the `.env` file or your environment.

**`.env` file:**
```bash
NVIDIA_API_KEY="nvapi-..." 
```

The system will automatically prioritize this if the key is valid.

### To Use VeriReason (Local/Offline)
To force the system to use the local VeriReason model, simply **unset** or **remove** the `NVIDIA_API_KEY`.

**Option A: Temporarily (Command Line)**
```bash
# Run with empty key to force fallback
NVIDIA_API_KEY="" python main.py build --name my_chip --desc "..."
```

**Option B: Permanently (`.env`)**
Comment out the key in your `.env` file:
```bash
# NVIDIA_API_KEY="nvapi-..."
```

When no NVIDIA key is found, AgentIC safely falls back to the local `VeriReason` model configured at `http://localhost:11434`.

---

## 2. Command Reference

All commands are run via `python main.py [COMMAND] [OPTIONS]`.

### `build`
Automates the entire chip design pipeline: RTL → Verification → Hardening.

```bash
python main.py build --name [NAME] --desc "[DESCRIPTION]" [OPTIONS]
```

**Options:**
- `--name, -n`: Name of the module/project (Required).
- `--desc, -d`: Natural language description of functionality (Required).
- `--skip-openlane`: specificy this flag to stop after verification (skips GDSII hardening).
- `--full-signoff`: Enable rigorous verification (Formal + Coverage + Regression).
- `--max-retries`: Max attempts to auto-fix errors (Default: 5).
- `--show-thinking`: Show LLM reasoning process in console.

**Example:**
```bash
python main.py build -n spi_master -d "SPI Master with configurable CPOL" --full-signoff
```

### `simulate`
Runs simulation on an existing design, with AI-powered auto-fixing for testbench or RTL errors.

```bash
python main.py simulate --name [NAME]
```

**Options:**
- `--max-retries`: Max auto-fix attempts (Default: 5).
- `--show-thinking`: Show detailed AI analysis of simulation logs.

### `harden`
Runs the OpenLane flow to turn verified RTL into a GDSII layout file.

```bash
python main.py harden --name [NAME]
```

**Features:**
- Auto-generates `config.tcl` compliant with OpenLane 2.0.
- Runs in background or foreground.
- Performs final signoff checks (STA/Power) upon completion.

### `verify`
Runs the standalone verification suite (Lint + Simulation + Formal).

```bash
python main.py verify [NAME]
```



---

## 3. Configuration Notes

- **PDK Root**: Default `~/.ciel`. Change via `PDK_ROOT` env var.
- **OpenLane Root**: Default `~/OpenLane`. Change via `OPENLANE_ROOT` env var.
- **Local Model**: Defaults to `VeriReason-Qwen2.5-3b`. Update `LLM_MODEL` in `.env` if you have a custom fine-tune.
