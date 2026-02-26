import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT UNIQUE NOT NULL,
    mode             TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    duration_seconds REAL,
    status           TEXT NOT NULL DEFAULT 'running',
    exit_code        INTEGER,
    dry_run          INTEGER NOT NULL DEFAULT 0,
    paper            INTEGER NOT NULL DEFAULT 1,
    error_message    TEXT,
    report_path      TEXT,
    log_path         TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL REFERENCES runs(run_id),
    timestamp TEXT NOT NULL,
    action    TEXT NOT NULL,
    detail    TEXT NOT NULL,
    data_json TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    timestamp   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    action      TEXT NOT NULL,
    price       REAL,
    shares      INTEGER,
    score       REAL,
    reasons     TEXT,
    exit_reason TEXT,
    pnl_dollars REAL,
    pnl_pct     REAL
);

CREATE TABLE IF NOT EXISTS screening_funnels (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL REFERENCES runs(run_id),
    timestamp TEXT NOT NULL,
    universe  INTEGER,
    stage_1   INTEGER,
    stage_2   INTEGER,
    stage_3   INTEGER,
    stage_4   INTEGER,
    stage_5   INTEGER,
    final     INTEGER
);

CREATE TABLE IF NOT EXISTS regime_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL REFERENCES runs(run_id),
    timestamp         TEXT NOT NULL,
    regime_name       TEXT NOT NULL,
    vix_level         REAL,
    sizing_multiplier REAL,
    entries_allowed   INTEGER,
    risk_alerts       TEXT
);
"""


class DashboardDB:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or DB_PATH)
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- Writes --

    def insert_run(self, run_id, mode, started_at, dry_run=0, paper=1,
                   log_path=None):
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO runs
                   (run_id, mode, started_at, status, dry_run, paper, log_path)
                   VALUES (?, ?, ?, 'running', ?, ?, ?)""",
                (run_id, mode, started_at, dry_run, paper, log_path),
            )

    def finish_run(self, run_id, finished_at, duration_seconds, status,
                   exit_code=None, error_message=None, report_path=None):
        with self._connect() as conn:
            conn.execute(
                """UPDATE runs SET finished_at=?, duration_seconds=?,
                   status=?, exit_code=?, error_message=?, report_path=?
                   WHERE run_id=?""",
                (finished_at, duration_seconds, status, exit_code,
                 error_message, report_path, run_id),
            )

    def insert_event(self, run_id, timestamp, action, detail, data=None):
        data_json = json.dumps(data) if data else None
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO run_events (run_id, timestamp, action, detail, data_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, timestamp, action, detail, data_json),
            )

    def insert_decision(self, run_id, timestamp, ticker, action, price=None,
                        shares=None, score=None, reasons=None,
                        exit_reason=None, pnl_dollars=None, pnl_pct=None):
        reasons_json = json.dumps(reasons) if reasons else None
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions
                   (run_id, timestamp, ticker, action, price, shares, score,
                    reasons, exit_reason, pnl_dollars, pnl_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, timestamp, ticker, action, price, shares, score,
                 reasons_json, exit_reason, pnl_dollars, pnl_pct),
            )

    def insert_screening(self, run_id, timestamp, universe=None, stage_1=None,
                         stage_2=None, stage_3=None, stage_4=None,
                         stage_5=None, final=None):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO screening_funnels
                   (run_id, timestamp, universe, stage_1, stage_2, stage_3,
                    stage_4, stage_5, final)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, timestamp, universe, stage_1, stage_2, stage_3,
                 stage_4, stage_5, final),
            )

    def insert_regime(self, run_id, timestamp, regime_name, vix_level=None,
                      sizing_multiplier=None, entries_allowed=None,
                      risk_alerts=None):
        alerts_json = json.dumps(risk_alerts) if risk_alerts else None
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO regime_snapshots
                   (run_id, timestamp, regime_name, vix_level,
                    sizing_multiplier, entries_allowed, risk_alerts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, timestamp, regime_name, vix_level,
                 sizing_multiplier, entries_allowed, alerts_json),
            )

    # -- Reads --

    def get_latest_run(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_run(self, run_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_runs(self, mode=None, status=None, limit=50, offset=0):
        query = "SELECT * FROM runs WHERE 1=1"
        params = []
        if mode:
            query += " AND mode = ?"
            params.append(mode)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_run_count(self, mode=None, status=None):
        query = "SELECT COUNT(*) FROM runs WHERE 1=1"
        params = []
        if mode:
            query += " AND mode = ?"
            params.append(mode)
        if status:
            query += " AND status = ?"
            params.append(status)
        with self._connect() as conn:
            return conn.execute(query, params).fetchone()[0]

    def get_events(self, run_id):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY timestamp",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_decisions(self, run_id=None, ticker=None, limit=100, offset=0):
        query = "SELECT d.*, r.mode FROM decisions d JOIN runs r ON d.run_id = r.run_id WHERE 1=1"
        params = []
        if run_id:
            query += " AND d.run_id = ?"
            params.append(run_id)
        if ticker:
            query += " AND d.ticker = ?"
            params.append(ticker)
        query += " ORDER BY d.timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_screening(self, run_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM screening_funnels WHERE run_id = ? ORDER BY timestamp DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_screening_history(self, limit=20):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT sf.*, r.mode FROM screening_funnels sf
                   JOIN runs r ON sf.run_id = r.run_id
                   ORDER BY sf.timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_regime(self, run_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM regime_snapshots WHERE run_id = ? ORDER BY timestamp DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_regime_history(self, limit=50):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM regime_snapshots ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_regime(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM regime_snapshots ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_distinct_tickers(self):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM decisions ORDER BY ticker"
            ).fetchall()
        return [r["ticker"] for r in rows]

    def get_running_run(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def run_exists(self, run_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return row is not None
