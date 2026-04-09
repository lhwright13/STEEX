#!/usr/bin/env python3
"""Universal training worker for terminal-based platforms.

Works on Lightning AI, SageMaker Studio Lab, or any environment
with a GPU and Python. Pulls config from HF Hub, trains, pushes back.

Usage:
    pip install unsloth[colab-new] datasets huggingface_hub trl
    huggingface-cli login
    python worker_train.py --hub-repo username/steex-lfm2-market --dataset-repo username/steex-training-data
"""

import argparse
import json
import os
import time


def detect_platform():
    if os.environ.get("LIGHTNING_CLOUD_PROJECT_ID"):
        return "lightning"
    if os.path.exists("/home/studio-lab-user"):
        return "sagemaker"
    if os.environ.get("COLAB_GPU"):
        return "colab"
    if os.environ.get("KAGGLE_KERNEL_RUN_TYPE"):
        return "kaggle"
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="STEEX Training Worker")
    parser.add_argument("--hub-repo", required=True, help="HF Hub model repo")
    parser.add_argument("--dataset-repo", required=True, help="HF Hub dataset repo")
    parser.add_argument("--max-steps", type=int, default=0, help="Max steps (0=full epoch)")
    parser.add_argument("--epochs", type=int, default=1, help="Epochs to train")
    parser.add_argument("--lr", type=float, default=1e-4, help="Learning rate")
    parser.add_argument("--save-steps", type=int, default=25, help="Save every N steps")
    args = parser.parse_args()

    platform = detect_platform()
    print(f"Platform: {platform}")

    import torch
    if torch.cuda.is_available():
        gpu = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_mem / 1e9
        print(f"GPU: {gpu} ({vram:.1f} GB)")
        use_bf16 = "A100" in gpu or "A10" in gpu
    else:
        print("No CUDA GPU — using CPU (this will be very slow)")
        use_bf16 = False

    # Try to pull config from Hub
    try:
        from huggingface_hub import hf_hub_download
        config_path = hf_hub_download(repo_id=args.hub_repo, filename="pipeline_config.json")
        with open(config_path) as f:
            config = json.load(f)
        lr = config.get("learning_rate", args.lr)
        save_steps = config.get("save_steps", args.save_steps)
        print(f"Config loaded from Hub (lr={lr}, save_steps={save_steps})")
    except Exception:
        lr = args.lr
        save_steps = args.save_steps
        print(f"Using CLI config (lr={lr}, save_steps={save_steps})")

    # Load model
    from unsloth import FastLanguageModel

    resume = False
    try:
        from huggingface_hub import HfApi
        files = HfApi().list_repo_files(args.hub_repo)
        resume = any("adapter_config.json" in f for f in files)
    except Exception:
        pass

    if resume:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.hub_repo,
            max_seq_length=4096, dtype=None, load_in_4bit=True,
        )
        print(f"Resumed from {args.hub_repo}")
    else:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name="LiquidAI/LFM2.5-1.2B-Base",
            max_seq_length=4096, dtype=None, load_in_4bit=True,
        )
        model = FastLanguageModel.get_peft_model(
            model, r=16, lora_alpha=32, lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            bias="none", use_gradient_checkpointing="unsloth",
        )
        print("Fresh start from base model")

    # Load dataset
    from huggingface_hub import hf_hub_download as hf_dl
    dataset_path = hf_dl(repo_id=args.dataset_repo, filename="train.jsonl", repo_type="dataset")

    examples = []
    with open(dataset_path) as f:
        for line in f:
            examples.append(json.loads(line))

    from datasets import Dataset
    from unsloth.chat_templates import get_chat_template

    tokenizer = get_chat_template(tokenizer, chat_template="chatml")
    dataset = Dataset.from_list(examples)

    def format_example(example):
        text = tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False,
        )
        return {"text": text}

    dataset = dataset.map(format_example, remove_columns=dataset.column_names)
    print(f"Dataset: {len(dataset)} examples")

    # Train
    from trl import SFTTrainer
    from transformers import TrainingArguments

    train_args = {
        "output_dir": "./checkpoints",
        "per_device_train_batch_size": 2,
        "gradient_accumulation_steps": 8,
        "learning_rate": lr,
        "warmup_ratio": 0.05,
        "weight_decay": 0.01,
        "lr_scheduler_type": "cosine",
        "save_steps": save_steps,
        "logging_steps": 10,
        "save_total_limit": 3,
        "optim": "adamw_8bit",
        "seed": 42,
        "report_to": "none",
    }

    if args.max_steps > 0:
        train_args["max_steps"] = args.max_steps
    else:
        train_args["num_train_epochs"] = args.epochs

    if use_bf16:
        train_args["bf16"] = True
    else:
        train_args["fp16"] = True

    start_time = time.time()

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=TrainingArguments(**train_args),
        dataset_text_field="text",
        max_seq_length=4096,
        packing=True,
    )

    result = trainer.train()
    elapsed = time.time() - start_time

    # Push to Hub
    print("Pushing to Hub...")
    model.push_to_hub(args.hub_repo, tokenizer=tokenizer, private=True)

    # Update training state
    from huggingface_hub import HfApi
    state = {
        "platform": platform,
        "gpu": gpu if torch.cuda.is_available() else "cpu",
        "step": trainer.state.global_step,
        "final_loss": result.training_loss,
        "duration_minutes": elapsed / 60,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_active": False,
        "session_complete": True,
    }

    HfApi().upload_file(
        path_or_fileobj=json.dumps(state, indent=2).encode(),
        path_in_repo="training_state.json",
        repo_id=args.hub_repo,
        commit_message=f"{platform}: {trainer.state.global_step} steps, loss={result.training_loss:.4f}",
    )

    print(f"\nDone: {trainer.state.global_step} steps, loss={result.training_loss:.4f}, {elapsed/60:.1f}min")
    print(f"Pushed to {args.hub_repo}")


if __name__ == "__main__":
    main()
