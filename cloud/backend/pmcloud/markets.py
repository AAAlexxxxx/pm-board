"""Live market snapshot: every open Polymarket market above a weekly-volume floor (Gamma keyset pages)."""

import json
import time
from datetime import datetime, timezone

from . import api, paths
from .categories import categorize

PAGE = 100


def _f(x):
    try:
        return round(float(x), 6)
    except (TypeError, ValueError):
        return None


def compact(m: dict) -> dict:
    ev = (m.get("events") or [{}])[0]
    fs = m.get("feeSchedule") or {}
    tags = [t.get("label") for t in m.get("tags") or [] if t.get("label")]
    return {
        "id": m.get("id"), "c": m.get("conditionId"), "q": m.get("question"), "gi": m.get("groupItemTitle") or "",
        "e": ev.get("title") or "", "s": ev.get("slug") or m.get("slug") or "", "cat": categorize(tags),
        "o": api.jl(m.get("outcomes")), "tk": api.jl(m.get("clobTokenIds")),
        "b": _f(m.get("bestBid")), "a": _f(m.get("bestAsk")), "l": _f(m.get("lastTradePrice")),
        "v24": _f(m.get("volume24hr")) or 0, "v7": _f(m.get("volume1wk")) or 0, "v30": _f(m.get("volume1mo")) or 0,
        "lq": _f(m.get("liquidityNum") or m.get("liquidityClob")) or 0,
        "d1": _f(m.get("oneDayPriceChange")), "d7": _f(m.get("oneWeekPriceChange")), "end": m.get("endDate"),
        "nr": int(bool(m.get("negRisk"))), "gm": int(bool(m.get("gameStartTime") or m.get("sportsMarketType"))),
        "fee": _f(fs.get("rate")) if m.get("feesEnabled") else 0, "acc": int(bool(m.get("acceptingOrders"))),
        "cr": (m.get("createdAt") or "")[:10],
    }


def fetch(min_weekly: float = 1000) -> list[dict]:
    """Keyset pages (plain offsets stop at 2,100), sorted by 7-day volume, until the floor."""
    rows, seen, cursor = [], set(), None
    for _ in range(400):
        params = {"closed": "false", "order": "volume1wk", "ascending": "false", "include_tag": "true", "limit": PAGE}
        if cursor:
            params["after_cursor"] = cursor
        d = api.get(f"{api.GAMMA}/markets/keyset", params) or {}
        page = d.get("markets") or []
        for m in page:
            if (_f(m.get("volume1wk")) or 0) < min_weekly:
                return rows
            if not m.get("closed") and m.get("id") not in seen:
                seen.add(m.get("id"))
                rows.append(compact(m))
        cursor = d.get("next_cursor")
        if not cursor or not page:
            return rows
    return rows


def build(min_weekly: float = 1000) -> dict:
    t0 = time.time()
    rows = fetch(min_weekly)
    snap = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "min_weekly": min_weekly,
            "rows": rows, "seconds": round(time.time() - t0, 1)}
    paths.STATE.mkdir(parents=True, exist_ok=True)
    paths.SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return snap


def load() -> dict | None:
    try:
        return json.loads(paths.SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
