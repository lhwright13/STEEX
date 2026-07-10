"""One-off data repair for the 07-07/07-08 phantom-trade incident (task C).

What happened: transient/partial broker reads during the 07-07 selloff made
sync_from_broker believe the whole book had been liquidated. It recorded
phantom "exits" for every position (twice), re-created the positions with
score=0 and fresh entry dates, and a double-sell race briefly took JBL short
(-9), producing negative-share trade rows.

What this does (DRY-RUN by default; pass --apply to write):
  1. Back up data/trades.json and data/positions.json to data/backups/.
  2. Pull FILLED SELL orders from the Alpaca paper account (closed orders).
  3. Keep a trade row only if a matching broker fill exists (ticker + qty
     within 1 share + exit within 3 days). Everything else — negative shares,
     score-0 sync fabrications, duplicate exits of never-sold positions — is
     moved to data/trades_quarantine.json with the reason attached.
  4. For live positions whose score==0 ("synced from broker" re-creations),
     restore entry_date / entry_price / score / reasons / high_since_entry
     from the richest quarantined row for that ticker (those rows carry the
     ORIGINAL position metadata). current_stop / shares stay as-is (live).

Run:  venv/bin/python scripts/repair_trades_20260709.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = Path("data")
TRADES = DATA / "trades.json"
POSITIONS = DATA / "positions.json"
QUARANTINE = DATA / "trades_quarantine.json"
BACKUPS = DATA / "backups"


def _dt(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", ""))
        return d.replace(tzinfo=None)  # compare everything naive-local-ish; 3d tolerance absorbs tz skew
    except Exception:
        return None


def fetch_filled_sells():
    from src.broker.alpaca import AlpacaBroker
    b = AlpacaBroker(paper=True)
    fills = []
    for o in b.get_order_history(status="closed", limit=500):
        if o.get("side") == "sell" and o.get("status") == "filled" and o.get("filled_qty"):
            fills.append(o)
    return fills


def match_fill(row, fills, used):
    """A trade row is real iff an unused broker fill matches it."""
    ed = _dt(row.get("exit_date"))
    for i, f in enumerate(fills):
        if i in used:
            continue
        if f["ticker"] != row.get("ticker"):
            continue
        if abs(float(f["filled_qty"]) - float(row.get("shares") or 0)) > 1.0:
            continue
        fd = _dt(f.get("filled_at"))
        if ed and fd and abs(ed - fd) > timedelta(days=3):
            continue
        used.add(i)
        return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    trades = json.loads(TRADES.read_text())
    positions = json.loads(POSITIONS.read_text())
    fills = fetch_filled_sells()
    print(f"{len(trades)} trade rows | {len(positions)} live positions | "
          f"{len(fills)} filled broker sells fetched\n")

    used, kept, quarantined = set(), [], []
    for row in trades:
        reason = None
        if (row.get("shares") or 0) <= 0:
            reason = "negative/zero shares (double-sell race artifact)"
        else:
            fill = match_fill(row, fills, used)
            if fill is None:
                reason = "no matching filled broker sell order"
        if reason:
            quarantined.append({**row, "_quarantine_reason": reason})
            print(f"  QUARANTINE {row['ticker']:6} exit={str(row.get('exit_date'))[:10]} "
                  f"sh={row.get('shares')} score={row.get('score')} — {reason}")
        else:
            kept.append(row)

    print(f"\nkept {len(kept)} / quarantined {len(quarantined)}")

    # Restore metadata on score-0 live positions from the richest original row
    # (quarantined phantoms carry the pre-wipe metadata; kept rows can too when
    # the phantom's source got matched to an unrelated fill).
    restores = {}
    for tk, pos in positions.items():
        if pos.get("score"):
            continue
        candidates = [r for r in (quarantined + kept)
                      if r["ticker"] == tk and (r.get("score") or 0) > 0]
        if not candidates:
            continue
        src = max(candidates, key=lambda r: _dt(r.get("entry_date")) or datetime.min)
        restores[tk] = {
            "entry_date": src["entry_date"],
            "entry_price": src["entry_price"],
            "cost_basis": round(src["entry_price"] * pos.get("shares", src.get("shares", 0)), 2),
            "score": src.get("score"),
            "reasons": src.get("reasons") or ["restored from quarantined phantom exit"],
            "high_since_entry": max(
                pos.get("high_since_entry") or 0, src.get("entry_price") or 0
            ),
        }
        print(f"  RESTORE   {tk:6} entry={str(src['entry_date'])[:10]} @ "
              f"${src['entry_price']} score={src.get('score')}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit the repair.")
        return

    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    shutil.copy2(TRADES, BACKUPS / f"trades.{stamp}.json")
    shutil.copy2(POSITIONS, BACKUPS / f"positions.{stamp}.json")

    existing_q = json.loads(QUARANTINE.read_text()) if QUARANTINE.exists() else []
    QUARANTINE.write_text(json.dumps(existing_q + quarantined, indent=2, default=str))
    TRADES.write_text(json.dumps(kept, indent=2, default=str))
    for tk, patch in restores.items():
        positions[tk].update(patch)
    POSITIONS.write_text(json.dumps(positions, indent=2, default=str))
    print(f"\nAPPLIED. Backups in {BACKUPS}/*.{stamp}.json; "
          f"{len(quarantined)} rows quarantined to {QUARANTINE}")


if __name__ == "__main__":
    main()
