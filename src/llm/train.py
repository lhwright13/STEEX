"""Unified LFM2.5 fine-tuning script with HF Hub checkpoint relay.

Works across Colab, Kaggle, Lightning AI, and Modal.
Saves/resumes checkpoints via Hugging Face Hub for cross-platform training.

Usage:
    python -m src.llm.train --dataset data/llm/train.jsonl --epochs 3
    python -m src.llm.train --resume  # Resume from last HF Hub checkpoint
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    # Model
    "model_name": "LiquidAI/LFM2.5-1.2B-Base",
    "max_seq_length": 4096,
    "dtype": None,  # auto-detect

    # LoRA
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "lora_target_modules": [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],

    # Training
    "epochs": 3,
    "batch_size": 2,
    "gradient_accumulation_steps": 8,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.1,
    "weight_decay": 0.01,
    "max_grad_norm": 1.0,
    "lr_scheduler_type": "cosine",
    "save_steps": 50,
    "logging_steps": 10,

    # Hub
    "hub_repo": None,  # Set to "username/steex-lfm2-market" to enable
    "hub_private": True,
    "checkpoint_dir": "checkpoints",
}


def detect_platform() -> str:
    """Detect which free GPU platform we're running on."""
    if os.environ.get("COLAB_GPU") or os.path.exists("/content"):
        return "colab"
    elif os.environ.get("KAGGLE_KERNEL_RUN_TYPE"):
        return "kaggle"
    elif os.environ.get("LIGHTNING_CLOUD_PROJECT_ID"):
        return "lightning"
    elif os.environ.get("MODAL_ENVIRONMENT"):
        return "modal"
    else:
        return "local"


def get_gpu_info() -> dict:
    """Get GPU information for logging."""
    info = {"platform": detect_platform(), "gpu": "none", "vram_gb": 0}
    try:
        import torch
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_gb"] = torch.cuda.get_device_properties(0).total_mem / 1e9
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["gpu"] = "Apple MPS"
            info["vram_gb"] = 16  # Approximate for unified memory
    except ImportError:
        pass
    return info


def install_dependencies(platform: str) -> None:
    """Install required packages for the detected platform."""
    import subprocess

    base_packages = [
        "unsloth",
        "datasets",
        "huggingface_hub",
        "trl",
    ]

    # Unsloth handles torch/transformers/peft installation
    logger.info(f"Installing dependencies for {platform}...")

    if platform in ("colab", "kaggle"):
        # Use pip with --quiet for notebook environments
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet",
            "unsloth[colab-new]",
            "datasets",
            "huggingface_hub",
        ])
    elif platform == "lightning":
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet",
            *base_packages,
        ])
    else:
        logger.info("Local environment — install manually: pip install unsloth datasets huggingface_hub trl")


def load_dataset(path: str) -> "Dataset":
    """Load training data from JSONL or HF datasets format."""
    from datasets import Dataset, load_from_disk

    path = Path(path)

    if path.is_dir():
        logger.info(f"Loading HF dataset from {path}")
        return load_from_disk(str(path))
    elif path.suffix == ".jsonl":
        logger.info(f"Loading JSONL from {path}")
        examples = []
        with open(path) as f:
            for line in f:
                examples.append(json.loads(line))
        return Dataset.from_list(examples)
    else:
        raise ValueError(f"Unsupported dataset format: {path}")


def find_latest_checkpoint(config: dict) -> Optional[str]:
    """Find the latest checkpoint, checking HF Hub first, then local."""
    hub_repo = config.get("hub_repo")

    # Check HF Hub for remote checkpoint
    if hub_repo:
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            files = api.list_repo_files(hub_repo)
            # Look for checkpoint directories
            checkpoints = sorted(
                [f for f in files if "checkpoint-" in f],
                key=lambda x: int(x.split("checkpoint-")[1].split("/")[0]),
            )
            if checkpoints:
                latest = checkpoints[-1].split("/")[0]
                logger.info(f"Found remote checkpoint: {latest}")
                return f"hub:{hub_repo}/{latest}"
        except Exception as e:
            logger.debug(f"No remote checkpoint found: {e}")

    # Check local checkpoints
    ckpt_dir = Path(config["checkpoint_dir"])
    if ckpt_dir.exists():
        checkpoints = sorted(ckpt_dir.glob("checkpoint-*"), key=lambda p: p.stat().st_mtime)
        if checkpoints:
            latest = checkpoints[-1]
            logger.info(f"Found local checkpoint: {latest}")
            return str(latest)

    return None


class CrashSafeCallback:
    """Trainer callback that pushes checkpoints to Hub for crash resilience.

    Pushes to HF Hub every `push_every_n_saves` local saves, and always
    updates training_state.json so the pipeline controller can track progress.
    """

    def __init__(self, hub_repo: str, push_every_n_saves: int = 4):
        self.hub_repo = hub_repo
        self.push_every = push_every_n_saves
        self.save_count = 0

    def on_save(self, args, state, control, **kwargs):
        self.save_count += 1

        # Always update lightweight state file
        try:
            from huggingface_hub import HfApi
            import json
            training_state = {
                "step": state.global_step,
                "final_loss": state.log_history[-1].get("loss", 0) if state.log_history else 0,
                "platform": detect_platform(),
                "session_active": True,
                "session_complete": False,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            HfApi().upload_file(
                path_or_fileobj=json.dumps(training_state, indent=2).encode(),
                path_in_repo="training_state.json",
                repo_id=self.hub_repo,
                commit_message=f"Step {state.global_step}",
            )
        except Exception as e:
            logger.debug(f"State update failed: {e}")

        # Push full checkpoint less frequently
        if self.save_count % self.push_every == 0:
            push_checkpoint_to_hub(args.output_dir, {"hub_repo": self.hub_repo}, state.global_step)


def push_checkpoint_to_hub(checkpoint_path: str, config: dict, step: int) -> None:
    """Push a checkpoint to HF Hub for cross-platform relay."""
    hub_repo = config.get("hub_repo")
    if not hub_repo:
        return

    try:
        from huggingface_hub import HfApi
        api = HfApi()

        # Create repo if it doesn't exist
        api.create_repo(hub_repo, private=config.get("hub_private", True), exist_ok=True)

        # Upload checkpoint
        api.upload_folder(
            folder_path=checkpoint_path,
            repo_id=hub_repo,
            path_in_repo=f"checkpoint-{step}",
            commit_message=f"Checkpoint at step {step} from {detect_platform()}",
        )

        # Upload training state
        state = {
            "step": step,
            "platform": detect_platform(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "gpu": get_gpu_info(),
        }
        api.upload_file(
            path_or_fileobj=json.dumps(state, indent=2).encode(),
            path_in_repo="training_state.json",
            repo_id=hub_repo,
            commit_message=f"Training state at step {step}",
        )

        logger.info(f"Pushed checkpoint-{step} to {hub_repo}")
    except Exception as e:
        logger.error(f"Failed to push checkpoint: {e}")


def train(dataset_path: str, config: Optional[dict] = None, resume: bool = False) -> str:
    """Run LoRA fine-tuning on LFM2.5-1.2B-Base.

    Args:
        dataset_path: Path to JSONL or HF dataset directory
        config: Training configuration (merges with DEFAULT_CONFIG)
        resume: Whether to resume from the latest checkpoint

    Returns:
        Path to the final model output directory
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    platform = detect_platform()
    gpu_info = get_gpu_info()

    logger.info(f"Platform: {platform}")
    logger.info(f"GPU: {gpu_info['gpu']} ({gpu_info['vram_gb']:.1f} GB)")
    logger.info(f"Model: {cfg['model_name']}")

    # Load model with Unsloth (2x faster, 50% less memory)
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["model_name"],
        max_seq_length=cfg["max_seq_length"],
        dtype=cfg["dtype"],
        load_in_4bit=True,  # Crucial for fitting on T4 16GB
    )

    # Apply LoRA
    model = FastLanguageModel.get_peft_model(
        model,
        r=cfg["lora_r"],
        lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg["lora_dropout"],
        target_modules=cfg["lora_target_modules"],
        bias="none",
        use_gradient_checkpointing="unsloth",  # 30% more memory efficient
    )

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    logger.info(f"Trainable params: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    # Load dataset
    dataset = load_dataset(dataset_path)
    logger.info(f"Dataset: {len(dataset)} examples")

    # Format for chat template
    from unsloth.chat_templates import get_chat_template
    tokenizer = get_chat_template(tokenizer, chat_template="chatml")

    def format_example(example):
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    dataset = dataset.map(format_example, remove_columns=dataset.column_names)

    # Check for resume
    resume_from = None
    if resume:
        resume_from = find_latest_checkpoint(cfg)
        if resume_from:
            logger.info(f"Resuming from: {resume_from}")
        else:
            logger.info("No checkpoint found, starting fresh")

    # Training
    from trl import SFTTrainer
    from transformers import TrainingArguments

    output_dir = Path(cfg["checkpoint_dir"]) / "steex-lfm2-market"
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=cfg["epochs"],
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=cfg["gradient_accumulation_steps"],
        learning_rate=cfg["learning_rate"],
        warmup_ratio=cfg["warmup_ratio"],
        weight_decay=cfg["weight_decay"],
        max_grad_norm=cfg["max_grad_norm"],
        lr_scheduler_type=cfg["lr_scheduler_type"],
        save_steps=cfg["save_steps"],
        logging_steps=cfg["logging_steps"],
        save_total_limit=3,
        fp16=not gpu_info["gpu"].startswith("Apple"),
        bf16=False,
        optim="adamw_8bit",
        seed=42,
        report_to="none",
        # Resume support
        resume_from_checkpoint=resume_from if resume_from and not resume_from.startswith("hub:") else None,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=training_args,
        dataset_text_field="text",
        max_seq_length=cfg["max_seq_length"],
        packing=True,  # Pack multiple examples per sequence for efficiency
    )

    logger.info("Starting training...")
    start = time.time()
    trainer.train(resume_from_checkpoint=resume_from if resume_from and not resume_from.startswith("hub:") else None)
    elapsed = time.time() - start
    logger.info(f"Training completed in {elapsed/60:.1f} minutes")

    # Save final model
    final_dir = str(output_dir / "final")
    model.save_pretrained(final_dir)
    tokenizer.save_pretrained(final_dir)
    logger.info(f"Saved final model to {final_dir}")

    # Push to Hub
    if cfg.get("hub_repo"):
        try:
            model.push_to_hub(cfg["hub_repo"], tokenizer=tokenizer, private=cfg.get("hub_private", True))
            logger.info(f"Pushed final model to {cfg['hub_repo']}")
        except Exception as e:
            logger.error(f"Failed to push final model: {e}")

    return final_dir


def export_gguf(model_dir: str, output_path: Optional[str] = None, quant: str = "q4_k_m") -> str:
    """Export fine-tuned model to GGUF for local inference on Apple Silicon.

    Args:
        model_dir: Path to the fine-tuned model
        output_path: Output GGUF file path
        quant: Quantization level (q4_k_m, q5_k_m, q8_0)

    Returns:
        Path to the GGUF file
    """
    if output_path is None:
        output_path = str(Path(model_dir).parent / f"steex-lfm2-market-{quant}.gguf")

    logger.info(f"Exporting to GGUF ({quant})...")

    # Unsloth has built-in GGUF export
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_dir,
        max_seq_length=4096,
        dtype=None,
        load_in_4bit=True,
    )

    model.save_pretrained_gguf(
        output_path.replace(".gguf", ""),
        tokenizer,
        quantization_method=quant,
    )

    logger.info(f"Exported GGUF to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="STEEX LFM2.5 Fine-Tuning Pipeline")
    sub = parser.add_subparsers(dest="command", help="Command")

    # Train
    train_p = sub.add_parser("train", help="Run fine-tuning")
    train_p.add_argument("--dataset", required=True, help="Path to JSONL or HF dataset")
    train_p.add_argument("--epochs", type=int, default=3)
    train_p.add_argument("--batch-size", type=int, default=2)
    train_p.add_argument("--lr", type=float, default=2e-4)
    train_p.add_argument("--lora-r", type=int, default=16)
    train_p.add_argument("--hub-repo", help="HF Hub repo for checkpoint relay (e.g. username/steex-lfm2)")
    train_p.add_argument("--resume", action="store_true", help="Resume from latest checkpoint")
    train_p.add_argument("--install-deps", action="store_true", help="Auto-install dependencies")

    # Export
    export_p = sub.add_parser("export", help="Export to GGUF")
    export_p.add_argument("--model-dir", required=True, help="Path to fine-tuned model")
    export_p.add_argument("--output", help="Output GGUF path")
    export_p.add_argument("--quant", default="q4_k_m", choices=["q4_k_m", "q5_k_m", "q8_0"])

    # Build dataset
    build_p = sub.add_parser("build-dataset", help="Build training dataset from STEEX data")
    build_p.add_argument("--output", default="data/llm/train.jsonl", help="Output path")
    build_p.add_argument("--data-dir", default="data", help="STEEX data directory")

    # Status
    status_p = sub.add_parser("status", help="Show training status and platform info")

    args = parser.parse_args()

    if args.command == "train":
        if args.install_deps:
            install_dependencies(detect_platform())

        config = {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.lr,
            "lora_r": args.lora_r,
        }
        if args.hub_repo:
            config["hub_repo"] = args.hub_repo

        train(args.dataset, config=config, resume=args.resume)

    elif args.command == "export":
        export_gguf(args.model_dir, args.output, args.quant)

    elif args.command == "build-dataset":
        from src.llm.dataset_builder import LLMDatasetBuilder
        builder = LLMDatasetBuilder(data_dir=Path(args.data_dir))
        examples = builder.build_full_dataset()
        builder.save_jsonl(examples, Path(args.output))
        print(f"Built {len(examples)} training examples → {args.output}")

    elif args.command == "status":
        info = get_gpu_info()
        print(f"Platform:  {info['platform']}")
        print(f"GPU:       {info['gpu']}")
        print(f"VRAM:      {info['vram_gb']:.1f} GB")

        # Check for checkpoints
        ckpt_dir = Path(DEFAULT_CONFIG["checkpoint_dir"])
        if ckpt_dir.exists():
            checkpoints = list(ckpt_dir.glob("**/checkpoint-*"))
            print(f"Local checkpoints: {len(checkpoints)}")
        else:
            print("Local checkpoints: 0")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
