"""pmcloud command line (what GitHub Actions runs; works locally too).

    python -m pmcloud refresh [--floor 1000]     snapshot, marks, settlement, fair values, alerts -> site data
    python -m pmcloud cycle [--dry]              refresh + Claude's daily engine cycle (trades book B)
    python -m pmcloud scan                       refresh + engine evaluation without trading
    python -m pmcloud trade --book a open <market> buy|sell <usd> [--limit PX] [--dry]
    python -m pmcloud trade --book a close <pid|market> [--limit PX] [--dry]
    python -m pmcloud dispatch                   route a repository_dispatch (env DISPATCH_TYPE, PAYLOAD)
    python -m pmcloud publish                    rebuild site data from the saved snapshot (no network)
"""

import argparse
import json
import logging
import os
import sys
import time

import pandas as pd

from . import anomalies, engine, markets, paths, publish
from . import book as B

log = logging.getLogger("pmcloud")


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def do_trade(t: dict, rows: list[dict], now: pd.Timestamp) -> dict:
    """A manual paper trade requested from the phone or the command line; the outcome is published in meta.json."""
    key = str(t.get("book", "a")).lower()
    path = paths.BOOKS.get(key, paths.BOOKS["a"])
    op = t.get("op")
    out = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "op": op, "book": key, "request": t}
    try:
        lim = t.get("limit")
        lim = float(lim) if lim not in (None, "") else None
        dry = bool(t.get("dry"))
        if op == "open":
            r = B.open_position(path, str(t["market"]), str(t["side"]), float(t["usd"]), lim, dry=dry, rows=rows)
        elif op == "close":
            r = B.close_position(path, str(t["pid"]), lim, dry=dry)
        else:
            raise B.TradeError(f"Неизвестная операция: {op}")
        out.update(ok=True, summary=r["summary"], dry=r.get("dry", False), pid=r.get("pid"))
    except B.TradeError as e:
        out.update(ok=False, error=str(e), candidates=e.candidates)
    except (KeyError, ValueError, TypeError) as e:
        out.update(ok=False, error=f"Неверный запрос: {e}")
    except Exception as e:  # never let a bad trade request stop the refresh that follows
        log.exception("trade failed")
        out.update(ok=False, error=f"{type(e).__name__}: {e}")
    paths.LAST_ACTION.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("trade %s: %s", op, out.get("summary") or out.get("error"))
    return out


def run(mode: str, floor: float = 1000, dry: bool = False, trade: dict | None = None) -> dict:
    t0 = time.time()
    now = now_utc()
    snap = markets.build(floor)
    rows = snap["rows"]
    log.info("snapshot: %d markets in %.0fs", len(rows), snap["seconds"])
    action = do_trade(trade, rows, now) if trade else None
    for k, p in paths.BOOKS.items():
        for s in B.settle(p):
            log.info("[%s] %s", k, s["summary"])
        B.mark(p)
    held = {p["c"] for path in paths.BOOKS.values() for p in B.load(path)["open"]}
    alerts = anomalies.detect(rows, now, held)
    ft = engine.fair_table(rows, now)
    res = None
    if mode in ("cycle", "scan"):
        res = engine.cycle(rows, now, dry=dry or mode == "scan")
        log.info("engine: NAV $%.2f, %d candidates, %s %d", res["nav"], res["n_candidates"],
                 "would trade" if res["dry"] else "traded", len(res["traded"]))
        for s in res["traded"]:
            log.info("  %s", s)
    publish.build(snap, now, alerts=alerts, ft=ft, mode=mode)
    log.info("published (%s): %d markets, %d fair values, %d alerts, %.0fs", mode, len(rows), len(ft), len(alerts),
             time.time() - t0)
    return {"snapshot": snap["generated"], "n": len(rows), "engine": res, "action": action}


def main(argv):
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m pmcloud", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--floor", type=float, default=1000, help="pull markets with 7-day volume >= this")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh")
    sub.add_parser("cycle").add_argument("--dry", action="store_true")
    sub.add_parser("scan")
    sub.add_parser("dispatch")
    sub.add_parser("publish")
    t = sub.add_parser("trade")
    t.add_argument("--book", default="a", choices=["a", "b"])
    t.add_argument("--limit", type=float)
    t.add_argument("--dry", action="store_true")
    ts = t.add_subparsers(dest="op", required=True)
    o = ts.add_parser("open")
    o.add_argument("market")
    o.add_argument("side")
    o.add_argument("usd", type=float)
    ts.add_parser("close").add_argument("pid")
    a = ap.parse_args(argv)

    if a.cmd in ("refresh", "scan"):
        run(a.cmd, a.floor)
    elif a.cmd == "cycle":
        run("cycle", a.floor, dry=a.dry)
    elif a.cmd == "dispatch":
        kind = os.environ.get("DISPATCH_TYPE") or "refresh"
        payload = json.loads(os.environ.get("PAYLOAD") or "null") or {}
        log.info("dispatch %s %s", kind, payload)
        if kind == "trade":
            run("refresh", a.floor, trade=payload)
        else:
            run(kind if kind in ("cycle", "scan") else "refresh", a.floor)
    elif a.cmd == "publish":
        snap = markets.load()
        if not snap:
            raise SystemExit("нет сохранённого снимка: сначала python -m pmcloud refresh")
        now = now_utc()
        publish.build(snap, now, ft=engine.fair_table(snap["rows"], now), mode="publish", record=False)
        log.info("published from the saved snapshot (%s)", snap["generated"])
    elif a.cmd == "trade":
        req = {"book": a.book, "op": a.op, "limit": a.limit, "dry": a.dry}
        if a.op == "open":
            req.update(market=a.market, side=a.side, usd=a.usd)
        else:
            req.update(pid=a.pid)
        if a.dry:
            snap = markets.load() or markets.build(a.floor)
            out = do_trade(req, snap["rows"], now_utc())
        else:
            out = run("refresh", a.floor, trade=req)["action"]
        print(("[проверка] " if a.dry else "") + (out.get("summary") or out.get("error", "")))
        for x in out.get("candidates") or []:
            print("  " + "  ".join(f"{k}={v}" for k, v in x.items()))
        if not out.get("ok"):
            raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
