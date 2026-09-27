"""Sudden changes and structural anomalies on the snapshot, with a little state between runs.

kind           meaning                                                              severity
price_jump     mid moved >= 5c since the previous run (<= 26h ago)                  2
day_move       |24h price change| >= 15c                                            2
volume_spike   24h volume >= 4x the week's daily average (and >= $50k)              1
wide_spread    liquid market with a spread >= 5c                                    1
fav_collapse   favourite was >= 0.90 in the last 3 days, now <= 0.75                3
negrisk_sell   mutually exclusive event: sum of Yes bids > 1.01 (robust to outcomes missing from the snapshot)  3
negrisk_dev    sum of Yes mids >= 1.05                                              1
Any alert on a held market gets +1 severity and held=1.
"""

import json
from datetime import timedelta

import pandas as pd

from . import paths

JUMP, DAY_MOVE, VOL_SPIKE_X, MIN_VOL24, WIDE = 0.05, 0.15, 4.0, 25_000, 0.05
COLLAPSE_FROM, COLLAPSE_TO = 0.90, 0.75
DEDUPE_H, KEEP_DAYS = 6, 7


def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _q(r) -> str:
    return (r.q or "")[:90]


def detect(rows: list[dict], now: pd.Timestamp, held: set[str]) -> list[dict]:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    d = pd.DataFrame(rows)
    d = d[d.b.notna() & d.a.notna()].copy()
    d["mid"] = (d.b + d.a) / 2
    ends = (pd.to_datetime(d.end, utc=True, errors="coerce") - now).dt.total_seconds()
    live = (d.mid.between(0.015, 0.985) & (ends.fillna(1e9) > 86400) & (d.gm == 0)) | d.c.isin(held)
    liquid = (d.v24 >= MIN_VOL24) & live
    out = []

    def add(sub, kind, sev, value, msg):
        for r in sub.itertuples():
            out.append({"ts": ts, "c": r.c, "s": r.s, "cat": r.cat, "kind": kind, "sev": sev,
                        "value": round(float(value(r)), 4), "msg": msg(r)})

    prev = _load(paths.PREV_MIDS, {})
    if prev.get("ts") and (now - pd.Timestamp(prev["ts"])).total_seconds() <= 26 * 3600:
        d["prev"] = d.c.map(prev.get("mid", {}))
        x = d[liquid & d.prev.notna() & ((d.mid - d.prev).abs() >= JUMP)]
        add(x, "price_jump", 2, lambda r: r.mid - r.prev, lambda r: f"{_q(r)}: {r.prev:.3f} -> {r.mid:.3f}")
    x = d[liquid & d.d1.notna() & (d.d1.abs() >= DAY_MOVE)]
    add(x, "day_move", 2, lambda r: r.d1, lambda r: f"{_q(r)}: за 24ч {r.d1:+.3f}, сейчас {r.mid:.3f}")
    x = d[live & (d.v24 >= 2 * MIN_VOL24) & (d.v24 >= VOL_SPIKE_X * d.v7 / 7)]
    add(x, "volume_spike", 1, lambda r: r.v24 / max(r.v7 / 7, 1),
        lambda r: f"{_q(r)}: объём 24ч ${r.v24:,.0f} против ${r.v7 / 7:,.0f}/день")
    x = d[(d.v24 >= 2 * MIN_VOL24) & ((d.a - d.b) >= WIDE) & d.mid.between(0.05, 0.95)]
    add(x, "wide_spread", 1, lambda r: r.a - r.b, lambda r: f"{_q(r)}: спред {r.a - r.b:.3f} при mid {r.mid:.3f}")

    # 3-day range of the mid for liquid markets, kept per UTC day
    days = _load(paths.MID_DAYS, {})
    today = now.strftime("%Y-%m-%d")
    keep = {(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)}
    liq_rows = d[d.v24 >= MIN_VOL24]
    for r in liq_rows.itertuples():
        h = {k: v for k, v in days.get(r.c, {}).items() if k in keep}
        lo, hi = h.get(today, [r.mid, r.mid])
        h[today] = [round(min(lo, r.mid), 4), round(max(hi, r.mid), 4)]
        days[r.c] = h
    days = {c: h for c, h in days.items() if any(k in keep for k in h)}
    hist_hi = {c: max(v[1] for k, v in h.items() if k != today) for c, h in days.items() if any(k != today for k in h)}
    hist_lo = {c: min(v[0] for k, v in h.items() if k != today) for c, h in days.items() if any(k != today for k in h)}
    d["hi3"] = d.c.map(hist_hi)
    d["lo3"] = d.c.map(hist_lo)
    fav_was = (d.hi3 >= COLLAPSE_FROM) & (d.mid <= COLLAPSE_TO)
    dog_was = (d.lo3 <= 1 - COLLAPSE_FROM) & (d.mid >= 1 - COLLAPSE_TO)
    x = d[live & (fav_was | dog_was)]
    add(x, "fav_collapse", 3, lambda r: r.mid,
        lambda r: f"{_q(r)}: фаворит сломался, диапазон 3д {r.lo3:.3f}-{r.hi3:.3f}, сейчас {r.mid:.3f}")
    paths.MID_DAYS.write_text(json.dumps(days, separators=(",", ":")), encoding="utf-8")
    paths.PREV_MIDS.write_text(json.dumps({"ts": ts, "mid": {r.c: round(r.mid, 4) for r in liq_rows.itertuples()}},
                                          separators=(",", ":")), encoding="utf-8")

    # mutually exclusive events: only the sums that missing (illiquid) outcomes cannot fake
    ev = d[(d.nr == 1) & (d.acc == 1) & (d.gm == 0)].groupby("s").agg(
        n=("c", "size"), sb=("b", "sum"), sm=("mid", "sum"), v24=("v24", "sum"), e=("e", "first"), cat=("cat", "first"))
    ev = ev[(ev.n >= 2) & (ev.v24 >= MIN_VOL24)]
    for s, r in ev.iterrows():
        if r.sb > 1.01:
            out.append({"ts": ts, "c": None, "s": s, "cat": r.cat, "kind": "negrisk_sell", "sev": 3, "value": round(r.sb - 1, 4),
                        "msg": f"{r.e[:80]}: {r.n} исходов, сумма bid Yes {r.sb:.3f} > 1, продажа всех Yes фиксирует прибыль"})
        elif r.sm >= 1.05:
            out.append({"ts": ts, "c": None, "s": s, "cat": r.cat, "kind": "negrisk_dev", "sev": 1, "value": round(r.sm - 1, 4),
                        "msg": f"{r.e[:80]}: {r.n} исходов, сумма mid Yes {r.sm:.3f}"})

    for a in out:
        a["held"] = int(a["c"] in held)
        a["sev"] += a["held"]
    # de-duplicate against the recent ledger and append
    recent = []
    try:
        recent = [json.loads(line) for line in paths.ALERTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        pass
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = [a for a in recent if a["ts"] >= cut]
    dcut = (now - timedelta(hours=DEDUPE_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = {(a.get("c") or a.get("s"), a["kind"]) for a in recent if a["ts"] >= dcut}
    new = [a for a in out if ((a["c"] or a["s"]), a["kind"]) not in seen]
    recent += new
    paths.ALERTS.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in recent), encoding="utf-8")
    return sorted(recent, key=lambda a: (a["held"], a["ts"], a["sev"]), reverse=True)
