import argparse
import uvicorn
import os
from vllm.entrypoints.openai.api_server import app
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.entrypoints.openai.serving_chat import OpenAIServingChat
from vllm.entrypoints.openai.serving_completion import OpenAIServingCompletion

# Note: This is a simplified launcher. 
# vLLM is best launched directly via module: python -m vllm.entrypoints.openai.api_server
# But this script provides a friendly wrapper for the user.

def main():
    parser = argparse.ArgumentParser(description="AgentIC Remote LLM Server Launcher")
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-Coder-32B-Instruct", help="HuggingFace model ID to load")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9, help="Fraction of GPU memory to use")
    
    args = parser.parse_args()

    print(f"🚀 Starting AgentIC LLM Server...")
    print(f"    Model: {args.model}")
    print(f"    Host:  {args.host}")
    print(f"    Port:  {args.port}")
    print(f"\nCommand to reproduce with raw vllm:\npython -m vllm.entrypoints.openai.api_server --model {args.model} --host {args.host} --port {args.port} --gpu-memory-utilization {args.gpu_memory-utilization}")
    
    # We will just shell out to the standard vllm module command because it's more robust
    # than trying to wire up the internal FastAPI app manually in a script that might drift.
    
    cmd = f"python3 -m vllm.entrypoints.openai.api_server --model {args.model} --host {args.host} --port {args.port} --gpu-memory-utilization {args.gpu_memory_utilization} --trust-remote-code"
    
    print(f"\n⚡ Executing: {cmd}")
    os.system(cmd)

if __name__ == "__main__":
    main()
