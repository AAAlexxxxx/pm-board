"""Paper books on the live order book (port of pmtrader/trade.py without local paths or rendering).

Book file: {start_cash, cash, open: [...], closed: [...], trades: [...]}
Sides follow the board's convention: the market is quoted in the Yes contract.
    buy   buy Yes at the ask
    sell  sell Yes at the bid = buy No at 1 - bid; the No shares are what the book holds
Execution walks the live CLOB book level by level (taker) with the taker fee rate x p x (1 - p) per share.
Default price cap: 2c worse than the best level; `limit` sets it in Yes terms.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import api, paths

SIDES = {"buy": "buy", "покупка": "buy", "long": "buy", "yes": "buy",
         "sell": "sell", "продажа": "sell", "short": "sell", "no": "sell"}
SLIP_CAP = 0.02
MIN_SHARES = 5  # Polymarket minimum order size


class TradeError(Exception):
    def __init__(self, message: str, candidates: list | None = None):
        super().__init__(message)
        self.candidates = candidates or []


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fee_per_share(price: float, rate: float) -> float:
    return rate * price * (1 - price)


def walk(levels, limit, fee_rate=0.0, budget_usd=None, max_shares=None):
    """Consume book levels best-first; returns (shares, avg_price, fee).
    Buys (budget_usd given) stop above `limit`, sells (max_shares given) stop below it."""
    shares = cost = fee = 0.0
    for p, q in levels:
        if (p > limit) if budget_usd is not None else (p < limit):
            break
        unit = p + fee_per_share(p, fee_rate)
        take = q
        if budget_usd is not None:
            take = min(take, (budget_usd - cost - fee) / unit)
        if max_shares is not None:
            take = min(take, max_shares - shares)
        if take <= 0:
            break
        shares += take
        cost += take * p
        fee += take * fee_per_share(p, fee_rate)
    return shares, (cost / shares if shares else 0.0), fee


# ---------- book file ----------
def load(path: Path) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"start_cash": paths.START_CASH, "cash": paths.START_CASH, "open": [], "closed": [],
                                    "trades": []}, indent=1), encoding="utf-8")
    book = json.loads(path.read_text(encoding="utf-8"))
    book.setdefault("closed", [])
    book.setdefault("trades", [])
    if consolidate(book):
        save(path, book)
    return book


def save(path: Path, book: dict):
    path.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")


def nav(book: dict) -> float:
    return book["cash"] + sum(p.get("last_value", p["cost"]) for p in book["open"])


# ---------- market lookup ----------
def find_market(key: str, rows: list[dict] | None = None) -> dict:
    """Gamma market dict for a condition id / market id / event link / search words (in the last snapshot)."""
    key = key.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{64}", key):
        m = api.market(key)
    elif key.isdigit():
        m = api.market_by_id(key)
    else:
        rows = rows or []
        mm = re.search(r"polymarket\.com/event/([^/?#]+)(?:/([^/?#]+))?", key)
        if mm:
            hits = [r for r in rows if r["s"] == mm.group(1)]
            if mm.group(2):
                hits = [r for r in hits if mm.group(2) in (r.get("gi") or "").lower().replace(" ", "-")] or hits
        else:
            words = key.lower().split()
            hits = [r for r in rows if all(w in f"{r['q']} {r['e']} {r.get('gi', '')}".lower() for w in words)]
        if len(hits) != 1:
            hits.sort(key=lambda r: -r["v24"])
            what = "Нет совпадений" if not hits else f"Найдено {len(hits)} рынков, уточните"
            raise TradeError(f"{what} для «{key}»",
                             [{"c": r["c"], "q": r["q"], "v24": r["v24"], "b": r["b"], "a": r["a"]} for r in hits[:15]])
        m = api.market(hits[0]["c"])
    if not m:
        raise TradeError(f"Рынок не найден или уже закрыт: {key}")
    return m


def _info(m: dict) -> dict:
    ev = (m.get("events") or [{}])[0]
    fs = m.get("feeSchedule") or {}
    return {"c": m["conditionId"], "id": m.get("id"), "q": m.get("question"), "e": ev.get("title") or "",
            "s": ev.get("slug") or m.get("slug") or "", "tokens": api.jl(m.get("clobTokenIds")),
            "outcomes": api.jl(m.get("outcomes")), "fee": float(fs.get("rate") or 0) if m.get("feesEnabled") else 0.0,
            "end": m.get("endDate"), "accepting": bool(m.get("acceptingOrders"))}


# ---------- positions ----------
def _lot(p: dict) -> dict:
    return {k: p[k] for k in ("opened", "qty", "px", "tpx", "fee", "cost")}


def _merge_into(pos: dict, add: dict):
    """Add a fill to an open position: contracts, cost and fees add up, the entry price becomes the
    contract-weighted average, the open date stays the first one; each fill is kept in `lots`."""
    lots = pos.get("lots") or [_lot(pos)]
    qty = pos["qty"] + add["qty"]
    tpx = (pos["qty"] * pos["tpx"] + add["qty"] * add["tpx"]) / qty
    pos.update(qty=round(qty, 4), tpx=round(tpx, 5), px=round(tpx if pos["side"] == "buy" else 1 - tpx, 5),
               fee=round(pos["fee"] + add["fee"], 4), cost=round(pos["cost"] + add["cost"], 4),
               lots=lots + [_lot(add)], last_add=add["opened"])
    pos.pop("last_value", None)
    pos.pop("last_mark_ts", None)


def consolidate(book: dict) -> int:
    keep, n = {}, 0
    for p in list(book["open"]):
        key = (p["c"], p["side"])
        if key in keep:
            _merge_into(keep[key], p)
            book["open"].remove(p)
            n += 1
        else:
            keep[key] = p
    return n


def open_position(path: Path, key: str, side_word: str, usd: float, limit: float | None = None, dry: bool = False,
                  rows: list[dict] | None = None, extra: dict | None = None) -> dict:
    """Walk the book for `usd` (fee included). Returns the fill; with dry=False also books it."""
    side = SIDES.get(str(side_word).lower())
    if not side:
        raise TradeError(f"Направление: buy/покупка или sell/продажа, а не «{side_word}»")
    if not usd or usd <= 0:
        raise TradeError("Сумма должна быть больше нуля")
    book = load(path)
    if usd > book["cash"] + 1e-9:
        raise TradeError(f"Недостаточно кэша: ${book['cash']:,.2f} < ${usd:,.2f}")
    mk = _info(find_market(key, rows))
    held = [p for p in book["open"] if p["c"] == mk["c"]]
    if any(p["side"] != side for p in held):
        raise TradeError("По этому рынку уже открыта позиция в противоположную сторону: сначала закройте её")
    existing = held[0] if held else None
    if not mk["accepting"]:
        raise TradeError(f"Рынок не принимает ордера: {mk['q']}")
    ti = 0 if side == "buy" else 1   # buy Yes -> Yes token asks; sell Yes -> No token asks
    ob = api.book(mk["tokens"][ti])
    if not ob["asks"]:
        raise TradeError("В стакане нет встречных заявок: сделать сделку сейчас нельзя")
    best = ob["asks"][0][0]
    cap = best + SLIP_CAP if limit is None else (limit if side == "buy" else 1 - limit)
    shares, tpx, fee = walk(ob["asks"], cap, mk["fee"], budget_usd=usd)
    if shares < MIN_SHARES:
        raise TradeError(f"Исполнилось бы {shares:.1f} контрактов (минимум {MIN_SHARES}): мало заявок до цены {cap:.3f}")
    cost = shares * tpx + fee
    ypx = tpx if side == "buy" else 1 - tpx
    pos = {"pid": uuid.uuid4().hex[:8], "c": mk["c"], "mid": mk["id"], "q": mk["q"], "e": mk["e"], "s": mk["s"], "side": side,
           "token": mk["tokens"][ti], "outcome": mk["outcomes"][ti], "qty": round(shares, 4), "px": round(ypx, 5),
           "tpx": round(tpx, 5), "fee": round(fee, 4), "cost": round(cost, 4), "opened": now_iso(), "end": mk["end"],
           "best_at_open": best, **(extra or {})}
    word = "Покупка Yes по" if side == "buy" else "Продажа Yes по"
    res = {**pos, "dry": dry, "best_yes": best if side == "buy" else 1 - best, "payout": round(shares, 4),
           "ret": (shares - cost) / cost, "fee_rate": mk["fee"],
           "summary": f"{word} {ypx:.4f}: {shares:,.1f} контрактов «{mk['q']}». Куплено {mk['outcomes'][ti]} по {tpx:.4f} "
                      f"(лучший уровень {best:.4f}), комиссия ${fee:,.2f}, итого ${cost:,.2f}; "
                      f"при выигрыше ${shares:,.2f} ({(shares - cost) / cost:+.2%})"}
    if dry:
        return res
    if cost > book["cash"] + 1e-9:
        raise TradeError(f"Недостаточно кэша: ${book['cash']:,.2f} < ${cost:,.2f}")
    book["cash"] = round(book["cash"] - cost, 4)
    if existing:
        _merge_into(existing, pos)
        res["pid"] = existing["pid"]
    else:
        book["open"].append(pos)
    book["trades"].append({"ts": pos["opened"], "action": "add" if existing else "open", "pid": res["pid"],
                           **{k: pos[k] for k in ("c", "q", "side", "qty", "px", "fee", "cost")}})
    save(path, book)
    return res


def find_position(book: dict, key: str) -> dict:
    hits = [p for p in book["open"] if p["pid"] == key or p["c"] == key]
    if not hits:
        words = key.lower().split()
        hits = [p for p in book["open"] if all(w in f"{p['q']} {p['e']}".lower() for w in words)]
    if len(hits) != 1:
        what = "Нет открытых позиций" if not hits else "Несколько позиций, укажите id"
        raise TradeError(f"{what} для «{key}»",
                         [{"pid": p["pid"], "q": p["q"], "side": p["side"], "qty": p["qty"], "px": p["px"]} for p in hits])
    return hits[0]


def _close(book: dict, p: dict, shares: float, exit_px: float, proceeds: float, reason: str) -> dict:
    frac = shares / p["qty"]
    cost = p["cost"] * frac
    rec = {**p, "qty": round(shares, 4), "cost": round(cost, 4), "exit_px": round(exit_px, 5), "proceeds": round(proceeds, 4),
           "closed": now_iso(), "reason": reason, "pnl": round(proceeds - cost, 4)}
    rec.pop("last_value", None)
    rec.pop("last_mark_ts", None)
    book["closed"].append(rec)
    if frac >= 0.9999:
        book["open"] = [x for x in book["open"] if x["pid"] != p["pid"]]
    else:  # thin book: the rest stays open
        p["qty"] = round(p["qty"] - shares, 4)
        p["cost"] = round(p["cost"] - cost, 4)
    book["cash"] = round(book["cash"] + proceeds, 4)
    book["trades"].append({"ts": rec["closed"], "action": "close", "pid": p["pid"], "q": p["q"], "side": p["side"],
                           "qty": rec["qty"], "exit_px": rec["exit_px"], "proceeds": rec["proceeds"], "pnl": rec["pnl"],
                           "reason": reason})
    return rec


def close_position(path: Path, key: str, limit: float | None = None, dry: bool = False) -> dict:
    book = load(path)
    p = find_position(book, key)
    mk = _info(find_market(p["c"]))
    ob = api.book(p["token"])
    if not ob["bids"]:
        raise TradeError("В стакане нет покупателей: закрыть сейчас нельзя")
    best = ob["bids"][0][0]
    floor = best - SLIP_CAP if limit is None else (limit if p["side"] == "buy" else 1 - limit)
    shares, tpx, fee = walk(ob["bids"], floor, mk["fee"], max_shares=p["qty"])
    if shares <= 0:
        raise TradeError(f"Нет покупателей не хуже {floor:.3f}")
    proceeds = shares * tpx - fee
    ypx = tpx if p["side"] == "buy" else 1 - tpx
    pnl = proceeds - p["cost"] * shares / p["qty"]
    res = {"pid": p["pid"], "q": p["q"], "side": p["side"], "qty": shares, "of": p["qty"], "exit_px": ypx, "tpx": tpx,
           "fee": fee, "proceeds": proceeds, "pnl": pnl, "dry": dry,
           "summary": f"Закрытие {shares:,.1f} из {p['qty']:,.1f} «{p['q']}»: продано {p['outcome']} по {tpx:.4f} "
                      f"(цена Yes {ypx:.4f}), комиссия ${fee:,.2f}, выручка ${proceeds:,.2f}, P&L ${pnl:+,.2f}"}
    if dry:
        return res
    _close(book, p, min(shares, p["qty"]), ypx, proceeds, "продажа по рынку")
    save(path, book)
    return res


def settle(path: Path) -> list[dict]:
    """Close positions whose market has resolved on-chain: $1 per share of the winning outcome."""
    book = load(path)
    if not book["open"]:
        return []
    ms = {m["conditionId"]: m for m in api.closed_markets(sorted({p["c"] for p in book["open"]}))}
    out = []
    for p in list(book["open"]):
        m = ms.get(p["c"])
        if not m or not m.get("closed"):
            continue
        prices = [float(x) for x in api.jl(m.get("outcomePrices"))]
        if m.get("umaResolutionStatus") != "resolved" and prices not in ([1.0, 0.0], [0.0, 1.0]):
            continue
        pay = prices[api.jl(m.get("outcomes")).index(p["outcome"])]
        reason = "погашение: " + ("выигрыш" if pay >= 0.99 else "проигрыш" if pay <= 0.01 else f"выплата {pay:g}")
        rec = _close(book, p, p["qty"], prices[0], p["qty"] * pay, reason)
        out.append({**rec, "summary": f"{reason}: {p['q']} -> ${p['qty'] * pay:,.2f}"})
    if out:
        save(path, book)
    return out


def mark(path: Path) -> int:
    """Store a conservative mark (best bid of the held token) on every open position."""
    book = load(path)
    n = 0
    for p in book["open"]:
        ob = api.book(p["token"])
        if ob["bids"]:
            p["last_bid"] = ob["bids"][0][0]
            p["last_value"] = round(p["qty"] * ob["bids"][0][0], 4)
            p["last_mark_ts"] = now_iso()
            n += 1
    if n:
        save(path, book)
    return n
