#!/usr/bin/env python3
"""
train_lora.py — LoRA fine-tune for VLSI formal verification.
Optimized for MI300X (192GB VRAM) with DeepSpeed ZeRO-3 offload.

Usage:
    source /root/vlsi-env/bin/activate
    python3 train_lora.py \
        --model_name_or_path vxkyyy/vlsi-moe-ffn-merged \
        --train_file sva_train.jsonl \
        --output_dir ./vlsi-formal-lora
"""

import os
import sys
import json
import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import Dataset


def load_jsonl(path: str):
    """Load chat-format JSONL into HF Dataset."""
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            # Flatten chat format to text
            text = ""
            for msg in item["messages"]:
                text += f"<|im_start|>{msg['role']}\n{msg['content']}\n<|im_end|>\n"
            data.append({"text": text})
    return Dataset.from_list(data)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_name_or_path", required=True)
    parser.add_argument("--train_file", default="sva_train.jsonl")
    parser.add_argument("--output_dir", default="./vlsi-formal-lora")
    parser.add_argument("--batch_size", type=int, default=1)
    parser.add_argument("--gradient_accumulation_steps", type=int, default=4)
    parser.add_argument("--num_train_epochs", type=int, default=3)
    parser.add_argument("--learning_rate", type=float, default=1e-4)
    parser.add_argument("--lora_r", type=int, default=64)
    parser.add_argument("--lora_alpha", type=int, default=128)
    parser.add_argument("--max_seq_length", type=int, default=2048)
    args = parser.parse_args()

    print(f"[+] Loading model: {args.model_name_or_path}")

    # Load tokenizer (use your patched local copy)
    tokenizer = AutoTokenizer.from_pretrained(
        "/root/vlsi-model",
        trust_remote_code=True,
        use_fast=True,
        padding_side="right",
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load model in bfloat16 with device_map
    model = AutoModelForCausalLM.from_pretrained(
        args.model_name_or_path,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )

    print(f"[+] Model loaded on: {model.device}")

    # LoRA config — target attention + FFN layers
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Load dataset
    print(f"[+] Loading dataset: {args.train_file}")
    dataset = load_jsonl(args.train_file)

    def tokenize(examples):
        return tokenizer(
            examples["text"],
            truncation=True,
            max_length=args.max_seq_length,
            padding="max_length",
        )

    dataset = dataset.map(tokenize, batched=True, remove_columns=["text"])

    # Training args
    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.num_train_epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        bf16=True,
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        optim="adamw_torch",
        report_to="none",  # no wandb
        dataloader_num_workers=2,
        remove_unused_columns=False,
    )

    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer,
        mlm=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        data_collator=data_collator,
    )

    print("[+] Starting training …")
    trainer.train()

    print(f"[+] Saving LoRA adapters to {args.output_dir}")
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)

    print("[+] Done.")
    print(f"\nTo merge and use:")
    print(f"  python3 merge_lora.py --base {args.model_name_or_path} --lora {args.output_dir} --output ./vlsi-formal-merged")


if __name__ == "__main__":
    main()
