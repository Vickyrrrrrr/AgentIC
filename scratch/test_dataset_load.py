from datasets import load_dataset
import os

try:
    ds = load_dataset("dakies/nvlabs-verilogeval", split="test", token=os.environ.get("HF_TOKEN", True))
    print(f"Success: Loaded {len(ds)} tasks")
except Exception as e:
    print(f"Error: {e}")
