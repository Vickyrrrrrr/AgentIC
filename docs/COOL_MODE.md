# 🔥 Cooling Down: How to Run Locally Safely

**No, your computer will not "blast".**
Modern CPUs and GPUs have safety sensors. If they get too hot (usually 100°C+), they will automatically slow down ("thermal throttle") or shut off the computer to prevent damage.

However, running a 5GB model like `deepseek-r1` pins your processor to 100% usage, which generates max heat.

## Solution: Use a "Lighter" Brain
Since you need to run strictly locally ($0 cost), the best way to reduce heat is to use a **smaller model**.

A 1.5B or 3B model requires **much less math** per second than a 7B/8B model. It will run faster and generate less heat.

### Recommended Models for Coding (Low Heat)
1.  **DeepSeek Coder 1.3B** (Tiny, very fast, decent at Verilog)
2.  **Qwen 2.5 Coder 3B** (Excellent balance of smarts and speed)

### How to Switch
1.  **Open Terminal and Pull a Tiny Model:**
    ```bash
    # Try the 1.3 Billion parameter version (approx 700MB - 1GB)
    ollama pull deepseek-coder:1.3b
    
    # OR try Qwen 3B (approx 2GB) - Better quality
    ollama pull qwen2.5-coder:3b
    ```

2.  **Update AgentIC to use it:**
    Open `src/agentic/config.py` and change `LLM_MODEL`:
    ```python
    # LLM_MODEL = "ollama/deepseek-r1"  <-- Comment this out (Heat: High)
    LLM_MODEL = "ollama/deepseek-coder:1.3b"  # <-- Use this (Heat: Low)
    ```

3.  **Physical Tips:**
    *   Prop up the back of your laptop for airflow.
    *   Use a cooling pad if you have one.
