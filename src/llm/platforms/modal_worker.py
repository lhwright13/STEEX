"""Modal serverless training worker.

Standalone Modal App that runs a training session on A100.
Triggered by the local pipeline controller via `modal run`.

Usage:
    modal run src/llm/platforms/modal_worker.py --hub-repo user/steex-lfm2-market --dataset-repo user/steex-training-data
"""

import argparse
import json
import logging
import time

logger = logging.getLogger(__name__)

# Modal imports are deferred to avoid requiring modal locally
try:
    import modal

    app = modal.App("steex-training")
    volume = modal.Volume.from_name("steex-checkpoints", create_if_missing=True)

    training_image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install(
            "unsloth[colab-new]",
            "datasets",
            "huggingface_hub",
            "trl",
            "torch",
        )
    )

    @app.function(
        gpu="A100",
        timeout=4 * 3600,
        image=training_image,
        volumes={"/cache": volume},
        secrets=[modal.Secret.from_name("huggingface")],
    )
    def train_session(
        hub_repo: str,
        dataset_repo: str,
        max_steps: int = 500,
        learning_rate: float = 1e-4,
        save_steps: int = 25,
    ) -> str:
        """Run a single training session on Modal A100."""
        import json
        import os
        import time

        from datasets import Dataset
        from huggingface_hub import HfApi, hf_hub_download
        from transformers import TrainingArguments
        from trl import SFTTrainer
        from unsloth import FastLanguageModel
        from unsloth.chat_templates import get_chat_template

        start_time = time.time()
        api = HfApi()

        # 1. Check for existing LoRA on Hub (resume)
        resume = False
        try:
            files = api.list_repo_files(hub_repo)
            has_adapter = any("adapter_config.json" in f for f in files)
            resume = has_adapter
        except Exception:
            pass

        # 2. Load model
        if resume:
            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=hub_repo,
                max_seq_length=4096,
                dtype=None,
                load_in_4bit=True,
            )
            print(f"Resumed from {hub_repo}")
        else:
            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name="LiquidAI/LFM2.5-1.2B-Base",
                max_seq_length=4096,
                dtype=None,
                load_in_4bit=True,
            )
            model = FastLanguageModel.get_peft_model(
                model,
                r=16, lora_alpha=32, lora_dropout=0.05,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                "gate_proj", "up_proj", "down_proj"],
                bias="none",
                use_gradient_checkpointing="unsloth",
            )
            print("Starting fresh from base model")

        # 3. Load dataset from Hub
        dataset_path = hf_hub_download(
            repo_id=dataset_repo, filename="train.jsonl", repo_type="dataset",
        )
        examples = []
        with open(dataset_path) as f:
            for line in f:
                examples.append(json.loads(line))
        dataset = Dataset.from_list(examples)

        tokenizer = get_chat_template(tokenizer, chat_template="chatml")

        def format_example(example):
            text = tokenizer.apply_chat_template(
                example["messages"], tokenize=False, add_generation_prompt=False,
            )
            return {"text": text}

        dataset = dataset.map(format_example, remove_columns=dataset.column_names)
        print(f"Dataset: {len(dataset)} examples")

        # 4. Train
        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            args=TrainingArguments(
                output_dir="/cache/checkpoints",
                max_steps=max_steps,
                per_device_train_batch_size=2,
                gradient_accumulation_steps=8,
                learning_rate=learning_rate,
                warmup_ratio=0.05,
                weight_decay=0.01,
                lr_scheduler_type="cosine",
                save_steps=save_steps,
                logging_steps=10,
                save_total_limit=3,
                bf16=True,  # A100 supports bf16
                optim="adamw_8bit",
                seed=42,
                report_to="none",
            ),
            dataset_text_field="text",
            max_seq_length=4096,
            packing=True,
        )

        result = trainer.train()
        elapsed = time.time() - start_time

        # 5. Push to Hub
        model.push_to_hub(hub_repo, tokenizer=tokenizer, private=True)

        # 6. Update training state
        final_loss = result.training_loss
        state = {
            "platform": "modal",
            "gpu": "A100",
            "step": trainer.state.global_step,
            "final_loss": final_loss,
            "duration_minutes": elapsed / 60,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_active": False,
            "session_complete": True,
        }

        api.upload_file(
            path_or_fileobj=json.dumps(state, indent=2).encode(),
            path_in_repo="training_state.json",
            repo_id=hub_repo,
            commit_message=f"Modal session: {trainer.state.global_step} steps, loss={final_loss:.4f}",
        )

        print(f"Done: {trainer.state.global_step} steps, loss={final_loss:.4f}, {elapsed/60:.1f}min")
        return json.dumps(state)

    @app.local_entrypoint()
    def main(
        hub_repo: str,
        dataset_repo: str,
        max_steps: int = 500,
    ):
        """CLI entry point for `modal run`."""
        result = train_session.remote(
            hub_repo=hub_repo,
            dataset_repo=dataset_repo,
            max_steps=max_steps,
        )
        print(result)

except ImportError:
    # Modal not installed — this file is importable but non-functional
    pass
