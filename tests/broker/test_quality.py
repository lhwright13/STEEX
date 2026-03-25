"""Tests for ExecutionQualityTracker — slippage calculation, reporting, persistence."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.broker.quality import ExecutionQualityTracker, ExecutionRecord


@pytest.fixture
def tracker(tmp_path, test_settings):
    """Tracker using a temp directory for persistence."""
    test_settings.data_dir = str(tmp_path)
    test_settings.execution_max_acceptable_slippage = 0.005  # 0.5%
    return ExecutionQualityTracker(settings=test_settings)


class TestRecordExecution:
    def test_buy_slippage_positive_when_overpay(self, tracker):
        """Buying at higher price than intended = negative slippage (cost)."""
        record = tracker.record_execution("AAPL", "buy", 150.00, 150.30)
        assert record.slippage_pct == pytest.approx(0.002, abs=1e-4)

    def test_buy_slippage_negative_when_underpay(self, tracker):
        """Buying at lower price than intended = positive (savings)."""
        record = tracker.record_execution("AAPL", "buy", 150.00, 149.70)
        assert record.slippage_pct == pytest.approx(-0.002, abs=1e-4)

    def test_sell_slippage_positive_when_receive_less(self, tracker):
        """Selling at lower price = negative slippage (cost)."""
        record = tracker.record_execution("AAPL", "sell", 150.00, 149.70)
        assert record.slippage_pct == pytest.approx(0.002, abs=1e-4)

    def test_sell_slippage_negative_when_receive_more(self, tracker):
        """Selling at higher price = positive (improvement)."""
        record = tracker.record_execution("AAPL", "sell", 150.00, 150.30)
        assert record.slippage_pct == pytest.approx(-0.002, abs=1e-4)

    def test_zero_intended_price(self, tracker):
        """Zero intended price results in 0 slippage (avoid division by zero)."""
        record = tracker.record_execution("AAPL", "buy", 0.0, 150.00)
        assert record.slippage_pct == 0.0

    def test_exact_fill(self, tracker):
        """No slippage when filled at intended price."""
        record = tracker.record_execution("AAPL", "buy", 100.00, 100.00)
        assert record.slippage_pct == 0.0

    def test_record_has_timestamp(self, tracker):
        record = tracker.record_execution("AAPL", "buy", 100.00, 100.10)
        assert record.timestamp is not None
        assert len(record.timestamp) > 0

    def test_record_has_order_id(self, tracker):
        record = tracker.record_execution("AAPL", "buy", 100.00, 100.10, order_id="ord-42")
        assert record.order_id == "ord-42"

    def test_records_accumulate(self, tracker):
        tracker.record_execution("AAPL", "buy", 100.00, 100.10)
        tracker.record_execution("MSFT", "sell", 300.00, 299.50)
        assert len(tracker.records) == 2


class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path, test_settings):
        """Records survive save → new tracker instance → load."""
        test_settings.data_dir = str(tmp_path)
        test_settings.execution_max_acceptable_slippage = 0.005

        tracker1 = ExecutionQualityTracker(settings=test_settings)
        tracker1.record_execution("AAPL", "buy", 150.00, 150.15, order_id="o1")
        tracker1.record_execution("MSFT", "sell", 300.00, 299.50, order_id="o2")

        tracker2 = ExecutionQualityTracker(settings=test_settings)
        assert len(tracker2.records) == 2
        assert tracker2.records[0].ticker == "AAPL"
        assert tracker2.records[1].ticker == "MSFT"
        assert tracker2.records[0].order_id == "o1"

    def test_load_corrupted_file(self, tmp_path, test_settings):
        """Corrupted JSON file doesn't crash — starts with empty records."""
        test_settings.data_dir = str(tmp_path)
        test_settings.execution_max_acceptable_slippage = 0.005

        records_file = tmp_path / "execution_records.json"
        records_file.write_text("not valid json{{{")

        tracker = ExecutionQualityTracker(settings=test_settings)
        assert tracker.records == []

    def test_load_missing_file(self, tmp_path, test_settings):
        """No file yet — starts with empty records."""
        test_settings.data_dir = str(tmp_path)
        test_settings.execution_max_acceptable_slippage = 0.005

        tracker = ExecutionQualityTracker(settings=test_settings)
        assert tracker.records == []


class TestGenerateReport:
    def test_empty_report(self, tracker):
        report = tracker.generate_report()
        assert report["total_executions"] == 0
        assert report["avg_slippage_pct"] == 0.0
        assert report["acceptable_rate"] == 1.0

    def test_report_with_records(self, tracker):
        tracker.record_execution("AAPL", "buy", 100.00, 100.10)
        tracker.record_execution("MSFT", "sell", 200.00, 199.80)
        tracker.record_execution("GOOG", "buy", 150.00, 150.00)

        report = tracker.generate_report()

        assert report["total_executions"] == 3
        assert report["avg_slippage_pct"] > 0
        assert report["max_slippage_pct"] >= report["avg_slippage_pct"]
        assert len(report["worst_fills"]) <= 5

    def test_report_last_n(self, tracker):
        for i in range(10):
            tracker.record_execution("AAPL", "buy", 100.00, 100.00 + i * 0.01)

        report_all = tracker.generate_report()
        report_last3 = tracker.generate_report(last_n=3)

        assert report_all["total_executions"] == 10
        assert report_last3["total_executions"] == 3

    def test_acceptable_rate(self, tracker):
        """Records within threshold count as acceptable."""
        # 0.5% threshold = $0.50 on $100
        tracker.record_execution("AAPL", "buy", 100.00, 100.40)  # 0.4% — acceptable
        tracker.record_execution("MSFT", "buy", 100.00, 101.00)  # 1.0% — not acceptable
        tracker.record_execution("GOOG", "buy", 100.00, 100.00)  # 0.0% — acceptable

        report = tracker.generate_report()
        assert report["acceptable_rate"] == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_buy_sell_split(self, tracker):
        tracker.record_execution("AAPL", "buy", 100.00, 100.20)
        tracker.record_execution("MSFT", "sell", 200.00, 199.60)

        report = tracker.generate_report()
        assert report["buy_avg_slippage"] > 0
        assert report["sell_avg_slippage"] > 0

    def test_worst_fills_sorted(self, tracker):
        tracker.record_execution("A", "buy", 100.00, 100.10)   # small
        tracker.record_execution("B", "buy", 100.00, 102.00)   # large
        tracker.record_execution("C", "buy", 100.00, 100.50)   # medium

        report = tracker.generate_report()
        worst = report["worst_fills"]
        assert worst[0]["ticker"] == "B"
