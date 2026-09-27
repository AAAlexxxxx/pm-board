"""Claude's decision engine on the live snapshot: port of engine/live_bot.py (night run exp-102 + explore tier).

Rules: favourite side 0.85-0.99 (geopolitics 0.95-0.99), 1-150 days to end, >= $20k/day average volume, 3-day
stability (72h low >= band - 2c, range <= 5c), calibrated edge after 1c slippage and fee >= 0.75c, annualised edge
>= 25%, <= 2 positions per event, category <= 30%, geopolitics <= 10%, position <= 5% NAV and <= 5% of daily volume,
0.25 Kelly, hold to resolution. Explore tier: looser bands and thresholds, $100 lots, <= 20% NAV, tagged.
Calibration: P(win) - price by group x price band x horizon, precomputed from night_eval/cases.parquet into
state/calibration.json (engine/policy.calibrate; refresh monthly).
"""

import json
import time

import numpy as np
import pandas as pd

from . import api, paths
from . import book as B
from .categories import group

BANDS_CAL = [0.55, 0.65, 0.75, 0.8, 0.85, 0.88, 0.9, 0.92, 0.94, 0.96, 0.97, 0.98, 0.99, 0.998]
HORIZONS = [0, 7, 30, 90, 180]
MIN_DAYS, MAX_DAYS = 1, 150
EXP_SLIP = 0.01
KELLY_FRACTION, MAX_POS, LIQ = 0.25, 0.05, 0.05
MAX_PER_EVENT, MAX_CAT, MAX_GEO = 2, 0.30, 0.10
MIN_CASH, MIN_ORDER, MAX_NEW = 0.05, 50.0, 6
CORE = dict(tier="core", BANDS={"event": (0.85, 0.99), "quant": (0.85, 0.99), "geo": (0.95, 0.99)}, MIN_DAILY_VOL=20_000,
            STABLE_DROP=0.02, MAX_RANGE3=0.05, MIN_EDGE=0.0075, MIN_ANN=0.25, ENRICH=40)
EXPLORE = dict(tier="explore", BANDS={"event": (0.80, 0.99), "quant": (0.80, 0.99), "geo": (0.90, 0.99)},
               MIN_DAILY_VOL=10_000, STABLE_DROP=0.03, MAX_RANGE3=0.08, MIN_EDGE=0.0, MIN_ANN=0.10, ENRICH=150)
EXPLORE_USD, EXPLORE_MAX, EXPLORE_NEW = 100.0, 0.20, 10
SHADOW_MIN_VOL = 5_000
BOOK = paths.BOOKS["b"]
RULES_RU = {
    "Вселенная": "фаворит 0.85–0.99 (геополитика 0.95–0.99), 1–150 дней до конца, ≥ $20k/день за неделю",
    "Стабильность": "72ч по часам: минимум ≥ нижняя граница − 2¢, размах ≤ 5¢",
    "Edge": "калиброванная fair − (ask + 1¢) − комиссия ≥ 0.75¢; годовых ≥ 25%",
    "Размер": "0.25 Kelly, ≤ 5% NAV, ≤ 5% дневного объёма, ≤ 2 позиции на событие, категория ≤ 30%, геополитика ≤ 10%",
    "Explore": "полосы от 0.80 (гео 0.90), ≥ $10k/день, размах ≤ 8¢, годовых ≥ 10%; лоты $100, всего ≤ 20% NAV",
    "Выход": "держать до погашения, без стопа",
}
_HIST: dict[str, list[float]] = {}


def load_cal() -> dict:
    c = json.loads(paths.CALIBRATION.read_text(encoding="utf-8"))
    return c["cells"]


def fair(cal: dict, p: float, days: float, grp: str) -> float:
    b = int(np.digitize(p, BANDS_CAL)) - 1
    h = int(np.digitize(max(days, 1), HORIZONS, right=True)) - 1
    return min(p + cal.get(f"{grp}|{b}|{h}", 0.0), 0.999)


def frame(rows: list[dict], now: pd.Timestamp) -> pd.DataFrame:
    """Snapshot rows -> favourite-side view with the fields every rule uses."""
    d = pd.DataFrame(rows)
    d = d[(d.acc == 1) & d.b.notna() & d.a.notna() & (d.b > 0) & (d.a < 1)].copy()
    d["days"] = (pd.to_datetime(d.end, utc=True, errors="coerce") - now).dt.total_seconds() / 86400
    d["mid"] = (d.b + d.a) / 2
    d["yes_fav"] = d.mid >= 0.5
    d["fav_ask"] = np.where(d.yes_fav, d.a, 1 - d.b)
    d["fav_bid"] = np.where(d.yes_fav, d.b, 1 - d.a)
    d["grp"] = d.cat.map(group)
    d["daily_vol"] = d.v7 / 7
    d["fee_ps"] = d.fee.fillna(0) * d.fav_ask * (1 - d.fav_ask)
    return d


def price(d: pd.DataFrame, cal: dict) -> pd.DataFrame:
    d = d.copy()
    d["px"] = d.fav_ask + EXP_SLIP
    d["fairp"] = [fair(cal, p, t, g) for p, t, g in zip(d.fav_ask, d.days, d.grp)]
    d["edge"] = d.fairp - d.px - d.fee_ps
    d["ann"] = d.edge / d.px * 365 / d.days.clip(lower=1)
    return d


def fair_table(rows: list[dict], now: pd.Timestamp, cal: dict | None = None) -> pd.DataFrame:
    """Fair value for every liquid favourite (0.55-0.995, 1-150 days, >= $5k/day): the screener's edge column
    and the shadow ledger."""
    cal = cal or load_cal()
    d = frame(rows, now)
    d = d[d.fav_ask.between(0.55, 0.995) & d.days.between(MIN_DAYS, MAX_DAYS) & (d.daily_vol >= SHADOW_MIN_VOL)]
    return price(d, cal) if len(d) else d


def shadow_log(ft: pd.DataFrame, now: pd.Timestamp) -> int:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with paths.monthly("shadow", ts).open("a", encoding="utf-8") as f:
        for r in ft.itertuples():
            f.write(json.dumps({"ts": ts, "c": r.c, "cat": r.cat, "fav": "Yes" if r.yes_fav else "No",
                                "ask": round(float(r.fav_ask), 4), "bid": round(float(r.fav_bid), 4),
                                "fair": round(r.fairp, 4), "days": round(r.days, 2), "v7": r.v7, "fee": r.fee},
                               ensure_ascii=False) + "\n")
    return len(ft)


def log(rec: dict, now: pd.Timestamp):
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with paths.monthly("claude_log", ts).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, **rec}, ensure_ascii=False, default=str) + "\n")


def _history(token: str) -> list[float]:
    if token not in _HIST:
        _HIST[token] = api.history(token)
        time.sleep(0.1)
    return _HIST[token]


def candidates(d: pd.DataFrame, book: dict, cal: dict, R: dict) -> pd.DataFrame:
    """Rule filter + 72h stability check (CLOB price history) -> decision/reason per row."""
    lo = d.grp.map(lambda g: R["BANDS"][g][0])
    hi = d.grp.map(lambda g: R["BANDS"][g][1])
    held = {p["c"] for p in book["open"]}
    d = d[d.fav_ask.between(lo, hi) & d.days.between(MIN_DAYS, MAX_DAYS) & (d.daily_vol >= R["MIN_DAILY_VOL"])
          & (d.v24 >= R["MIN_DAILY_VOL"] / 2) & ~d.c.isin(held)]
    if d.empty:
        return d
    d = price(d, cal).sort_values("ann", ascending=False).head(R["ENRICH"]).copy()
    lo3, hi3 = [], []
    for r in d.itertuples():
        toks = r.tk if isinstance(r.tk, list) else []
        s = _history(toks[0 if r.yes_fav else 1]) if len(toks) == 2 else []
        lo3.append(min(s) if s else np.nan)
        hi3.append(max(s) if s else np.nan)
    d["lo3"], d["hi3"] = lo3, hi3
    blo = d.grp.map(lambda g: R["BANDS"][g][0])

    def verdict(r, b_lo):
        if pd.isna(r.lo3):
            return "reject", "нет истории цены"
        if r.lo3 < b_lo - R["STABLE_DROP"] or r.hi3 - r.lo3 > R["MAX_RANGE3"]:
            return "reject", f"нестабилен 72ч {r.lo3:.3f}–{r.hi3:.3f}"
        if r.edge < R["MIN_EDGE"]:
            return "reject", f"edge {r.edge * 100:.2f}¢ < {R['MIN_EDGE'] * 100:.2f}¢"
        if r.ann < R["MIN_ANN"]:
            return "reject", f"годовых {r.ann:.0%} < {R['MIN_ANN']:.0%}"
        return "candidate", f"edge {r.edge * 100:.2f}¢, годовых {r.ann:.0%}"

    d[["decision", "reason"]] = [verdict(r, b) for r, b in zip(d.itertuples(), blo)]
    d["tier"] = R["tier"]
    return d.sort_values(["decision", "ann"], ascending=[True, False])


def _base(r) -> dict:
    return {"c": r.c, "q": r.q, "e": r.e, "s": r.s, "cat": r.cat, "fav": "Yes" if r.yes_fav else "No",
            "fav_ask": round(float(r.fav_ask), 4), "fair": round(r.fairp, 4), "edge": round(r.edge, 4),
            "ann": round(r.ann, 3), "days": round(r.days, 1), "daily_vol": round(float(r.daily_vol)),
            "lo3": None if pd.isna(r.lo3) else round(float(r.lo3), 4),
            "hi3": None if pd.isna(r.hi3) else round(float(r.hi3), 4), "tier": r.tier}


def _open(r, usd: float, tier: str, dry: bool, rows: list[dict]) -> dict:
    side = "buy" if r.yes_fav else "sell"
    limit = min(r.fav_ask + 0.01, 0.995) if side == "buy" else max(1 - (r.fav_ask + 0.01), 0.005)
    return B.open_position(BOOK, r.c, side, round(usd, 2), limit=limit, dry=dry, rows=rows,
                           extra={"cat": r.cat, "fair_at_entry": round(r.fairp, 4), "tier": tier})


def cycle(rows: list[dict], now: pd.Timestamp, dry: bool = False) -> dict:
    """One daily cycle of Claude's book: settle, mark, shadow ledger, core trades, explore trades, mark.
    dry=True evaluates everything and trades nothing (the 'scan' mode)."""
    cal = load_cal()
    settled = [] if dry else B.settle(BOOK)
    for s in settled:
        log({"action": "settle", "summary": s["summary"], "pnl": s.get("pnl")}, now)
    if not dry:
        B.mark(BOOK)
    book = B.load(BOOK)
    d = frame(rows, now)
    n_shadow = 0 if dry else shadow_log(fair_table(rows, now, cal), now)
    scan, done = [], []
    N = B.nav(book)
    by_ev = pd.Series([p["e"] for p in book["open"]]).value_counts().to_dict() if book["open"] else {}
    by_cat, geo = {}, 0.0
    for p in book["open"]:
        by_cat[p.get("cat")] = by_cat.get(p.get("cat"), 0) + p["cost"]
        geo += p["cost"] if p.get("cat") == "Geopolitics" else 0

    def skip(base, why, action="skip"):
        scan.append({**base, "decision": action, "reason": why})
        log({"action": action, **base, "reason": why}, now)

    # core tier: Kelly-sized entries
    c = candidates(d, book, cal, CORE)
    n_new = 0
    for r in (c.itertuples() if len(c) else []):
        base = _base(r)
        if r.decision != "candidate":
            skip(base, r.reason, "reject")
            continue
        if n_new >= MAX_NEW:
            skip(base, "лимит новых сделок за цикл")
            continue
        if by_ev.get(r.e, 0) >= MAX_PER_EVENT:
            skip(base, "лимит позиций на событие")
            continue
        kelly = max((r.fairp - r.px - r.fee_ps) / (1 - r.px), 0)
        usd = min(KELLY_FRACTION * kelly, MAX_POS) * N
        room = MAX_CAT * N - by_cat.get(r.cat, 0)
        if r.cat == "Geopolitics":
            room = min(room, MAX_GEO * N - geo)
        usd = min(usd, room, LIQ * r.daily_vol, book["cash"] - MIN_CASH * N)
        if usd < MIN_ORDER:
            skip(base, f"размер ${max(usd, 0):.0f} меньше минимума")
            continue
        try:
            res = _open(r, usd, "core", dry, rows)
        except B.TradeError as e:
            skip(base, str(e), "fail")
            continue
        rec = {**base, "usd": round(usd, 2), "kelly": round(kelly, 3), "fill": res["tpx"], "fee": round(res["fee"], 4),
               "summary": res["summary"]}
        scan.append({**rec, "decision": "dry" if dry else "open", "reason": r.reason})
        log({"action": "dry" if dry else "open", **rec}, now)
        if not dry:
            book = B.load(BOOK)
        by_ev[r.e] = by_ev.get(r.e, 0) + 1
        by_cat[r.cat] = by_cat.get(r.cat, 0) + usd
        geo += usd if r.cat == "Geopolitics" else 0
        n_new += 1
        done.append(res["summary"])

    # explore tier: fixed $100 lots under a total cap
    used = sum(p["cost"] for p in book["open"] if p.get("tier") == "explore")
    c2 = candidates(d, book, cal, EXPLORE)
    n_x = 0
    for r in (c2.itertuples() if len(c2) else []):
        base = _base(r)
        if r.decision != "candidate":
            scan.append({**base, "decision": "reject", "reason": r.reason})
            continue
        if n_x >= EXPLORE_NEW or used + EXPLORE_USD > EXPLORE_MAX * N or book["cash"] - EXPLORE_USD < MIN_CASH * N:
            skip(base, "лимит explore-уровня")
            continue
        if by_ev.get(r.e, 0) >= MAX_PER_EVENT:
            skip(base, "лимит позиций на событие")
            continue
        try:
            res = _open(r, EXPLORE_USD, "explore", dry, rows)
        except B.TradeError as e:
            skip(base, str(e), "fail")
            continue
        rec = {**base, "usd": EXPLORE_USD, "fill": res["tpx"], "fee": round(res["fee"], 4), "summary": res["summary"]}
        scan.append({**rec, "decision": "dry" if dry else "open", "reason": r.reason})
        log({"action": "dry" if dry else "open", **rec}, now)
        if not dry:
            book = B.load(BOOK)
        used += EXPLORE_USD
        by_ev[r.e] = by_ev.get(r.e, 0) + 1
        n_x += 1
        done.append("[explore] " + res["summary"])

    if not dry:
        B.mark(BOOK)
    book = B.load(BOOK)
    order = {"open": 0, "dry": 0, "fail": 1, "skip": 2, "reject": 3}
    scan.sort(key=lambda x: (order.get(x["decision"], 9), -x["ann"]))
    result = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "dry": dry, "nav": round(B.nav(book), 2),
              "settled": [s["summary"] for s in settled], "shadow": n_shadow,
              "n_candidates": sum(1 for x in scan if x["decision"] != "reject"), "traded": done, "rows": scan}
    paths.LAST_SCAN.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return result
