"""Build cloud/site/data/*.json from the snapshot, the books and the state ledgers (everything the page reads)."""

import json
import math
from datetime import timedelta

import pandas as pd

from . import engine, paths
from . import book as B
from .categories import NAMES

HISTORY_POINTS = 2000
LOG_TAIL = 200


def _clean(x):
    """NaN/inf are not JSON: turn them into null before dumping."""
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _j(path, obj):
    paths.DATA.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _days(end, now):
    t = pd.to_datetime(end, utc=True, errors="coerce") if end else pd.NaT
    return None if pd.isna(t) else round((t - now).total_seconds() / 86400, 2)


def _hold(a, b):
    ta, tb = pd.to_datetime(a, utc=True, errors="coerce"), pd.to_datetime(b, utc=True, errors="coerce")
    return None if pd.isna(ta) or pd.isna(tb) else round((tb - ta).total_seconds() / 86400, 2)


def book_view(path, now, cat_map: dict) -> dict:
    b = B.load(path)
    start = b["start_cash"]
    open_ = []
    for p in b["open"]:
        val = p.get("last_value", p["cost"])
        days = _days(p.get("end"), now)
        mark = p.get("last_bid")
        ytm = (p["qty"] - p["cost"]) / p["cost"] if p["cost"] else 0
        open_.append({
            "pid": p["pid"], "c": p["c"], "q": p["q"], "e": p.get("e"), "s": p.get("s"), "side": p["side"],
            "outcome": p.get("outcome"), "qty": p["qty"], "px": p["px"], "tpx": p.get("tpx"), "fee": p.get("fee", 0),
            "cost": p["cost"], "opened": p["opened"], "end": p.get("end"), "days": days, "value": round(val, 2),
            "pnl": round(val - p["cost"], 2), "ret": (val - p["cost"]) / p["cost"] if p["cost"] else 0,
            "mark": mark, "ymark": None if mark is None else (mark if p["side"] == "buy" else round(1 - mark, 4)),
            "max_gain": round(p["qty"] - p["cost"], 2), "ytm": ytm,
            "ann": ytm * 365 / max(days, 1) if days is not None and days > 0 else None,
            "cat": p.get("cat") or cat_map.get(p["c"], "Other"), "tier": p.get("tier"),
            "fair0": p.get("fair_at_entry"), "marked": p.get("last_mark_ts"), "lots": len(p.get("lots") or []),
        })
    open_.sort(key=lambda x: x["end"] or "")
    closed = []
    for p in b["closed"]:
        cost = p.get("cost") or 0
        closed.append({
            "pid": p["pid"], "q": p["q"], "e": p.get("e"), "s": p.get("s"), "side": p["side"], "outcome": p.get("outcome"),
            "qty": p["qty"], "px": p["px"], "cost": cost, "opened": p.get("opened"), "closed": p.get("closed"),
            "reason": p.get("reason"), "exit_px": p.get("exit_px"), "proceeds": p.get("proceeds"), "pnl": p.get("pnl", 0),
            "ret": p.get("pnl", 0) / cost if cost else 0, "hold": _hold(p.get("opened"), p.get("closed")),
            "cat": p.get("cat") or cat_map.get(p["c"], "Other"), "tier": p.get("tier"),
        })
    closed.sort(key=lambda x: x["closed"] or "", reverse=True)
    invested = sum(p["cost"] for p in b["open"])
    value = sum(x["value"] for x in open_)
    nav = b["cash"] + value
    realized = sum(p.get("pnl", 0) for p in b["closed"])
    fees = sum(p.get("fee", 0) for p in b["open"]) + sum(p.get("fee", 0) for p in b["closed"])
    kpi = {"nav": round(nav, 2), "start": start, "cash": round(b["cash"], 2), "invested": round(invested, 2),
           "value": round(value, 2), "unrealized": round(value - invested, 2), "realized": round(realized, 2),
           "fees": round(fees, 2), "ret": nav / start - 1, "max_gain_open": round(sum(x["max_gain"] for x in open_), 2),
           "n_open": len(open_), "n_closed": len(closed), "wins": sum(1 for x in closed if x["pnl"] > 0),
           "losses": sum(1 for x in closed if x["pnl"] <= 0),
           "explore": round(sum(x["cost"] for x in open_ if x["tier"] == "explore"), 2)}
    by_cat, by_week = {}, {}
    for x in open_:
        by_cat[x["cat"]] = by_cat.get(x["cat"], 0) + x["cost"]
        t = pd.to_datetime(x["end"], utc=True, errors="coerce")
        wk = "?" if pd.isna(t) else (t - timedelta(days=t.weekday())).strftime("%Y-%m-%d")
        by_week[wk] = by_week.get(wk, 0) + x["cost"]
    return {"kpi": kpi, "open": open_, "closed": closed[:100],
            "by_cat": sorted([{"k": k, "v": round(v, 2)} for k, v in by_cat.items()], key=lambda x: -x["v"]),
            "by_week": sorted([{"k": k, "v": round(v, 2)} for k, v in by_week.items()], key=lambda x: x["k"]),
            "trades": list(reversed(b.get("trades", [])))[:100]}


def nav_history(now, navs: dict, record: bool) -> list:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not paths.NAV_HISTORY.exists():   # seed: both books started at $10,000 when the first trade was booked
        first = min([t["ts"] for k in paths.BOOKS.values() for t in B.load(k).get("trades", [])] or [ts])
        paths.NAV_HISTORY.write_text(json.dumps({"ts": first, "a": paths.START_CASH, "b": paths.START_CASH}) + "\n",
                                     encoding="utf-8")
    if record:
        with paths.NAV_HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "a": navs["a"], "b": navs["b"]}) + "\n")
    pts = []
    for line in paths.NAV_HISTORY.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            pts.append([r["ts"], r["a"], r["b"]])
    return pts[-HISTORY_POINTS:]


def engine_view(now) -> dict:
    scan = _load(paths.LAST_SCAN, None)
    months = {now.strftime("%Y-%m"), (now - timedelta(days=28)).strftime("%Y-%m")}
    tail = []
    for m in sorted(months):
        p = paths.STATE / "claude_log" / f"{m}.jsonl"
        if p.exists():
            tail += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    tail = [x for x in tail if x.get("action") in ("open", "settle", "fail", "skip", "dry")]
    return {"scan": scan, "log": list(reversed(tail))[:LOG_TAIL], "rules": engine.RULES_RU}


def build(snap: dict, now, alerts: list | None = None, ft=None, mode: str = "refresh", record: bool = True):
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = snap["rows"]
    ft_map = {}
    if ft is not None and len(ft):
        ft_map = {r.c: (round(r.fairp, 4), round(float(r.edge), 4), round(float(r.ann), 3)) for r in ft.itertuples()}
    mrows = []
    for r in rows:
        x = {"c": r["c"], "q": r["q"], "e": r["e"], "s": r["s"], "k": NAMES.index(r["cat"]), "b": r["b"], "a": r["a"],
             "l": r["l"], "v24": round(r["v24"]), "v7": round(r["v7"]), "lq": round(r["lq"]), "d1": r["d1"], "d7": r["d7"],
             "end": r["end"], "nr": r["nr"], "gm": r["gm"], "fee": r["fee"], "acc": r["acc"], "cr": r["cr"]}
        if r["gi"]:
            x["gi"] = r["gi"]
        if r["o"] != ["Yes", "No"]:
            x["o"] = r["o"]
        if r["c"] in ft_map:
            x["fv"], x["edge"], x["ann"] = ft_map[r["c"]]
        mrows.append(x)
    _j(paths.DATA / "markets.json", {"generated": snap["generated"], "floor": snap["min_weekly"], "cats": NAMES,
                                     "rows": mrows})
    cat_map = {r["c"]: r["cat"] for r in rows}
    books = {k: book_view(p, now, cat_map) for k, p in paths.BOOKS.items()}
    hist = nav_history(now, {k: books[k]["kpi"]["nav"] for k in books}, record)
    _j(paths.DATA / "books.json", {"generated": ts, **books, "history": hist})
    _j(paths.DATA / "engine.json", {"generated": ts, **engine_view(now)})
    if alerts is None:
        alerts = []
        if paths.ALERTS.exists():
            alerts = [json.loads(line) for line in paths.ALERTS.read_text(encoding="utf-8").splitlines() if line.strip()]
            alerts.sort(key=lambda a: (a["held"], a["ts"], a["sev"]), reverse=True)
    _j(paths.DATA / "alerts.json", {"generated": ts, "rows": alerts[:300]})
    cal = _load(paths.CALIBRATION, {})
    _j(paths.DATA / "meta.json", {
        "generated": ts, "snapshot": snap["generated"], "mode": mode, "n_markets": len(rows), "floor": snap["min_weekly"],
        "seconds": snap.get("seconds"), "last_action": _load(paths.LAST_ACTION, None),
        "calibration": {k: cal.get(k) for k in ("built", "last_entry_day", "n_cases")},
        "nav": {k: books[k]["kpi"]["nav"] for k in books}, "n_fair": len(ft_map),
    })
