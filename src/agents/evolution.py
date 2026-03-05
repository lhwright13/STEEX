"""Agent prompt self-improvement system.

Collects meta-recommendations from agent sessions, stores them,
and periodically rewrites prompts incorporating validated suggestions.
Tool suggestions are flagged for human review, never auto-applied.

Safety constraints (modeled after config_writer.py PARAM_BOUNDS):
- Never remove safety rules from prompts
- Maximum one prompt rewrite per agent per week
- All prompt changes logged with before/after diff
- Prompt changes can be reverted via the history file
"""

import difflib
import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("steex.evolution")

# Safety phrases that must never be removed from prompts
SAFETY_PHRASES = [
    "sync_broker",
    "always call first",
    "source of truth",
    "server-side",
    "GTC stop",
    "dry-run",
    "dry_run",
    "safety",
    "stop-loss",
    "crash-proof",
]


class PromptEvolver:
    """Manages agent prompt evolution from meta-recommendations."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.agents_dir = self.data_dir / "agents"
        self.prompts_dir = self.agents_dir / "prompts"
        self.recommendations_path = self.agents_dir / "recommendations.json"
        self.history_path = self.agents_dir / "prompt_history.json"

    def _ensure_dirs(self):
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)

    def collect_meta(self, session_data: Dict[str, Any]):
        """Extract and store meta-recommendations from a completed session.

        Args:
            session_data: Full session dict (from AgentSession.save output)
        """
        self._ensure_dirs()
        recs = self._read_json(self.recommendations_path, default=[])

        for trace in session_data.get("traces", []):
            meta = trace.get("meta")
            if not meta:
                continue

            has_content = (
                meta.get("prompt_suggestions")
                or meta.get("tool_suggestions")
                or meta.get("process_suggestions")
            )
            if not has_content:
                continue

            recs.append({
                "timestamp": datetime.now().isoformat(),
                "run_id": session_data.get("run_id", ""),
                "mode": session_data.get("mode", ""),
                "agent": trace.get("role", ""),
                "prompt_suggestions": meta.get("prompt_suggestions", []),
                "tool_suggestions": meta.get("tool_suggestions", []),
                "process_suggestions": meta.get("process_suggestions", []),
                "applied": False,
            })

        self._write_json(self.recommendations_path, recs)

    def get_pending_recommendations(self, agent_name: Optional[str] = None) -> List[Dict]:
        """Get unapplied recommendations, optionally filtered by agent."""
        recs = self._read_json(self.recommendations_path, default=[])
        pending = [r for r in recs if not r.get("applied", False)]
        if agent_name:
            pending = [r for r in pending if r.get("agent") == agent_name]
        return pending

    def can_rewrite(self, agent_name: str) -> bool:
        """Check if an agent's prompt can be rewritten (max 1/week)."""
        history = self._read_json(self.history_path, default=[])
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()

        for entry in reversed(history):
            if entry.get("agent") == agent_name:
                if entry.get("timestamp", "") > cutoff:
                    return False
                break

        return True

    def rewrite_prompt(
        self,
        agent_name: str,
        current_prompt: str,
        suggestions: List[str],
        claude_bin: Optional[str] = None,
    ) -> Optional[str]:
        """Use Claude to rewrite a prompt incorporating suggestions.

        Args:
            agent_name: Agent whose prompt to rewrite
            current_prompt: The current prompt text
            suggestions: List of improvement suggestions to incorporate
            claude_bin: Path to claude CLI binary

        Returns:
            New prompt text, or None if rewrite failed or was unsafe
        """
        if not self.can_rewrite(agent_name):
            logger.warning("Skipping rewrite for %s - already rewritten this week", agent_name)
            return None

        if not suggestions:
            return None

        if not claude_bin:
            claude_bin = shutil.which("claude")
        if not claude_bin:
            logger.error("claude CLI not found, cannot rewrite prompt")
            return None

        rewrite_instruction = (
            "You are rewriting a system prompt for a trading agent. "
            "Incorporate the following improvement suggestions into the prompt. "
            "Keep ALL existing safety rules, tool references, and output schema intact. "
            "Only add or clarify instructions based on the suggestions. "
            "Do NOT remove any existing content about safety, stops, broker sync, "
            "or output format. Output ONLY the rewritten prompt text, nothing else.\n\n"
            f"CURRENT PROMPT:\n{current_prompt}\n\n"
            f"SUGGESTIONS TO INCORPORATE:\n"
        )
        for i, s in enumerate(suggestions, 1):
            rewrite_instruction += f"{i}. {s}\n"

        try:
            import os
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)

            result = subprocess.run(
                [claude_bin, "-p", rewrite_instruction, "--output-format", "text"],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )

            if result.returncode != 0:
                logger.error("Prompt rewrite failed: %s", result.stderr[:300])
                return None

            new_prompt = result.stdout.strip()

            # Safety check: verify all safety phrases are preserved
            if not self._safety_check(current_prompt, new_prompt):
                logger.error("Rewritten prompt failed safety check - safety content removed")
                return None

            return new_prompt

        except subprocess.TimeoutExpired:
            logger.error("Prompt rewrite timed out")
            return None
        except Exception as e:
            logger.error("Prompt rewrite error: %s", e)
            return None

    def apply_rewrite(self, agent_name: str, old_prompt: str, new_prompt: str):
        """Save a rewritten prompt to disk and record in history.

        Args:
            agent_name: Agent name (used as filename)
            old_prompt: Previous prompt text (for diff)
            new_prompt: New prompt text to save
        """
        self._ensure_dirs()

        # Save new prompt
        prompt_path = self.prompts_dir / f"{agent_name}.md"
        prompt_path.write_text(new_prompt)

        # Record in history
        diff = list(difflib.unified_diff(
            old_prompt.splitlines(keepends=True),
            new_prompt.splitlines(keepends=True),
            fromfile=f"{agent_name} (old)",
            tofile=f"{agent_name} (new)",
        ))

        history = self._read_json(self.history_path, default=[])
        history.append({
            "timestamp": datetime.now().isoformat(),
            "agent": agent_name,
            "diff": "".join(diff),
            "prompt_path": str(prompt_path),
        })
        self._write_json(self.history_path, history)

        # Mark applied recommendations
        recs = self._read_json(self.recommendations_path, default=[])
        for r in recs:
            if r.get("agent") == agent_name and not r.get("applied"):
                r["applied"] = True
                r["applied_at"] = datetime.now().isoformat()
        self._write_json(self.recommendations_path, recs)

        logger.info("Applied prompt rewrite for %s -> %s", agent_name, prompt_path)

    def revert_prompt(self, agent_name: str) -> bool:
        """Remove a disk prompt override, reverting to code default.

        Args:
            agent_name: Agent whose prompt to revert

        Returns:
            True if a disk override was removed
        """
        prompt_path = self.prompts_dir / f"{agent_name}.md"
        if prompt_path.exists():
            prompt_path.unlink()
            logger.info("Reverted prompt for %s (removed %s)", agent_name, prompt_path)
            return True
        return False

    def evolve_agent(
        self,
        agent_name: str,
        current_prompt: str,
        claude_bin: Optional[str] = None,
    ) -> bool:
        """Full evolution cycle for one agent.

        Collects pending suggestions, rewrites prompt, applies if safe.

        Returns:
            True if a prompt was rewritten and applied
        """
        pending = self.get_pending_recommendations(agent_name)
        if not pending:
            return False

        # Gather all prompt suggestions
        all_suggestions = []
        for rec in pending:
            all_suggestions.extend(rec.get("prompt_suggestions", []))

        if not all_suggestions:
            return False

        new_prompt = self.rewrite_prompt(
            agent_name, current_prompt, all_suggestions, claude_bin
        )
        if new_prompt is None:
            return False

        self.apply_rewrite(agent_name, current_prompt, new_prompt)
        return True

    def _safety_check(self, old_prompt: str, new_prompt: str) -> bool:
        """Verify safety-critical content is preserved in the rewritten prompt."""
        old_lower = old_prompt.lower()
        new_lower = new_prompt.lower()

        for phrase in SAFETY_PHRASES:
            # Only check phrases that existed in the original
            if phrase.lower() in old_lower and phrase.lower() not in new_lower:
                logger.warning("Safety phrase removed: '%s'", phrase)
                return False

        return True

    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default if default is not None else {}
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
