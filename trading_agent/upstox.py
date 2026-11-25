"""Upstox over plain HTTPS links — works on Mac, Windows and Linux (no Excel needed).

Links used (the same ones as in hello2–hello4.ipynb):
  login    https://api.upstox.com/v2/login/authorization/dialog
  token    https://api.upstox.com/v2/login/authorization/token
  quotes   https://api.upstox.com/v2/market-quote/quotes
  symbols  https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import os
import time
from datetime import datetime
from urllib.parse import parse_qs, quote, urlparse

import pandas as pd
import requests
from dotenv import set_key

from .settings import CACHE_DIR, ENV_PATH, env_str, load_env
from .universe import yahoo_symbol

LOGIN_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
QUOTES_URL = "https://api.upstox.com/v2/market-quote/quotes"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


class UpstoxError(RuntimeError):
    pass


def credentials() -> dict:
    load_env()
    return {
        "api_key": env_str("UPSTOX_API_KEY"),
        "api_secret": env_str("UPSTOX_API_SECRET"),
        "redirect_uri": env_str("UPSTOX_REDIRECT_URI", "https://127.0.0.1:5000/"),
        "access_token": env_str("UPSTOX_ACCESS_TOKEN"),
    }


def login_url(api_key: str, redirect_uri: str) -> str:
    return f"{LOGIN_URL}?response_type=code&client_id={quote(api_key)}&redirect_uri={quote(redirect_uri, safe='')}"


def extract_code(text: str) -> str:
    """Accepts the whole redirected URL (…/?code=AbC123) or just the code."""
    text = (text or "").strip()
    if "code=" in text:
        qs = parse_qs(urlparse(text).query) or parse_qs(text.split("?", 1)[-1])
        if qs.get("code"):
            return qs["code"][0]
    return text


def exchange_code(code: str, api_key: str, api_secret: str, redirect_uri: str) -> str:
    resp = requests.post(TOKEN_URL, timeout=20, headers={"accept": "application/json"}, data={
        "code": code, "client_id": api_key, "client_secret": api_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    try:
        body = resp.json()
    except ValueError:
        raise UpstoxError(f"Upstox replied {resp.status_code}: {resp.text[:200]}")
    if "access_token" not in body:
        errs = body.get("errors") or body
        raise UpstoxError(f"Login failed: {errs}")
    return body["access_token"]


def save_token(token: str) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    set_key(str(ENV_PATH), "UPSTOX_ACCESS_TOKEN", token)
    os.environ["UPSTOX_ACCESS_TOKEN"] = token


def token_expiry(token: str) -> datetime | None:
    """Reads the expiry time stored inside the token (no network call)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return datetime.fromtimestamp(exp) if exp else None
    except Exception:
        return None


def token_is_valid(token: str) -> bool:
    exp = token_expiry(token) if token else None
    return bool(token) and (exp is None or exp > datetime.now())


def load_instruments(max_age_hours: float = 20) -> pd.DataFrame:
    """NSE equity list with the ISIN-based keys Upstox needs (e.g. NSE_EQ|INE002A01018)."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / "upstox_NSE.json.gz"
    if not path.exists() or time.time() - path.stat().st_mtime > max_age_hours * 3600:
        resp = requests.get(INSTRUMENTS_URL, timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        df = pd.read_json(io.StringIO(fh.read()))
    df = df[df["segment"] == "NSE_EQ"]
    return df[["trading_symbol", "instrument_key", "name"]].drop_duplicates("trading_symbol")


def nse_symbol(ticker: str) -> str:
    """'ZOMATO.NS' -> 'ETERNAL' (follows the renames in universe.ALIASES)."""
    return yahoo_symbol(ticker).replace(".NS", "")


def instrument_keys(tickers: list[str], instruments: pd.DataFrame) -> tuple[dict[str, str], list[str]]:
    lookup = dict(zip(instruments["trading_symbol"], instruments["instrument_key"]))
    found, missing = {}, []
    for t in tickers:
        sym = nse_symbol(t)
        if sym in lookup:
            found[t] = lookup[sym]
        else:
            missing.append(t)
    return found, missing


def fetch_quotes(token: str, keys: dict[str, str]) -> pd.DataFrame:
    """Full market quotes for {ticker: instrument_key}. One call per 500 instruments."""
    if not token:
        raise UpstoxError("No access token — log in first.")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    by_key = {v: k for k, v in keys.items()}
    rows = []
    items = list(keys.values())
    for i in range(0, len(items), 500):
        resp = requests.get(QUOTES_URL, headers=headers, timeout=20,
                            params={"instrument_key": ",".join(items[i:i + 500])})
        if resp.status_code == 401:
            raise UpstoxError("Upstox says the access token is invalid or expired — log in again.")
        if resp.status_code != 200:
            raise UpstoxError(f"Upstox error {resp.status_code}: {resp.text[:200]}")
        for raw_key, q in (resp.json().get("data") or {}).items():
            ikey = q.get("instrument_token") or raw_key.replace(":", "|")
            ticker = by_key.get(ikey) or by_key.get(raw_key.replace(":", "|"))
            if ticker is None:  # fall back to matching the symbol after "NSE_EQ:"
                sym = raw_key.split(":")[-1]
                ticker = next((t for t in keys if nse_symbol(t) == sym), raw_key)
            ohlc = q.get("ohlc") or {}
            prev_close = (q.get("last_price") or 0) - (q.get("net_change") or 0)
            rows.append({
                "Ticker": ticker,
                "Symbol": nse_symbol(ticker) if ticker in keys else raw_key,
                "LTP": q.get("last_price"),
                "Change %": (q.get("net_change") or 0) / prev_close * 100 if prev_close else None,
                "Day Open": ohlc.get("open"),
                "Day High": ohlc.get("high"),
                "Day Low": ohlc.get("low"),
                "Volume": q.get("volume"),
                "Quote Time": q.get("timestamp"),
            })
    return pd.DataFrame(rows)


def update_five_minute_bars(store: dict, quotes: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Same 5-minute high/low logic as the notebooks, kept across refreshes in `store`."""
    now = now or datetime.now()
    bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0).strftime("%H:%M")
    hi, lo = [], []
    for sym, ltp in zip(quotes["Symbol"], quotes["LTP"]):
        s = store.get(sym)
        if ltp is None:
            hi.append(None); lo.append(None); continue  # noqa: E702
        if s is None or s["candle"] != bucket:
            s = store[sym] = {"candle": bucket, "high": ltp, "low": ltp}
        else:
            s["high"], s["low"] = max(s["high"], ltp), min(s["low"], ltp)
        hi.append(s["high"]); lo.append(s["low"])  # noqa: E702
    out = quotes.copy()
    out["5m Candle"] = bucket
    out["5m High"] = hi
    out["5m Low"] = lo
    return out
