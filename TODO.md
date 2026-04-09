# STEEX TODO

## Performance: Screen mode (5+ min → target <90s)

- [ ] **P1: Parallelize SEC insider scan** — `InsiderScanner.scan()` fetches 200 filings serially. Thread pool (10 workers) for HTTP fetches
- [ ] **P2: Parallelize stage 2 MA checks** — `check_trend_alignment` loops per-ticker. Batch or thread-pool it
- [ ] **P3: Parallelize stages 4+5** — Sentiment and fundamentals are independent per-ticker. Use ThreadPoolExecutor like prefetcher
- [ ] **P4: Verify prefetch cache sharing** — Screener may create new provider instances that don't share the prefetched cache. Ensure same `price_provider` instance flows through
- [ ] **P5: Remove duplicate fetches** — `refresh_data()` fetches VIX + SPY that prefetcher already fetched. Skip if cache is warm
- [ ] **P6: Short-circuit on dry-run** — Reuse last screen result if <30 min old instead of re-running full pipeline

## Architecture: Refactoring

- [ ] **R1: Split QuantManager** — 1800+ line god object. Extract: `ScreenPipeline`, `ExecutionEngine`, `ModeRunner`. QuantManager becomes a thin facade
- [ ] **R2: MCP SessionState** — Replace module-level globals (`_pipeline_result`, `_ranked`, etc.) with a `SessionState` dataclass. Add `reset()`. Prevents state corruption between tool calls
- [ ] **R3: Decompose Broker ABC** — 17 abstract methods is too many. Split into: `Broker`, `StopManager`, `OrderManager`, `AssetProvider`, `MarketCalendar`. Compose in AlpacaBroker
- [ ] **R4: Signal attribution on positions** — Replace free-text `reasons: List[str]` with structured `EntryAttribution(signal, score, weight)`. Enables direct signal→outcome mapping in learning loop
- [ ] **R5: Wire execution quality into learning** — `ExecutionQualityTracker` data should feed back to learning loop. Penalize signals that produce high-slippage entries

## Cleanup: Dead code removal

- [ ] **C1: Remove PySR** — `src/ml/` directory, `pysr_*` fields on ScreeningResult/RankedStock, config entries, `train` mode. Weight is 0.0, disabled by default, never runs
- [ ] **C2: Remove `pre_market` mode** — Legacy monolith combining screen+enter. Use `screen && enter` instead. Maintaining 3 code paths for the same flow
- [ ] **C3: Remove `full_cycle` mode** — Wrapper that chains modes. A shell script does this better
- [ ] **C4: Collapse dual prompt system** — `src/agents/prompts/*.py` (code) + `data/agents/prompts/*.md` (disk overrides). If not actively using prompt evolution, collapse to code-only
- [ ] **C5: Move `src/data/historical.py`** — If only used by backtest engine, move into `src/backtest/`

## Infrastructure

- [ ] **I1: Add CacheManager** — Expose cache stats (size, hit rate), selective invalidation, warm-by-pattern. Currently cache is invisible
- [ ] **I2: Dashboard decision** — `dashboard/` has routes but no tests and reads stale JSON files. Either invest in real-time (websocket) or remove it
