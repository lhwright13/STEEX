"""Cross-platform training orchestrator.

Tracks training progress across Colab/Kaggle/Lightning/Modal,
manages the HF Hub checkpoint relay, and suggests next steps.

Usage:
    python -m src.llm.orchestrator status
    python -m src.llm.orchestrator next
    python -m src.llm.orchestrator history
"""

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Free tier limits (approximate)
PLATFORM_LIMITS = {
    "colab": {
        "gpu": "T4 16GB",
        "session_hours": 12,
        "weekly_hours": 40,
        "notes": "Best for initial training. Upload data via file picker.",
    },
    "kaggle": {
        "gpu": "T4 x2 16GB",
        "session_hours": 12,
        "weekly_hours": 30,
        "notes": "Best for continued training. Use Kaggle Secrets for HF_TOKEN.",
    },
    "lightning": {
        "gpu": "T4 16GB",
        "session_hours": 4,
        "monthly_hours": 22,
        "notes": "Persistent environment. Good for iterative experiments.",
    },
    "modal": {
        "gpu": "Various",
        "monthly_credits": 30,  # dollars
        "notes": "Pay-per-second. ~6h of A100 or ~15h of T4 on free credits.",
    },
}

STATE_FILE = Path(__file__).parent.parent.parent / "data" / "llm" / "training_state.json"


class TrainingOrchestrator:
    """Track and coordinate cross-platform training."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or STATE_FILE
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load training state from disk."""
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        return {
            "sessions": [],
            "total_steps": 0,
            "total_hours": 0,
            "best_loss": None,
            "hub_repo": None,
            "model_name": "LiquidAI/LFM2.5-1.2B-Base",
            "created": datetime.now().isoformat(),
        }

    def _save_state(self):
        """Persist training state."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def record_session(
        self,
        platform: str,
        steps: int,
        duration_minutes: float,
        final_loss: float,
        checkpoint_pushed: bool = False,
    ):
        """Record a completed training session."""
        session = {
            "platform": platform,
            "steps": steps,
            "duration_minutes": duration_minutes,
            "final_loss": final_loss,
            "checkpoint_pushed": checkpoint_pushed,
            "timestamp": datetime.now().isoformat(),
        }
        self.state["sessions"].append(session)
        self.state["total_steps"] += steps
        self.state["total_hours"] += duration_minutes / 60

        if self.state["best_loss"] is None or final_loss < self.state["best_loss"]:
            self.state["best_loss"] = final_loss

        self._save_state()
        logger.info(f"Recorded session: {platform}, {steps} steps, loss={final_loss:.4f}")

    def get_platform_usage(self) -> Dict[str, float]:
        """Get hours used per platform this week."""
        week_ago = datetime.now() - timedelta(days=7)
        usage = {p: 0.0 for p in PLATFORM_LIMITS}

        for session in self.state.get("sessions", []):
            ts = datetime.fromisoformat(session["timestamp"])
            if ts > week_ago:
                platform = session["platform"]
                if platform in usage:
                    usage[platform] += session["duration_minutes"] / 60

        return usage

    def suggest_next_platform(self) -> str:
        """Suggest which platform to use next based on remaining quotas."""
        usage = self.get_platform_usage()

        # Calculate remaining hours
        remaining = {}
        for platform, limits in PLATFORM_LIMITS.items():
            weekly = limits.get("weekly_hours", limits.get("monthly_hours", 30) / 4)
            remaining[platform] = max(0, weekly - usage.get(platform, 0))

        # Sort by most remaining time
        best = max(remaining, key=remaining.get)
        return best

    def status(self) -> str:
        """Generate a status report."""
        lines = ["## STEEX LFM Training Status", ""]
        lines.append(f"Model: {self.state.get('model_name', 'N/A')}")
        lines.append(f"Hub repo: {self.state.get('hub_repo', 'Not configured')}")
        lines.append(f"Total steps: {self.state.get('total_steps', 0):,}")
        lines.append(f"Total training time: {self.state.get('total_hours', 0):.1f}h")
        best = self.state.get("best_loss")
        lines.append(f"Best loss: {best:.4f}" if best else "Best loss: N/A")
        lines.append("")

        # Platform usage this week
        usage = self.get_platform_usage()
        lines.append("### Platform Usage (this week)")
        for platform, hours in usage.items():
            limits = PLATFORM_LIMITS[platform]
            weekly = limits.get("weekly_hours", limits.get("monthly_hours", 30) / 4)
            bar_filled = int(20 * min(hours / weekly, 1))
            bar = "#" * bar_filled + "-" * (20 - bar_filled)
            lines.append(f"  {platform:10s} [{bar}] {hours:.1f}h / {weekly:.0f}h")

        # Suggestion
        lines.append("")
        next_platform = self.suggest_next_platform()
        lines.append(f"### Recommended next: {next_platform}")
        lines.append(f"  {PLATFORM_LIMITS[next_platform]['notes']}")

        # Recent sessions
        sessions = self.state.get("sessions", [])
        if sessions:
            lines.append("")
            lines.append("### Recent Sessions")
            for s in sessions[-5:]:
                ts = s["timestamp"][:16]
                lines.append(
                    f"  {ts} | {s['platform']:10s} | {s['steps']:>5d} steps | "
                    f"loss={s['final_loss']:.4f} | {s['duration_minutes']:.0f}min"
                )

        return "\n".join(lines)

    def history(self) -> List[dict]:
        """Return all training sessions."""
        return self.state.get("sessions", [])


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="STEEX Training Orchestrator")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show training status")
    sub.add_parser("next", help="Suggest next platform")
    sub.add_parser("history", help="Show training history")

    init_p = sub.add_parser("init", help="Initialize training state")
    init_p.add_argument("--hub-repo", required=True, help="HF Hub repo for checkpoints")

    record_p = sub.add_parser("record", help="Record a completed session")
    record_p.add_argument("--platform", required=True, choices=PLATFORM_LIMITS.keys())
    record_p.add_argument("--steps", type=int, required=True)
    record_p.add_argument("--duration", type=float, required=True, help="Duration in minutes")
    record_p.add_argument("--loss", type=float, required=True)
    record_p.add_argument("--pushed", action="store_true")

    args = parser.parse_args()
    orch = TrainingOrchestrator()

    if args.command == "status":
        print(orch.status())
    elif args.command == "next":
        platform = orch.suggest_next_platform()
        print(f"Recommended: {platform}")
        print(f"  {PLATFORM_LIMITS[platform]['notes']}")
    elif args.command == "history":
        for s in orch.history():
            print(json.dumps(s, indent=2))
    elif args.command == "init":
        orch.state["hub_repo"] = args.hub_repo
        orch._save_state()
        print(f"Initialized with hub repo: {args.hub_repo}")
    elif args.command == "record":
        orch.record_session(args.platform, args.steps, args.duration, args.loss, args.pushed)
        print("Session recorded.")
        print(orch.status())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
