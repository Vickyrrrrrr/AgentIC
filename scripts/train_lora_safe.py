#!/usr/bin/env python3
"""
train_lora_safe.py — Safe LoRA training for 33B on MI300X.
Uses gradient checkpointing + smaller effective batch to avoid OOM.
"""

import os
import json
import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)
from peft import LoraConfig, get_peft_model, TaskType
from datasets import Dataset

def load_jsonl(path):
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            item = json.loads(line)
            text = ""
            for msg in item["messages"]:
                text += f"<|im_start|>{msg['role']}\n{msg['content']}\n<|im_end|>\n"
            data.append({"text": text})
    return Dataset.from_list(data)

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--model_name_or_path", default="vxkyyy/vlsi-moe-ffn-merged")
parser.add_argument("--train_file", default="sva_train.jsonl")
parser.add_argument("--output_dir", default="./vlsi-formal-lora-safe")
args = parser.parse_args()

print(f"[+] Loading 33B model: {args.model_name_or_path}")
tokenizer = AutoTokenizer.from_pretrained("/root/vlsi-model", trust_remote_code=True, use_fast=True, padding_side="right")
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    args.model_name_or_path,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True,
)
print(f"[+] Model loaded on: {model.device}")

# Enable gradient checkpointing to save VRAM
model.gradient_checkpointing_enable()
print("[+] Gradient checkpointing enabled")

lora_config = LoraConfig(
    r=128,                    # Reduced from 256
    lora_alpha=256,           # 2x rank
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    lora_dropout=0.0,
    bias="none",
    task_type=TaskType.CAUSAL_LM,
    init_lora_weights="gaussian",
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

dataset = load_jsonl(args.train_file)

def tokenize(examples):
    return tokenizer(examples["text"], truncation=True, max_length=1024, padding="max_length")  # Reduced from 2048

dataset = dataset.map(tokenize, batched=True, remove_columns=["text"])

training_args = TrainingArguments(
    output_dir=args.output_dir,
    num_train_epochs=10,
    per_device_train_batch_size=1,          # Reduced from 2
    gradient_accumulation_steps=8,          # Effective batch = 8
    learning_rate=5e-5,
    warmup_steps=50,
    lr_scheduler_type="cosine",
    bf16=True,
    logging_steps=5,
    save_strategy="epoch",
    save_total_limit=2,
    optim="adamw_torch",
    weight_decay=0.01,
    max_grad_norm=0.3,
    report_to="none",
    dataloader_num_workers=2,
    remove_unused_columns=False,
    # Memory safety
    dataloader_pin_memory=False,
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=dataset,
    data_collator=DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
)

print("[+] Starting SAFE LoRA training (10 epochs)...")
trainer.train()
print(f"[+] Saving to {args.output_dir}")
model.save_pretrained(args.output_dir)
tokenizer.save_pretrained(args.output_dir)
print("[+] Done.")
