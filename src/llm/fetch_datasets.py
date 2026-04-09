#!/usr/bin/env python3
"""Download and convert public financial datasets for LFM fine-tuning.

Fetches high-quality open-source datasets from HuggingFace, converts them
to our chat JSONL format, and merges with synthetic STEEX data.

Usage:
    venv/bin/python -m src.llm.fetch_datasets
    venv/bin/python -m src.llm.fetch_datasets --max-per-source 5000
"""

import argparse
import json
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_FINANCE = (
    "You are a quantitative trading analyst. You analyze financial data, market conditions, "
    "and trading signals to provide structured analysis with clear reasoning."
)

SYSTEM_PROMPT_SENTIMENT = (
    "You are a financial sentiment analyst. You classify the sentiment of financial news "
    "and statements as positive, negative, or neutral, with brief justification."
)

# Datasets ranked by relevance to STEEX
DATASETS = [
    {
        "name": "fingpt-forecaster",
        "repo": "FinGPT/fingpt-forecaster-dow30-202305-202405",
        "split": "train",
        "priority": 1,
        "max_samples": None,  # Take all (~1500)
        "converter": "convert_fingpt_forecaster",
        "description": "Stock analysis with fundamentals + news → prediction. Most directly relevant.",
    },
    {
        "name": "finance-instruct-500k",
        "repo": "Josephgflowers/Finance-Instruct-500k",
        "split": "train",
        "priority": 2,
        "max_samples": 20000,
        "converter": "convert_finance_instruct",
        "description": "General financial reasoning in native chat format.",
    },
    {
        "name": "sujet-finance",
        "repo": "sujet-ai/Sujet-Finance-Instruct-177k",
        "split": "train",
        "priority": 2,
        "max_samples": 15000,
        "converter": "convert_sujet_finance",
        "description": "Multi-task financial instructions (sentiment, QA, classification).",
    },
    {
        "name": "fingpt-sentiment",
        "repo": "FinGPT/fingpt-sentiment-train",
        "split": "train",
        "priority": 3,
        "max_samples": 10000,
        "converter": "convert_fingpt_sentiment",
        "description": "Financial news sentiment classification.",
    },
    {
        "name": "fincot",
        "repo": "TheFinAI/FinCoT",
        "split": "SFT",
        "priority": 2,
        "max_samples": None,  # Take all (~7700)
        "converter": "convert_fincot",
        "description": "Chain-of-thought financial reasoning with step-by-step analysis.",
    },
    {
        "name": "finance-alpaca",
        "repo": "gbharti/finance-alpaca",
        "split": "train",
        "priority": 3,
        "max_samples": 10000,
        "converter": "convert_finance_alpaca",
        "description": "General financial Q&A in Alpaca format.",
    },
]


def _make_chat(system: str, user: str, assistant: str) -> Dict:
    """Create a chat training example."""
    return {
        "messages": [
            {"role": "system", "content": system.strip()},
            {"role": "user", "content": user.strip()},
            {"role": "assistant", "content": assistant.strip()},
        ]
    }


# ---------------------------------------------------------------------------
# Converters — one per dataset schema
# ---------------------------------------------------------------------------

def convert_fingpt_forecaster(dataset) -> List[Dict]:
    """FinGPT forecaster: prompt/answer format with stock analysis."""
    examples = []
    for row in dataset:
        prompt = row.get("prompt", "")
        answer = row.get("answer", "")
        if not prompt or not answer:
            continue
        # Strip instruction tags if present
        user_msg = prompt
        for tag in ["[INST]", "[/INST]", "<<SYS>>", "<</SYS>>"]:
            user_msg = user_msg.replace(tag, "")
        user_msg = user_msg.strip()
        if len(user_msg) < 50 or len(answer) < 50:
            continue
        examples.append(_make_chat(SYSTEM_PROMPT_FINANCE, user_msg, answer))
    return examples


def convert_finance_instruct(dataset) -> List[Dict]:
    """Finance-Instruct-500k: native system/user/assistant format."""
    examples = []
    for row in dataset:
        system = row.get("system", SYSTEM_PROMPT_FINANCE)
        user = row.get("user", "")
        assistant = row.get("assistant", "")
        if not user or not assistant:
            continue
        if len(user) < 20 or len(assistant) < 20:
            continue
        examples.append(_make_chat(system, user, assistant))
    return examples


def convert_sujet_finance(dataset) -> List[Dict]:
    """Sujet Finance: system_prompt/user_prompt/answer with task types."""
    examples = []
    for row in dataset:
        system = row.get("system_prompt", SYSTEM_PROMPT_FINANCE)
        user = row.get("user_prompt", "")
        inputs = row.get("inputs", "")
        answer = row.get("answer", "")
        if not user or not answer:
            continue
        # Combine user prompt with inputs if present
        full_user = f"{user}\n\n{inputs}".strip() if inputs else user
        if len(full_user) < 20 or len(answer) < 20:
            continue
        examples.append(_make_chat(system or SYSTEM_PROMPT_FINANCE, full_user, answer))
    return examples


def convert_fingpt_sentiment(dataset) -> List[Dict]:
    """FinGPT sentiment: input/instruction/output format."""
    examples = []
    for row in dataset:
        instruction = row.get("instruction", "")
        inp = row.get("input", "")
        output = row.get("output", "")
        if not inp or not output:
            continue
        user_msg = f"{instruction}\n\n{inp}".strip() if instruction else inp
        if len(user_msg) < 20:
            continue
        examples.append(_make_chat(SYSTEM_PROMPT_SENTIMENT, user_msg, output))
    return examples


def convert_fincot(dataset) -> List[Dict]:
    """FinCoT: chain-of-thought financial reasoning."""
    examples = []
    for row in dataset:
        question = row.get("Question", "")
        reasoning = row.get("Reasoning_process", "")
        answer = row.get("Final_response", "")
        if not question or not answer:
            continue
        # Combine reasoning and answer for rich training signal
        assistant_msg = reasoning
        if answer and answer not in reasoning:
            assistant_msg = f"{reasoning}\n\n**Answer:** {answer}"
        if len(question) < 20 or len(assistant_msg) < 20:
            continue
        examples.append(_make_chat(SYSTEM_PROMPT_FINANCE, question, assistant_msg.strip()))
    return examples


def convert_finance_alpaca(dataset) -> List[Dict]:
    """Finance Alpaca: instruction/input/output format."""
    examples = []
    for row in dataset:
        instruction = row.get("instruction", "")
        inp = row.get("input", "")
        output = row.get("output", "")
        if not instruction or not output:
            continue
        user_msg = f"{instruction}\n\n{inp}".strip() if inp else instruction
        if len(user_msg) < 20 or len(output) < 20:
            continue
        examples.append(_make_chat(SYSTEM_PROMPT_FINANCE, user_msg, output))
    return examples


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

CONVERTERS = {
    "convert_fingpt_forecaster": convert_fingpt_forecaster,
    "convert_finance_instruct": convert_finance_instruct,
    "convert_sujet_finance": convert_sujet_finance,
    "convert_fingpt_sentiment": convert_fingpt_sentiment,
    "convert_fincot": convert_fincot,
    "convert_finance_alpaca": convert_finance_alpaca,
}


def fetch_and_convert(
    max_per_source: Optional[int] = None,
    skip_datasets: Optional[List[str]] = None,
) -> List[Dict]:
    """Download datasets from HuggingFace and convert to chat format."""
    try:
        from datasets import load_dataset
    except ImportError:
        logger.error("Install datasets: pip install datasets")
        return []

    skip = set(skip_datasets or [])
    all_examples = []

    for ds_info in sorted(DATASETS, key=lambda d: d["priority"]):
        name = ds_info["name"]
        if name in skip:
            logger.info(f"Skipping {name}")
            continue

        repo = ds_info["repo"]
        max_n = max_per_source or ds_info.get("max_samples")

        logger.info(f"Fetching {name} from {repo}...")
        try:
            ds = load_dataset(repo, split=ds_info["split"])
        except Exception as e:
            logger.warning(f"Failed to load {repo}: {e}")
            continue

        # Convert
        converter = CONVERTERS[ds_info["converter"]]
        examples = converter(ds)
        logger.info(f"  Converted {len(examples)} examples from {name}")

        # Sample if needed
        if max_n and len(examples) > max_n:
            random.seed(42)
            examples = random.sample(examples, max_n)
            logger.info(f"  Sampled down to {max_n}")

        all_examples.extend(examples)
        logger.info(f"  Running total: {len(all_examples)}")

    return all_examples


def merge_datasets(
    synthetic_path: Path,
    public_examples: List[Dict],
    output_path: Path,
) -> int:
    """Merge synthetic STEEX data with public datasets."""
    examples = []

    # Load synthetic data
    if synthetic_path.exists():
        with open(synthetic_path) as f:
            for line in f:
                examples.append(json.loads(line))
        logger.info(f"Loaded {len(examples)} synthetic examples")

    # Add public data
    examples.extend(public_examples)
    logger.info(f"Total before shuffle: {len(examples)}")

    # Shuffle
    random.seed(42)
    random.shuffle(examples)

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    logger.info(f"Saved merged dataset: {output_path} ({len(examples)} examples)")
    return len(examples)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Fetch and convert financial datasets")
    parser.add_argument("--max-per-source", type=int, default=None, help="Max examples per dataset")
    parser.add_argument("--skip", nargs="*", default=[], help="Dataset names to skip")
    parser.add_argument("--output", default="data/llm/train_merged.jsonl", help="Output path")
    parser.add_argument("--synthetic", default="data/llm/train.jsonl", help="Synthetic data path")
    parser.add_argument("--no-merge", action="store_true", help="Don't merge with synthetic data")
    parser.add_argument("--list", action="store_true", help="List available datasets")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable datasets:\n")
        for ds in sorted(DATASETS, key=lambda d: d["priority"]):
            max_n = ds.get("max_samples", "all")
            print(f"  [{ds['priority']}] {ds['name']:25s} max={str(max_n):>6s}  {ds['description']}")
        print(f"\nTotal: {len(DATASETS)} datasets")
        return

    # Fetch and convert public datasets
    public = fetch_and_convert(
        max_per_source=args.max_per_source,
        skip_datasets=args.skip,
    )

    if args.no_merge:
        # Save public only
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w") as f:
            for ex in public:
                f.write(json.dumps(ex) + "\n")
        print(f"\nSaved {len(public)} public examples → {output}")
    else:
        # Merge with synthetic
        total = merge_datasets(
            synthetic_path=Path(args.synthetic),
            public_examples=public,
            output_path=Path(args.output),
        )
        print(f"\nMerged dataset: {total} examples → {args.output}")
        print(f"  Synthetic:  {args.synthetic}")
        print(f"  Public:     {len(public)} examples from {len(DATASETS)} sources")


if __name__ == "__main__":
    main()
