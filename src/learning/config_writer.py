"""Safe config writeback with bounds checking and audit trail.

Validates proposed parameter changes against defined bounds, clamps
values to safe ranges, normalizes scoring weights, and writes
validated changes back to config/config.yaml while preserving
comments and formatting.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import get_settings, CONFIG_FILE


# Maps each tunable parameter to (min, max, max_delta_per_cycle, tier).
# tier="frequent" -> weekly cadence (scoring weights)
# tier="rare"     -> monthly cadence (stops, sizing, hold days)
PARAM_BOUNDS: Dict[str, Tuple[float, float, float, str]] = {
    # Scoring weights (frequent - weekly)
    "weight_momentum":    (0.05, 0.50, 0.10, "frequent"),
    "weight_insider":     (0.05, 0.50, 0.10, "frequent"),
    "weight_volume":      (0.00, 0.30, 0.10, "frequent"),
    "weight_sentiment":   (0.00, 0.30, 0.10, "frequent"),
    "weight_fundamental": (0.00, 0.30, 0.10, "frequent"),
    "weight_options":     (0.00, 0.20, 0.10, "frequent"),
    "weight_pysr":        (0.00, 0.30, 0.10, "frequent"),
    # Exit parameters (rare - monthly, requires walk-forward)
    "initial_stop_pct":   (0.05, 0.20, 0.03, "rare"),
    "max_hold_days":      (10,   90,   10,    "rare"),
    "trail_stop_10":      (0.05, 0.25, 0.03, "rare"),
    "trail_stop_20":      (0.05, 0.25, 0.03, "rare"),
    "trail_stop_30":      (0.05, 0.25, 0.03, "rare"),
    # Sizing parameters (rare)
    "position_size_pct":  (0.02, 0.10, 0.02, "rare"),
    # Entry threshold (frequent)
    "manager_min_score_entry": (40.0, 75.0, 5.0, "frequent"),
}

WEIGHT_KEYS = [
    "weight_momentum", "weight_insider", "weight_volume",
    "weight_sentiment", "weight_fundamental", "weight_options",
]


class ConfigWriter:
    """Safe config writeback with bounds checking and audit trail."""

    def __init__(self, config_path: Optional[Path] = None, data_dir: Optional[str] = None):
        self.config_path = config_path or CONFIG_FILE
        settings = get_settings()
        self.data_dir = Path(data_dir or settings.data_dir)
        self.learning_dir = self.data_dir / "learning"
        self.history_path = self.learning_dir / "config_history.json"

    def propose_changes(
        self,
        changes: Dict[str, float],
        source: str,
        reason: str,
    ) -> Dict:
        """Validate and clamp proposed changes against bounds.

        Args:
            changes: Dict of parameter_name -> proposed_value
            source: Which analysis tool proposed this (e.g. "signal_research")
            reason: Human-readable reason for the change

        Returns:
            Dict with validated changes, clamped values, and any warnings
        """
        settings = get_settings()
        cap = getattr(settings, "learning_weight_change_cap", 0.10)

        validated = {}
        warnings = []
        skipped = []

        # Check for weight changes that need normalization
        has_weight_changes = any(k in WEIGHT_KEYS for k in changes)

        for param, proposed in changes.items():
            if param not in PARAM_BOUNDS:
                warnings.append(f"Unknown parameter '{param}' - skipped")
                skipped.append(param)
                continue

            lo, hi, max_delta, tier = PARAM_BOUNDS[param]

            # Get current value
            current = getattr(settings, param, None)
            if current is None:
                warnings.append(f"Cannot read current value of '{param}' - skipped")
                skipped.append(param)
                continue

            # Clamp to absolute bounds
            clamped = max(lo, min(hi, proposed))
            if clamped != proposed:
                warnings.append(
                    f"{param}: clamped {proposed:.4f} to [{lo}, {hi}] -> {clamped:.4f}"
                )

            # Clamp to max delta per cycle
            effective_cap = min(max_delta, cap) if param.startswith("weight_") else max_delta
            delta = clamped - current
            if abs(delta) > effective_cap:
                clamped = current + (effective_cap if delta > 0 else -effective_cap)
                warnings.append(
                    f"{param}: delta capped from {delta:+.4f} to "
                    f"{clamped - current:+.4f} (max {effective_cap})"
                )

            if abs(clamped - current) < 1e-6:
                continue

            validated[param] = {
                "current": current,
                "proposed": proposed,
                "validated": round(clamped, 6),
                "delta": round(clamped - current, 6),
                "tier": tier,
            }

        # Normalize weights if any weight changed
        if has_weight_changes:
            weight_changes = {
                k: v["validated"] for k, v in validated.items()
                if k in WEIGHT_KEYS
            }
            if weight_changes:
                normalized = self._normalize_weights(weight_changes, settings)
                for k, v in normalized.items():
                    if k in validated:
                        validated[k]["validated"] = round(v, 6)
                        validated[k]["delta"] = round(
                            v - validated[k]["current"], 6
                        )
                    elif abs(v - getattr(settings, k)) > 1e-6:
                        current_val = getattr(settings, k)
                        validated[k] = {
                            "current": current_val,
                            "proposed": v,
                            "validated": round(v, 6),
                            "delta": round(v - current_val, 6),
                            "tier": "frequent",
                        }

        return {
            "source": source,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            "changes": validated,
            "warnings": warnings,
            "skipped": skipped,
        }

    def apply_changes(
        self,
        validated: Dict,
        source: str,
        reason: str,
    ) -> Dict:
        """Write validated changes to config.yaml and append audit log.

        Args:
            validated: Output from propose_changes()
            source: Source of the change
            reason: Reason for the change

        Returns:
            Dict with applied changes and audit entry
        """
        changes = validated.get("changes", {})
        if not changes:
            return {"applied": False, "reason": "No changes to apply"}

        applied = {}
        for param, info in changes.items():
            value = info["validated"]
            success = self._rewrite_yaml_key(param, value)
            if success:
                applied[param] = {
                    "old": info["current"],
                    "new": value,
                    "delta": info["delta"],
                }

        # Write audit log
        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "reason": reason,
            "applied": applied,
            "warnings": validated.get("warnings", []),
        }
        self._append_audit_log(audit_entry)

        return {
            "applied": True,
            "count": len(applied),
            "changes": applied,
            "audit_entry": audit_entry,
        }

    def get_history(self, limit: int = 20) -> List[Dict]:
        """Read recent config change history.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of audit log entries (newest first)
        """
        if not self.history_path.exists():
            return []

        with open(self.history_path) as f:
            history = json.load(f)

        return history[-limit:][::-1]

    def _normalize_weights(
        self,
        weight_changes: Dict[str, float],
        settings=None,
    ) -> Dict[str, float]:
        """Ensure weight_* fields sum to 1.0 after any change.

        Adjusts unchanged weights proportionally to compensate.

        Args:
            weight_changes: Dict of weight parameter -> new value
            settings: Current settings (fetched if None)

        Returns:
            Dict of all weight parameters with normalized values
        """
        if settings is None:
            settings = get_settings()

        # Build full weight map: current values with overrides applied
        all_weights = {}
        for key in WEIGHT_KEYS:
            if key in weight_changes:
                all_weights[key] = weight_changes[key]
            else:
                all_weights[key] = getattr(settings, key)

        total = sum(all_weights.values())
        if total == 0 or abs(total - 1.0) < 1e-6:
            return all_weights

        # Scale all weights proportionally so they sum to 1.0
        factor = 1.0 / total
        return {k: v * factor for k, v in all_weights.items()}

    def _rewrite_yaml_key(self, key: str, value: Any) -> bool:
        """Replace a key's value in config.yaml preserving comments.

        Does a line-by-line scan, finds `key: <old_value>`, and replaces
        the value portion while keeping any inline comment.

        Args:
            key: The YAML key to update
            value: The new value

        Returns:
            True if the key was found and replaced
        """
        if not self.config_path.exists():
            return False

        lines = self.config_path.read_text().splitlines(keepends=True)
        found = False

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(f"{key}:"):
                # Parse: "key: value  # comment\n"
                after_key = stripped[len(key) + 1:].strip()

                # Split value from inline comment
                comment = ""
                if "#" in after_key:
                    # Find the comment (first # not inside a string)
                    parts = after_key.split("#", 1)
                    comment = "# " + parts[1].strip()

                # Format the value
                if isinstance(value, float):
                    if value == int(value) and abs(value) < 1000:
                        formatted = f"{value:.2f}"
                    else:
                        formatted = f"{value:.4f}".rstrip("0").rstrip(".")
                        if "." not in formatted:
                            formatted += ".0"
                elif isinstance(value, bool):
                    formatted = "true" if value else "false"
                elif isinstance(value, int):
                    formatted = str(value)
                else:
                    formatted = str(value)

                # Reconstruct line preserving indent
                indent = line[:len(line) - len(stripped)]
                if comment:
                    new_line = f"{indent}{key}: {formatted:<24s}{comment}\n"
                else:
                    new_line = f"{indent}{key}: {formatted}\n"

                lines[i] = new_line
                found = True
                break

        if found:
            self.config_path.write_text("".join(lines))

        return found

    def _append_audit_log(self, entry: Dict) -> None:
        """Append an entry to the config change history."""
        self.learning_dir.mkdir(parents=True, exist_ok=True)

        history = []
        if self.history_path.exists():
            with open(self.history_path) as f:
                history = json.load(f)

        history.append(entry)

        with open(self.history_path, "w") as f:
            json.dump(history, f, indent=2, default=str)
