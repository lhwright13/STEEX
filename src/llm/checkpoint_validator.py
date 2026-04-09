"""Checkpoint validation for the training pipeline.

Ensures checkpoints are healthy before promotion and detects
convergence to stop training at the right time.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    valid: bool
    promoted: bool  # True if this is a new best
    converged: bool
    reason: str
    current_loss: float
    best_loss: Optional[float]


class CheckpointValidator:
    """Validates checkpoints before promotion."""

    def __init__(
        self,
        max_loss_spike: float = 0.50,
        convergence_window: int = 10,
        convergence_threshold: float = 0.01,
    ):
        self.max_loss_spike = max_loss_spike
        self.convergence_window = convergence_window
        self.convergence_threshold = convergence_threshold

    def validate(
        self,
        current_loss: float,
        best_loss: Optional[float],
        loss_history: List[float],
    ) -> ValidationResult:
        """Validate a checkpoint.

        Args:
            current_loss: Loss from the just-completed session
            best_loss: Best loss seen so far (None if first session)
            loss_history: List of all previous losses in order

        Returns:
            ValidationResult with decision and reasoning
        """
        # Sanity check
        if math.isnan(current_loss) or math.isinf(current_loss):
            return ValidationResult(
                valid=False, promoted=False, converged=False,
                reason=f"Loss is {current_loss} (NaN/Inf)",
                current_loss=current_loss, best_loss=best_loss,
            )

        if current_loss < 0:
            return ValidationResult(
                valid=False, promoted=False, converged=False,
                reason=f"Negative loss ({current_loss}) indicates training error",
                current_loss=current_loss, best_loss=best_loss,
            )

        # First checkpoint — always valid and promoted
        if best_loss is None:
            return ValidationResult(
                valid=True, promoted=True, converged=False,
                reason="First checkpoint accepted as baseline",
                current_loss=current_loss, best_loss=current_loss,
            )

        # Spike detection
        spike_pct = (current_loss - best_loss) / best_loss
        if spike_pct > self.max_loss_spike:
            return ValidationResult(
                valid=False, promoted=False, converged=False,
                reason=f"Loss spiked {spike_pct:.0%} from best ({best_loss:.4f} → {current_loss:.4f})",
                current_loss=current_loss, best_loss=best_loss,
            )

        # Promotion check
        promoted = current_loss < best_loss
        new_best = current_loss if promoted else best_loss

        # Convergence check
        converged = self._check_convergence(loss_history + [current_loss])

        if promoted:
            reason = f"New best loss: {current_loss:.4f} (prev: {best_loss:.4f}, improvement: {(best_loss - current_loss) / best_loss:.1%})"
        elif converged:
            reason = f"Converged: loss plateau at {current_loss:.4f} over last {self.convergence_window} checkpoints"
        else:
            reason = f"Valid but not best: {current_loss:.4f} (best: {best_loss:.4f})"

        return ValidationResult(
            valid=True, promoted=promoted, converged=converged,
            reason=reason, current_loss=current_loss, best_loss=new_best,
        )

    def _check_convergence(self, history: List[float]) -> bool:
        """Check if training has converged.

        Returns True if loss hasn't improved by more than
        convergence_threshold over the last convergence_window entries.
        """
        if len(history) < self.convergence_window:
            return False

        recent = history[-self.convergence_window:]
        best_recent = min(recent)
        worst_recent = max(recent)

        if best_recent == 0:
            return False

        # Check if the range of recent losses is within threshold
        range_pct = (worst_recent - best_recent) / best_recent
        return range_pct < self.convergence_threshold

    def should_stop(self, loss_history: List[float]) -> bool:
        """Quick check: should we stop training entirely?

        Stop if:
        - Converged (plateau)
        - Loss is increasing over the window (diverging)
        """
        if len(loss_history) < self.convergence_window:
            return False

        recent = loss_history[-self.convergence_window:]

        # Convergence
        if self._check_convergence(loss_history):
            return True

        # Divergence: loss trend is upward over the window
        first_half = sum(recent[: len(recent) // 2]) / (len(recent) // 2)
        second_half = sum(recent[len(recent) // 2 :]) / (len(recent) - len(recent) // 2)

        if second_half > first_half * 1.1:  # 10% increase
            logger.warning(
                f"Loss diverging: first half avg={first_half:.4f}, "
                f"second half avg={second_half:.4f}"
            )
            return True

        return False
