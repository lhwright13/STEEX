"""Near-continuous training pipeline controller.

Runs on the local Mac. Monitors training state on HF Hub,
dispatches to GPU platforms, validates checkpoints, and
exports final models.

Usage:
    python -m src.llm.pipeline status
    python -m src.llm.pipeline dispatch
    python -m src.llm.pipeline monitor --single-check
"""

import json
import logging
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .checkpoint_validator import CheckpointValidator, ValidationResult
from .hub_relay import HubRelay
from .platforms.base import PlatformAdapter, PlatformConfig
from .platforms.modal_adapter import ModalAdapter
from .platforms.notebook_adapter import (
    ColabAdapter,
    KaggleAdapter,
    LightningAdapter,
    SageMakerAdapter,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
STATE_FILE = PROJECT_ROOT / "data" / "llm" / "pipeline_state.json"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "llm_pipeline.yaml"

ADAPTER_MAP = {
    "modal": ModalAdapter,
    "colab": ColabAdapter,
    "kaggle": KaggleAdapter,
    "sagemaker": SageMakerAdapter,
    "lightning": LightningAdapter,
}


class TrainingPipeline:
    """Near-continuous training pipeline controller."""

    def __init__(self, config_path: Optional[Path] = None):
        config_path = config_path or DEFAULT_CONFIG
        self.config = self._load_config(config_path)
        self.state = self._load_state()
        self.hub = HubRelay(
            model_repo=self.config["hub"]["model_repo"],
            dataset_repo=self.config["hub"]["dataset_repo"],
        )
        self.validator = CheckpointValidator(
            max_loss_spike=self.config["validation"]["max_loss_spike"],
            convergence_window=self.config["validation"]["convergence_window"],
        )
        self.platforms: Dict[str, PlatformAdapter] = {}
        self._register_platforms()

    def _load_config(self, path: Path) -> Dict:
        if not path.exists():
            raise FileNotFoundError(f"Pipeline config not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f)

    def _load_state(self) -> Dict:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
        return {
            "total_steps": 0,
            "total_hours": 0.0,
            "best_loss": None,
            "best_checkpoint_id": None,
            "loss_history": [],
            "sessions": [],
            "usage": {},
            "training_complete": False,
            "created": datetime.now().isoformat(),
        }

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    def _register_platforms(self):
        for name, platform_cfg in self.config.get("platforms", {}).items():
            if not platform_cfg.get("enabled", True):
                continue
            adapter_cls = ADAPTER_MAP.get(name)
            if adapter_cls is None:
                logger.warning(f"Unknown platform: {name}")
                continue
            cfg = PlatformConfig(
                name=name,
                gpu=platform_cfg.get("gpu", "T4"),
                priority=platform_cfg.get("priority", 5),
                phase=platform_cfg.get("phase", "any"),
                weekly_hours=platform_cfg.get("weekly_hours"),
                monthly_hours=platform_cfg.get("monthly_hours"),
                daily_hours=platform_cfg.get("daily_hours"),
                monthly_budget_dollars=platform_cfg.get("monthly_budget"),
                max_session_hours=platform_cfg.get("max_session_hours", 6),
                notebook_url=platform_cfg.get("notebook_url"),
            )
            self.platforms[name] = adapter_cls(cfg)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self):
        """First-time setup: create Hub repos, upload dataset, push config."""
        logger.info("Initializing training pipeline...")

        # Create repos
        self.hub.ensure_repos()

        # Upload dataset
        dataset_path = PROJECT_ROOT / "data" / "llm" / "train_merged.jsonl"
        if dataset_path.exists():
            self.hub.sync_dataset(dataset_path)
        else:
            logger.warning(f"Dataset not found at {dataset_path}")

        # Push pipeline config for workers
        worker_config = {
            "hub_repo": self.config["hub"]["model_repo"],
            "dataset_repo": self.config["hub"]["dataset_repo"],
            "learning_rate": self.config["training"].get("continued_learning_rate", 1e-4),
            "save_steps": self.config["training"].get("save_steps", 25),
            "max_seq_length": self.config["training"].get("max_seq_length", 4096),
            "epochs_this_session": 1,
        }
        self.hub.push_pipeline_config(worker_config)

        self._save_state()
        logger.info("Pipeline initialized. Run `dispatch` to start training.")

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def run_check(self) -> str:
        """Single poll cycle — called by cron every 15 min.

        Returns:
            Status message describing what happened.
        """
        if self.state.get("training_complete"):
            return "Training complete. Run `export` for GGUF."

        # Check Hub for session completion
        hub_state = self.hub.get_training_state()
        if hub_state and hub_state.get("session_complete"):
            return self._handle_session_complete(hub_state)

        # Check if a session is active
        if hub_state and hub_state.get("session_active"):
            elapsed = self._session_elapsed(hub_state)
            return f"Session active on {hub_state.get('platform', '?')} ({elapsed:.0f} min)"

        # No session running — dispatch next
        return self.dispatch()

    def _handle_session_complete(self, hub_state: Dict) -> str:
        """Process a completed session: validate, record, dispatch next."""
        platform = hub_state.get("platform", "unknown")
        final_loss = hub_state.get("final_loss", 0)
        steps = hub_state.get("step", 0)
        duration = hub_state.get("duration_minutes", 0)

        # Validate
        result = self.validator.validate(
            current_loss=final_loss,
            best_loss=self.state.get("best_loss"),
            loss_history=self.state.get("loss_history", []),
        )

        # Record session
        session = {
            "platform": platform,
            "steps": steps,
            "loss": final_loss,
            "duration_minutes": duration,
            "timestamp": hub_state.get("timestamp", datetime.now().isoformat()),
            "validation": result.reason,
            "promoted": result.promoted,
        }
        self.state["sessions"].append(session)
        self.state["total_steps"] += steps
        self.state["total_hours"] += duration / 60
        self.state["loss_history"].append(final_loss)

        if result.promoted:
            self.state["best_loss"] = result.best_loss
            self.state["best_checkpoint_id"] = self.hub.get_latest_checkpoint_id()

        # Record to Hub loss history
        self.hub.append_loss_history({
            "step": self.state["total_steps"],
            "loss": final_loss,
            "platform": platform,
            "timestamp": hub_state.get("timestamp"),
        })

        # Update platform usage
        self._update_usage(platform, duration / 60)

        # Clear session_complete flag on Hub
        hub_state["session_complete"] = False
        hub_state["session_active"] = False
        self.hub.update_training_state(hub_state)

        self._save_state()

        if not result.valid:
            _notify(f"Checkpoint REJECTED: {result.reason}")
            return f"Checkpoint rejected: {result.reason}"

        if result.converged:
            self.state["training_complete"] = True
            self._save_state()
            _notify(f"Training CONVERGED at loss {final_loss:.4f}! Run `export` for GGUF.")
            return f"Training converged: {result.reason}"

        _notify(f"{platform} done: {steps} steps, loss {final_loss:.4f}")

        # Dispatch next
        return self.dispatch()

    def _session_elapsed(self, hub_state: Dict) -> float:
        """Minutes since session started."""
        ts = hub_state.get("timestamp", "")
        try:
            start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return (datetime.now(start.tzinfo) - start).total_seconds() / 60
        except (ValueError, TypeError) as e:
            logger.debug(f"Could not parse session timestamp: {e}")
            return 0

    # ------------------------------------------------------------------
    # Platform selection and dispatch
    # ------------------------------------------------------------------

    def dispatch(self) -> str:
        """Pick and dispatch to the best available platform."""
        adapter = self._select_next_platform()
        if adapter is None:
            return "No platforms available (quotas exhausted or all disabled)"

        training_config = {
            "hub_repo": self.config["hub"]["model_repo"],
            "dataset_repo": self.config["hub"]["dataset_repo"],
            "max_steps_per_session": self._steps_for_platform(adapter),
            "usage": self.state.get("usage", {}),
        }

        result = adapter.dispatch(training_config)

        if adapter.can_auto_dispatch():
            return f"Dispatched to {adapter.name} (auto): {result}"
        else:
            return f"Action needed: open {adapter.name} — {result}"

    def _select_next_platform(self) -> Optional[PlatformAdapter]:
        """Select the next platform based on training phase and quota."""
        target_steps = self.config["training"].get("target_steps", 3000)
        progress = self.state["total_steps"] / max(target_steps, 1)

        candidates = []
        for name, adapter in self.platforms.items():
            remaining = adapter.quota_remaining(self.state.get("usage", {}))
            if remaining < 0.5:  # Less than 30 min
                continue

            phase = adapter.config.phase
            if phase == "early" and progress > 0.3:
                continue
            if phase == "late" and progress < 0.5:
                continue

            # Score: gpu_tier * remaining_hours * auto_bonus
            auto_bonus = 1.2 if adapter.can_auto_dispatch() else 1.0
            score = adapter.gpu_tier * remaining * auto_bonus
            candidates.append((score, adapter))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _steps_for_platform(self, adapter: PlatformAdapter) -> int:
        """Calculate how many steps this platform should run."""
        hours = min(
            adapter.config.max_session_hours,
            adapter.quota_remaining(self.state.get("usage", {})),
        )
        return int(hours * adapter.estimated_steps_per_hour())

    def _update_usage(self, platform: str, hours: float):
        """Update quota tracking for a platform."""
        usage = self.state.setdefault("usage", {})
        key_map = {
            "modal": "modal_dollars_this_month",
            "colab": "colab_hours_this_week",
            "kaggle": "kaggle_hours_this_week",
            "sagemaker": "sagemaker_hours_today",
            "lightning": "lightning_hours_this_month",
        }
        key = key_map.get(platform)
        if key:
            if platform == "modal":
                # Convert hours to dollars (A100 ~$3.50/hr)
                usage[key] = usage.get(key, 0) + hours * 3.50
            else:
                usage[key] = usage.get(key, 0) + hours

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_best(self) -> Optional[str]:
        """Export best checkpoint to GGUF and register with Ollama."""
        best_id = self.state.get("best_checkpoint_id")
        if not best_id:
            logger.warning("No best checkpoint to export")
            return None

        quant = self.config["export"]["quantization"]
        model_name = self.config["export"]["ollama_model_name"]

        logger.info(f"Exporting checkpoint {best_id} as {quant}...")

        # Download LoRA from Hub
        cache_dir = PROJECT_ROOT / "data" / "llm" / "export_cache"
        self.hub.download_checkpoint(best_id, cache_dir)

        # Export GGUF using existing train.py function
        from .train import export_gguf
        gguf_path = export_gguf(str(cache_dir), quant=quant)

        # Register with Ollama
        modelfile = cache_dir / "Modelfile"
        with open(modelfile, "w") as f:
            f.write(f"FROM {gguf_path}\n")
            f.write("PARAMETER temperature 0.3\n")
            f.write("PARAMETER min_p 0.15\n")
            f.write("PARAMETER repeat_penalty 1.05\n")
            f.write(f'SYSTEM "You are a quantitative trading analyst for the STEEX systematic trading system."\n')

        try:
            subprocess.run(
                ["ollama", "create", model_name, "-f", str(modelfile)],
                check=True, capture_output=True, text=True,
            )
            logger.info(f"Registered with Ollama as '{model_name}'")
            _notify(f"Model exported! Run: ollama run {model_name}")
        except Exception as e:
            logger.error(f"Ollama registration failed: {e}")

        # Push GGUF to Hub
        self.hub.push_gguf(Path(gguf_path))

        return gguf_path

    # ------------------------------------------------------------------
    # Dataset sync
    # ------------------------------------------------------------------

    def sync_data(self) -> bool:
        """Upload dataset to Hub."""
        dataset_path = PROJECT_ROOT / "data" / "llm" / "train_merged.jsonl"
        return self.hub.sync_dataset(dataset_path)

    # ------------------------------------------------------------------
    # Status dashboard
    # ------------------------------------------------------------------

    def status(self) -> str:
        """Generate a rich status report."""
        lines = []
        lines.append("=" * 60)
        lines.append("STEEX LFM Training Pipeline")
        lines.append("=" * 60)

        # Progress
        target = self.config["training"].get("target_steps", 3000)
        pct = (self.state["total_steps"] / max(target, 1)) * 100
        bar_filled = int(30 * min(pct / 100, 1))
        bar = "#" * bar_filled + "-" * (30 - bar_filled)

        lines.append(f"\nProgress: [{bar}] {pct:.0f}%")
        lines.append(f"  Steps: {self.state['total_steps']:,} / {target:,}")
        lines.append(f"  Time:  {self.state['total_hours']:.1f}h total")

        best = self.state.get("best_loss")
        lines.append(f"  Best loss: {best:.4f}" if best else "  Best loss: N/A")
        lines.append(f"  Complete: {'Yes' if self.state.get('training_complete') else 'No'}")

        # Platform quotas
        lines.append(f"\nPlatform Quotas:")
        usage = self.state.get("usage", {})
        for name, adapter in sorted(self.platforms.items(), key=lambda x: x[1].config.priority):
            remaining = adapter.quota_remaining(usage)
            auto = "auto" if adapter.can_auto_dispatch() else "manual"
            lines.append(f"  {name:12s} {adapter.config.gpu:6s}  {remaining:5.1f}h remaining  [{auto}]")

        # Next platform
        next_p = self._select_next_platform()
        if next_p:
            lines.append(f"\nNext: {next_p.name} ({next_p.config.gpu})")
        else:
            lines.append("\nNext: No platforms available")

        # Recent sessions
        sessions = self.state.get("sessions", [])
        if sessions:
            lines.append(f"\nRecent Sessions (last 5):")
            for s in sessions[-5:]:
                ts = s.get("timestamp", "")[:16]
                promoted = "+" if s.get("promoted") else " "
                lines.append(
                    f"  {ts} {s['platform']:10s} {s['steps']:>5d} steps  "
                    f"loss={s['loss']:.4f} {promoted} {s['duration_minutes']:.0f}min"
                )

        # Hub info
        lines.append(f"\nHub: {self.config['hub']['model_repo']}")
        lines.append(f"Data: {self.config['hub']['dataset_repo']}")

        lines.append("=" * 60)
        return "\n".join(lines)


def _notify(message: str) -> None:
    """Send a macOS notification."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "STEEX Training" sound name "Glass"'],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(f"Notification failed: {e}")
    logger.info(f"[NOTIFY] {message}")
