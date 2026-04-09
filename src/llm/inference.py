"""Local inference for fine-tuned LFM2.5 on Apple Silicon.

Supports Ollama (recommended), llama.cpp (GGUF), and MLX backends.

Usage:
    python -m src.llm.inference --model hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q5_K_M
    python -m src.llm.inference --model path/to/model.gguf --backend llama_cpp
    python -m src.llm.inference --model username/steex-lfm2-market-gguf --from-hub
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

OLLAMA_API = "http://localhost:11434"
DEFAULT_MODEL = "hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q5_K_M"

DEFAULT_SYSTEM_PROMPT = (
    "You are a quantitative trading analyst for the STEEX systematic trading system. "
    "You analyze stock screening signals, market regime data, and trade outcomes to make "
    "informed trading decisions. You provide structured analysis with clear reasoning, "
    "confidence levels, and risk considerations. Always reference specific signal values "
    "in your analysis."
)


class LFMInference:
    """Run LFM2.5 locally on Apple Silicon."""

    def __init__(self, model: str = DEFAULT_MODEL, backend: str = "auto"):
        """Initialize inference engine.

        Args:
            model: Ollama model tag, GGUF path, or HF repo ID
            backend: "ollama", "llama_cpp", "mlx", or "auto"
        """
        self.model = model
        self.backend = backend
        self._model = None
        self._tokenizer = None

        if backend == "auto":
            self.backend = self._detect_backend()

        if self.backend != "ollama":
            self._load_model()

    def _detect_backend(self) -> str:
        """Pick the best available backend."""
        # Check Ollama first (preferred — already installed, Metal optimized)
        try:
            req = Request(f"{OLLAMA_API}/api/tags", method="GET")
            urlopen(req, timeout=2)
            return "ollama"
        except (URLError, OSError):
            pass
        try:
            import mlx_lm
            return "mlx"
        except ImportError:
            pass
        try:
            from llama_cpp import Llama
            return "llama_cpp"
        except ImportError:
            pass
        raise RuntimeError(
            "No inference backend found. Install one:\n"
            "  brew install ollama           # Recommended for Apple Silicon\n"
            "  pip install mlx-lm            # Apple Silicon optimized\n"
            "  pip install llama-cpp-python   # GGUF support"
        )

    def _load_model(self):
        """Load model with the selected backend (non-Ollama)."""
        logger.info(f"Loading model with {self.backend} backend...")

        if self.backend == "llama_cpp":
            from llama_cpp import Llama
            self._model = Llama(
                model_path=self.model,
                n_ctx=4096,
                n_gpu_layers=-1,
                verbose=False,
            )
        elif self.backend == "mlx":
            import mlx_lm
            self._model, self._tokenizer = mlx_lm.load(self.model)

        logger.info(f"Model loaded ({self.backend})")

    def _ollama_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> Dict:
        """Call Ollama chat API and return full response with metrics."""
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "min_p": 0.15,
                "repeat_penalty": 1.05,
            },
        }).encode()

        req = Request(
            f"{OLLAMA_API}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        """Generate a response."""
        if system_prompt is None:
            system_prompt = DEFAULT_SYSTEM_PROMPT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        if self.backend == "ollama":
            result = self._ollama_chat(messages, max_tokens, temperature)
            return result["message"]["content"]

        elif self.backend == "llama_cpp":
            response = self._model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                min_p=0.15,
                repeat_penalty=1.05,
            )
            return response["choices"][0]["message"]["content"]

        elif self.backend == "mlx":
            import mlx_lm
            formatted = (
                f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                f"<|im_start|>user\n{prompt}<|im_end|>\n"
                f"<|im_start|>assistant\n"
            )
            return mlx_lm.generate(
                self._model, self._tokenizer,
                prompt=formatted, max_tokens=max_tokens, temp=temperature,
            )

    def generate_with_metrics(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> Dict:
        """Generate a response and return performance metrics."""
        if system_prompt is None:
            system_prompt = DEFAULT_SYSTEM_PROMPT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        start = time.time()

        if self.backend == "ollama":
            result = self._ollama_chat(messages, max_tokens, temperature)
            elapsed = time.time() - start
            eval_count = result.get("eval_count", 0)
            eval_duration_ns = result.get("eval_duration", 1)
            prompt_count = result.get("prompt_eval_count", 0)
            prompt_duration_ns = result.get("prompt_eval_duration", 1)

            return {
                "content": result["message"]["content"],
                "backend": "ollama",
                "model": self.model,
                "tokens_generated": eval_count,
                "tokens_prompt": prompt_count,
                "generation_tok_s": eval_count / (eval_duration_ns / 1e9) if eval_duration_ns else 0,
                "prompt_tok_s": prompt_count / (prompt_duration_ns / 1e9) if prompt_duration_ns else 0,
                "total_time_s": elapsed,
                "time_to_first_token_s": (result.get("prompt_eval_duration", 0) + result.get("load_duration", 0)) / 1e9,
            }
        else:
            content = self.generate(prompt, system_prompt, max_tokens, temperature)
            elapsed = time.time() - start
            return {
                "content": content,
                "backend": self.backend,
                "model": self.model,
                "total_time_s": elapsed,
            }

    def analyze_screening(self, screening_data: dict) -> str:
        """Analyze a screening result using the fine-tuned model."""
        from src.llm.dataset_builder import _format_screening_input
        prompt = _format_screening_input(screening_data)
        return self.generate(prompt)

    def assess_regime(self, vix: float, vix_pct: float, breadth: float, yield_spread: float) -> str:
        """Get a regime assessment from the model."""
        prompt = (
            f"Assess current market regime:\n\n"
            f"- VIX: {vix:.1f} ({vix_pct:.0f}th percentile)\n"
            f"- Market breadth: {breadth:.0f}% above 200-day MA\n"
            f"- Yield curve spread: {yield_spread:+.2f}%\n\n"
            f"What is the current regime and recommended action?"
        )
        return self.generate(prompt)

    def analyze_trade(self, trade: dict) -> str:
        """Get a post-mortem analysis for a completed trade."""
        from src.llm.dataset_builder import _format_trade_postmortem_input
        prompt = _format_trade_postmortem_input(trade)
        return self.generate(prompt)


def download_from_hub(repo_id: str, filename: Optional[str] = None) -> str:
    """Download a GGUF model from HF Hub.

    Args:
        repo_id: HF Hub repo (e.g., "username/steex-lfm2-market-gguf")
        filename: Specific file to download (auto-detects GGUF if None)

    Returns:
        Local path to the downloaded file
    """
    from huggingface_hub import hf_hub_download, list_repo_files

    if filename is None:
        files = list_repo_files(repo_id)
        gguf_files = [f for f in files if f.endswith(".gguf")]
        # Prefer Q4_K_M
        q4_files = [f for f in gguf_files if "Q4_K_M" in f]
        filename = q4_files[0] if q4_files else gguf_files[0]
        logger.info(f"Auto-selected: {filename}")

    path = hf_hub_download(repo_id=repo_id, filename=filename)
    logger.info(f"Downloaded to {path}")
    return path


def run_benchmark(engine: "LFMInference") -> Dict:
    """Run a standardized benchmark suite against the model."""
    prompts = [
        {
            "name": "Regime Assessment (short)",
            "prompt": "VIX is 14, breadth 72%, yield spread +1.2%. What regime?",
            "max_tokens": 128,
        },
        {
            "name": "Screening Analysis (medium)",
            "prompt": (
                "Analyze AAPL for entry:\n"
                "- 6-month momentum: +18.5%, 1-month: +3.2%\n"
                "- Above 50-day MA: Yes, Above 200-day MA: Yes\n"
                "- Insider score: 72/100 (4 buyers, $2.1M)\n"
                "- Sentiment: 68/100 (Bullish)\n"
                "- Fundamental score: 75/100 (P/E 28.3, ROE 45%)\n"
                "- Options flow: 61/100 (P/C 0.82)\n\n"
                "Should we enter? Provide analysis."
            ),
            "max_tokens": 512,
        },
        {
            "name": "Trade Post-Mortem (long)",
            "prompt": (
                "Analyze this completed trade:\n"
                "- Ticker: NVDA\n"
                "- Entry: $142.50 on 2026-02-10, Exit: $128.30 on 2026-03-05\n"
                "- P&L: -9.96% (-$1,420)\n"
                "- Hold: 23 days, Exit: stop_loss\n"
                "- Entry score: 78, Signals: momentum, insider cluster, volume surge\n\n"
                "What went wrong? Lessons learned?"
            ),
            "max_tokens": 512,
        },
    ]

    print(f"\n{'='*60}")
    print(f"STEEX LFM Inference Benchmark")
    print(f"Model:   {engine.model}")
    print(f"Backend: {engine.backend}")
    print(f"{'='*60}\n")

    results = []
    for p in prompts:
        print(f"--- {p['name']} ---")
        metrics = engine.generate_with_metrics(
            p["prompt"], max_tokens=p["max_tokens"], temperature=0.3,
        )

        gen_speed = metrics.get("generation_tok_s", 0)
        prompt_speed = metrics.get("prompt_tok_s", 0)
        tok_gen = metrics.get("tokens_generated", 0)
        tok_prompt = metrics.get("tokens_prompt", 0)

        print(f"  Prompt tokens:  {tok_prompt}")
        print(f"  Output tokens:  {tok_gen}")
        print(f"  Prompt speed:   {prompt_speed:.1f} tok/s")
        print(f"  Gen speed:      {gen_speed:.1f} tok/s")
        print(f"  Total time:     {metrics['total_time_s']:.2f}s")
        print(f"  Response preview: {metrics['content'][:120]}...")
        print()

        results.append({**metrics, "name": p["name"]})

    # Summary
    avg_gen = sum(r.get("generation_tok_s", 0) for r in results) / len(results)
    avg_prompt = sum(r.get("prompt_tok_s", 0) for r in results) / len(results)
    total_time = sum(r["total_time_s"] for r in results)

    print(f"{'='*60}")
    print(f"SUMMARY")
    print(f"  Avg generation speed: {avg_gen:.1f} tok/s")
    print(f"  Avg prompt speed:     {avg_prompt:.1f} tok/s")
    print(f"  Total benchmark time: {total_time:.2f}s")
    print(f"{'='*60}")

    return {"prompts": results, "avg_gen_tok_s": avg_gen, "avg_prompt_tok_s": avg_prompt}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="STEEX LFM Local Inference")
    sub = parser.add_subparsers(dest="command")

    # Benchmark
    bench_p = sub.add_parser("benchmark", help="Run inference benchmark")
    bench_p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    bench_p.add_argument("--backend", choices=["auto", "ollama", "llama_cpp", "mlx"], default="auto")

    # Chat
    chat_p = sub.add_parser("chat", help="Interactive chat mode")
    chat_p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    chat_p.add_argument("--backend", choices=["auto", "ollama", "llama_cpp", "mlx"], default="auto")

    # Single query
    query_p = sub.add_parser("query", help="Single query")
    query_p.add_argument("prompt", help="The prompt to send")
    query_p.add_argument("--model", default=DEFAULT_MODEL)
    query_p.add_argument("--backend", choices=["auto", "ollama", "llama_cpp", "mlx"], default="auto")

    args = parser.parse_args()

    if args.command == "benchmark":
        engine = LFMInference(args.model, backend=args.backend)
        run_benchmark(engine)

    elif args.command == "chat":
        engine = LFMInference(args.model, backend=args.backend)
        print("STEEX Market Analyst (type 'quit' to exit)")
        print("-" * 50)
        while True:
            try:
                prompt = input("\nYou: ").strip()
                if prompt.lower() in ("quit", "exit", "q"):
                    break
                if not prompt:
                    continue
                response = engine.generate(prompt)
                print(f"\nAnalyst: {response}")
            except (KeyboardInterrupt, EOFError):
                break

    elif args.command == "query":
        engine = LFMInference(args.model, backend=args.backend)
        print(engine.generate(args.prompt))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
