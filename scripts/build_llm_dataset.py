#!/usr/bin/env python3
"""Build LLM training dataset from STEEX data.

Usage:
    venv/bin/python scripts/build_llm_dataset.py
    venv/bin/python scripts/build_llm_dataset.py --output data/llm/train.jsonl
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.dataset_builder import LLMDatasetBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build LLM training dataset from STEEX data")
    parser.add_argument("--output", default="data/llm/train.jsonl", help="Output JSONL path")
    parser.add_argument("--data-dir", default="data", help="STEEX data directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output = Path(args.output)

    builder = LLMDatasetBuilder(data_dir=data_dir)

    # Build dataset from all available sources
    examples = builder.build_full_dataset()

    if not examples:
        logger.warning("No training examples generated. This is expected if you haven't run trades yet.")
        logger.info("The pipeline will generate synthetic regime examples as a starting point.")
        logger.info("As you run trades and screenings, the dataset will grow.")

    # Save
    builder.save_jsonl(examples, output)

    # Summary
    print(f"\n{'='*50}")
    print(f"Training dataset built: {output}")
    print(f"Total examples: {len(examples)}")

    # Count by type
    regime_count = sum(1 for e in examples if "regime" in e["messages"][1]["content"].lower())
    postmortem_count = sum(1 for e in examples if "post-mortem" in e["messages"][1]["content"].lower()
                          or "completed trade" in e["messages"][1]["content"].lower())
    screening_count = len(examples) - regime_count - postmortem_count

    print(f"  Screening examples: {screening_count}")
    print(f"  Post-mortem examples: {postmortem_count}")
    print(f"  Regime examples: {regime_count}")
    print(f"{'='*50}")
    print(f"\nNext steps:")
    print(f"  1. Upload {output} to HF Hub or Colab")
    print(f"  2. Open notebooks/llm/colab_train.ipynb in Colab")
    print(f"  3. Run training!")


if __name__ == "__main__":
    main()
