"""Where state and site data live. PMCLOUD_ROOT overrides the cloud/ folder (tests, local runs)."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("PMCLOUD_ROOT") or Path(__file__).resolve().parents[2])   # .../cloud
STATE = ROOT / "state"
SITE = ROOT / "site"
DATA = SITE / "data"
BOOKS = {"a": STATE / "book.json", "b": STATE / "book_claude.json"}
CALIBRATION = STATE / "calibration.json"
SNAPSHOT = STATE / "latest_snapshot.json"        # last market pull (not committed: 2-3 MB)
NAV_HISTORY = STATE / "nav_history.jsonl"
LAST_SCAN = STATE / "last_scan.json"
LAST_ACTION = STATE / "last_action.json"
PREV_MIDS = STATE / "prev_mids.json"
MID_DAYS = STATE / "mid_days.json"
ALERTS = STATE / "alerts.jsonl"
START_CASH = 10_000.0


def monthly(kind: str, ts: str) -> Path:
    """Append-only ledgers are split by month so a commit only touches the current file."""
    d = STATE / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ts[:7]}.jsonl"
