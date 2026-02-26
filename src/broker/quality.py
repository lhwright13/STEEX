"""Execution quality tracking.

Records every order fill and computes slippage metrics to quantify
execution costs. Wraps broker.buy/sell calls in the pipeline.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import Settings, get_settings
from ..data.price import PriceProvider


@dataclass
class ExecutionRecord:
    """Record of a single execution."""

    ticker: str
    side: str              # "buy" or "sell"
    intended_price: float
    filled_price: float
    slippage_pct: float
    order_id: str
    timestamp: str


class ExecutionQualityTracker:
    """Tracks execution quality (slippage, timing).

    Records every fill and computes aggregate slippage statistics.
    Persists to data/execution_records.json.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        price_provider: Optional[PriceProvider] = None,
    ):
        self.settings = settings or get_settings()
        self.price_provider = price_provider
        self.records: List[ExecutionRecord] = []
        self._records_file = Path(self.settings.data_dir) / "execution_records.json"
        self._load()

    def _load(self) -> None:
        """Load records from file."""
        if self._records_file.exists():
            try:
                with open(self._records_file) as f:
                    data = json.load(f)
                    self.records = [ExecutionRecord(**r) for r in data]
            except (json.JSONDecodeError, TypeError):
                self.records = []

    def _save(self) -> None:
        """Save records to file."""
        self._records_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._records_file, "w") as f:
            json.dump([asdict(r) for r in self.records], f, indent=2)

    def record_execution(
        self,
        ticker: str,
        side: str,
        intended_price: float,
        filled_price: float,
        order_id: str = "",
    ) -> ExecutionRecord:
        """Record an execution and compute slippage.

        Args:
            ticker: Stock ticker
            side: "buy" or "sell"
            intended_price: Price we wanted
            filled_price: Price we got
            order_id: Broker order ID

        Returns:
            ExecutionRecord with slippage computed
        """
        if intended_price > 0:
            if side == "buy":
                # Slippage is negative when we pay more than intended
                slippage = (filled_price - intended_price) / intended_price
            else:
                # Slippage is negative when we receive less than intended
                slippage = (intended_price - filled_price) / intended_price
        else:
            slippage = 0.0

        record = ExecutionRecord(
            ticker=ticker,
            side=side,
            intended_price=intended_price,
            filled_price=filled_price,
            slippage_pct=slippage,
            order_id=order_id,
            timestamp=datetime.now().isoformat(),
        )
        self.records.append(record)
        self._save()
        return record

    def generate_report(
        self,
        last_n: Optional[int] = None,
    ) -> Dict:
        """Generate execution quality report.

        Args:
            last_n: Only analyze last N records (None = all)

        Returns:
            Dict with slippage statistics
        """
        records = self.records[-last_n:] if last_n else self.records

        if not records:
            return {
                "total_executions": 0,
                "avg_slippage_pct": 0.0,
                "max_slippage_pct": 0.0,
                "buy_avg_slippage": 0.0,
                "sell_avg_slippage": 0.0,
                "worst_fills": [],
                "acceptable_rate": 1.0,
            }

        slippages = [r.slippage_pct for r in records]
        buy_slippages = [r.slippage_pct for r in records if r.side == "buy"]
        sell_slippages = [r.slippage_pct for r in records if r.side == "sell"]

        threshold = self.settings.execution_max_acceptable_slippage
        acceptable = sum(1 for s in slippages if abs(s) <= threshold)

        # Worst fills
        sorted_records = sorted(records, key=lambda r: abs(r.slippage_pct), reverse=True)
        worst = [
            {
                "ticker": r.ticker,
                "side": r.side,
                "slippage_pct": round(r.slippage_pct * 100, 3),
                "intended": r.intended_price,
                "filled": r.filled_price,
                "timestamp": r.timestamp,
            }
            for r in sorted_records[:5]
        ]

        return {
            "total_executions": len(records),
            "avg_slippage_pct": round(sum(abs(s) for s in slippages) / len(slippages) * 100, 3),
            "max_slippage_pct": round(max(abs(s) for s in slippages) * 100, 3),
            "buy_avg_slippage": round(
                sum(abs(s) for s in buy_slippages) / len(buy_slippages) * 100
                if buy_slippages else 0.0, 3,
            ),
            "sell_avg_slippage": round(
                sum(abs(s) for s in sell_slippages) / len(sell_slippages) * 100
                if sell_slippages else 0.0, 3,
            ),
            "worst_fills": worst,
            "acceptable_rate": round(acceptable / len(records), 3),
        }
