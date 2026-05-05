#!/usr/bin/env python3
"""
HuggingFace Space Demo for AgentIC VLSI-Aware MoE Model

Interactive demo: Natural language spec → PPA-optimized RTL generation.
Deploy on HuggingFace Spaces under the hackathon organization.

Files needed for HF Space:
- app.py (this file)
- requirements.txt
- README.md (Space description)

Usage (local):
    gradio app.py
"""

import json
import os
import tempfile
import time
from pathlib import Path

import gradio as gr
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_PATH = os.environ.get("MODEL_PATH", "agentIC-QwenMoE-50B-5A-vlsi-grpo")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

model = None
tokenizer = None


def load_model():
    global model, tokenizer
    try:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_PATH,
            trust_remote_code=True,
        )
        return True
    except Exception as e:
        print(f"Failed to load model: {e}")
        return False


def create_prompt(design_spec, ppa_priority):
    """Create a structured prompt for RTL generation."""
    system_prompt = (
        "You are an expert VLSI/RTL design engineer. Generate synthesizable, "
        "PPA-optimized Verilog RTL from natural language specifications.\n\n"
        f"Priority: {ppa_priority}\n\n"
        "Guidelines:\n"
        "- Use proper module declarations with parameters\n"
        "- Include synchronous reset (active-low rst_n) where applicable\n"
        "- Use non-blocking assignments (<=) in sequential logic\n"
        "- Use blocking assignments (=) in combinational logic\n"
        "- Add meaningful comments\n"
        "- Minimize gate count for area efficiency\n"
    )

    return f"{system_prompt}\n\nDesign Specification: {design_spec}\n\nGenerate the Verilog RTL:"


def generate_rtl(spec, ppa_priority, temperature, max_tokens):
    """Generate RTL from design specification."""
    if model is None or tokenizer is None:
        return "Error: Model not loaded. Please check MODEL_PATH environment variable."

    prompt = create_prompt(spec, ppa_priority)

    inputs = tokenizer(prompt, return_tensors="pt").to(DEVICE)

    start_time = time.time()
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            temperature=temperature,
            top_p=0.95,
            top_k=50,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    gen_time = time.time() - start_time

    generated_text = tokenizer.decode(outputs[0], skip_special_tokens=True)

    # Extract only the generated RTL (after the prompt)
    rtl_start = generated_text.find("Generate the Verilog RTL:")
    if rtl_start != -1:
        rtl_start += len("Generate the Verilog RTL:")
        rtl = generated_text[rtl_start:].strip()
    else:
        rtl = generated_text

    # Estimate PPA metrics
    ppa_estimate = estimate_ppa(rtl)

    return rtl, f"Generated in {gen_time:.2f}s | {ppa_estimate}"


def estimate_ppa(rtl):
    """Rough PPA estimation from RTL structure."""
    lines = [l for l in rtl.split("\n") if l.strip() and not l.strip().startswith("//")]
    regs = rtl.count("reg ")
    always = rtl.count("always @")
    params = rtl.count("parameter")

    area_score = max(0, 100 - len(lines) * 0.5 - regs * 2)
    power_score = max(0, 100 - regs * 3)
    perf_score = min(100, always * 15 + params * 5)

    return (
        f"Est. Area: {area_score:.0f}% | "
        f"Est. Power: {power_score:.0f}% | "
        f"Est. Perf: {perf_score:.0f}%"
    )


def build_demo():
    """Build Gradio demo interface."""
    with gr.Blocks(title="AgentIC: Sparse MoE VLSI Model", theme=gr.themes.Soft()) as demo:
        gr.Markdown("""
        # AgentIC: Sparse VLSI-Aware MoE Model
        ### AMD Developer Hackathon — Track 2: Fine-Tuning on AMD GPUs

        A **~50B parameter / ~5B active** sparse MoE model fine-tuned for **PPA-optimized RTL generation**.
        Trained on **AMD Instinct MI300X GPUs** via ROCm.

        Enter a hardware design specification below to generate Verilog RTL.
        """)

        with gr.Row():
            with gr.Column(scale=2):
                spec = gr.Textbox(
                    label="Design Specification",
                    placeholder="e.g., A 16-bit pipelined multiplier with active-low reset, "
                                "supporting signed multiplication and overflow detection",
                    lines=4,
                )
                ppa_priority = gr.Dropdown(
                    label="PPA Priority",
                    choices=["Balanced", "Area-Optimized", "Power-Optimized", "Performance-Optimized"],
                    value="Balanced",
                )
                temperature = gr.Slider(
                    label="Temperature",
                    minimum=0.1,
                    maximum=1.5,
                    value=0.7,
                    step=0.1,
                )
                max_tokens = gr.Slider(
                    label="Max Tokens",
                    minimum=256,
                    maximum=8192,
                    value=2048,
                    step=256,
                )
                generate_btn = gr.Button("Generate RTL", variant="primary")

            with gr.Column(scale=3):
                rtl_output = gr.Code(
                    label="Generated RTL",
                    language="verilog",
                    lines=20,
                )
                ppa_info = gr.Textbox(label="PPA Estimate", interactive=False)

        gr.Markdown("""
        ### Model Details
        - **Architecture:** Sparse MoE (8 routed experts + 1 shared expert per layer)
        - **Parameters:** ~50B total / ~5B active per token
        - **Base:** Qwen2.5-Coder-32B (upcycled via sparse upcycling)
        - **Training:** GRPO with composite PPA reward on AMD MI300X
        - **License:** Apache 2.0
        """)

        generate_btn.click(
            fn=generate_rtl,
            inputs=[spec, ppa_priority, temperature, max_tokens],
            outputs=[rtl_output, ppa_info],
        )

    return demo


if __name__ == "__main__":
    if DEVICE == "cuda":
        load_model()

    demo = build_demo()
    demo.launch(server_name="0.0.0.0", server_port=7860)
