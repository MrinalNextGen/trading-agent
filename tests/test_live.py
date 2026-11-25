"""Live-market helpers, tested with mocked Upstox / Google responses (no network, no real token)."""
import base64
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading_agent import gsheets, live, upstox


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def fake_token(exp: datetime) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc({'sub': 'X', 'exp': int(exp.timestamp())})}.sig"


def test_login_link_and_code_parsing():
    url = upstox.login_url("abc-123", "https://127.0.0.1:5000/")
    assert url == ("https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=abc-123"
                   "&redirect_uri=https%3A%2F%2F127.0.0.1%3A5000%2F")
    assert upstox.extract_code("https://127.0.0.1:5000/?code=Xy-9Z&state=1") == "Xy-9Z"
    assert upstox.extract_code("  Xy-9Z ") == "Xy-9Z"


def test_token_expiry_is_read_from_the_token():
    assert upstox.token_is_valid(fake_token(datetime.now() + timedelta(hours=3)))
    assert not upstox.token_is_valid(fake_token(datetime.now() - timedelta(minutes=1)))
    assert not upstox.token_is_valid("")


def test_quotes_are_parsed(monkeypatch):
    payload = {"status": "success", "data": {
        "NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 1386.5, "net_change": 6.5,
                            "volume": 1234, "timestamp": "2026-11-25T10:15:00+05:30",
                            "ohlc": {"open": 1380.0, "high": 1390.0, "low": 1370.0, "close": 1385.0}},
        "NSE_EQ:ETERNAL": {"instrument_token": "NSE_EQ|INE758T01015", "last_price": 362.0, "net_change": -3.0,
                           "volume": 99, "ohlc": {"open": 365.0, "high": 366.0, "low": 360.0, "close": 361.0}},
    }}
    seen = {}

    def fake_get(url, headers, timeout, params):
        seen.update(url=url, auth=headers["Authorization"], keys=params["instrument_key"])
        return FakeResp(200, payload)

    monkeypatch.setattr(upstox.requests, "get", fake_get)
    keys = {"RELIANCE.NS": "NSE_EQ|INE002A01018", "ZOMATO.NS": "NSE_EQ|INE758T01015"}
    q = upstox.fetch_quotes("tok", keys).set_index("Ticker")
    assert seen["url"] == upstox.QUOTES_URL and seen["auth"] == "Bearer tok"
    assert seen["keys"] == "NSE_EQ|INE002A01018,NSE_EQ|INE758T01015"
    assert q.loc["RELIANCE.NS", "LTP"] == 1386.5
    assert q.loc["RELIANCE.NS", "Change %"] == pytest.approx(6.5 / 1380.0 * 100)
    assert q.loc["ZOMATO.NS", "Symbol"] == "ETERNAL" and q.loc["ZOMATO.NS", "Day High"] == 366.0


def test_expired_token_gives_a_clear_message(monkeypatch):
    monkeypatch.setattr(upstox.requests, "get", lambda *a, **k: FakeResp(401, {"status": "error"}))
    with pytest.raises(upstox.UpstoxError, match="log in again"):
        upstox.fetch_quotes("old", {"RELIANCE.NS": "NSE_EQ|INE002A01018"})


def test_five_minute_high_low_like_the_notebooks():
    store = {}
    q = lambda p: pd.DataFrame({"Symbol": ["RELIANCE"], "LTP": [p]})  # noqa: E731
    t0 = datetime(2026, 11, 25, 10, 1)
    upstox.update_five_minute_bars(store, q(100.0), t0)
    upstox.update_five_minute_bars(store, q(103.0), t0 + timedelta(minutes=1))
    out = upstox.update_five_minute_bars(store, q(99.0), t0 + timedelta(minutes=2))
    assert (out.loc[0, "5m High"], out.loc[0, "5m Low"], out.loc[0, "5m Candle"]) == (103.0, 99.0, "10:00")
    out = upstox.update_five_minute_bars(store, q(101.0), t0 + timedelta(minutes=4))  # 10:05 -> new candle
    assert (out.loc[0, "5m High"], out.loc[0, "5m Low"], out.loc[0, "5m Candle"]) == (101.0, 101.0, "10:05")


def test_live_table_joins_quotes_and_signals():
    dates = pd.bdate_range("2024-01-01", periods=400)
    close = pd.Series(np.linspace(100, 200, 400), index=dates)
    prices = {"RELIANCE.NS": pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close})}
    status = live.strategy_status_table(prices, ["RELIANCE.NS", "MISSING.NS"])
    assert list(status["Ticker"]) == ["RELIANCE.NS"]
    quotes = pd.DataFrame({"Ticker": ["RELIANCE.NS"], "Symbol": ["RELIANCE"], "LTP": [210.0], "Change %": [1.0],
                           "Day High": [211.0], "Day Low": [205.0], "Volume": [10], "5m High": [210.5],
                           "5m Low": [209.0], "5m Candle": ["10:05"]})
    table = live.build_live_table(quotes, status)
    assert list(table.columns) == live.LIVE_COLUMNS
    expected = (210.0 / status.loc[0, "200 DMA"] - 1) * 100
    assert table.loc[0, "vs 200 DMA %"] == pytest.approx(expected)


def test_google_sheet_push_via_web_app_link(monkeypatch):
    monkeypatch.setattr(gsheets, "load_env", lambda: None)
    monkeypatch.setenv("GOOGLE_SHEETS_WEBAPP_URL", "https://script.google.com/macros/s/abc/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_WEBAPP_TOKEN", "word")
    sent = {}

    def fake_post(url, timeout, json):
        sent.update(url=url, body=json)
        return FakeResp(200, {"ok": True})

    monkeypatch.setattr(gsheets.requests, "post", fake_post)
    msg = gsheets.push(pd.DataFrame({"Symbol": ["TCS"], "LTP": [3500.5]}), tab="Live")
    assert "1 rows" in msg
    assert sent["body"] == {"token": "word", "sheet": "Live", "rows": [["Symbol", "LTP"], ["TCS", 3500.5]]}
