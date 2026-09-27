"""Thin read-only clients for Polymarket's public APIs (no keys, no signing)."""

import json
import time

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
_s = requests.Session()
_s.headers["User-Agent"] = "pmcloud/1.0 (paper-trading dashboard; read-only)"


def get(url, params=None, tries=5):
    for attempt in range(tries):
        try:
            r = _s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 404):
                return None
        except requests.RequestException:
            pass
        time.sleep(1.5 ** attempt)
    return None


def jl(x):
    return json.loads(x) if isinstance(x, str) else (x or [])


def market(condition_id: str) -> dict | None:
    ms = get(f"{GAMMA}/markets", [("condition_ids", condition_id)]) or []
    return ms[0] if ms else None


def market_by_id(market_id: str) -> dict | None:
    ms = get(f"{GAMMA}/markets", [("id", market_id)]) or []
    return ms[0] if ms else None


def closed_markets(ids: list[str]) -> list[dict]:
    """Gamma returns only open markets unless closed=true is passed."""
    out = []
    for i in range(0, len(ids), 50):
        q = [("condition_ids", c) for c in ids[i:i + 50]] + [("limit", 100), ("closed", "true")]
        out += get(f"{GAMMA}/markets", q) or []
    return out


def book(token: str) -> dict:
    """{'bids': [(p, size)...] best first, 'asks': [(p, size)...] best first}"""
    b = get(f"{CLOB}/book", {"token_id": token}) or {}
    return {"bids": sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True),
            "asks": sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))}


def history(token: str, hours: int = 72) -> list[float]:
    """Hourly prices of a token over the last `hours` (CLOB prices-history, 1-week window)."""
    h = get(f"{CLOB}/prices-history", {"market": token, "interval": "1w", "fidelity": 60}) or {}
    cut = time.time() - hours * 3600
    return [x["p"] for x in h.get("history", []) if x["t"] >= cut]
