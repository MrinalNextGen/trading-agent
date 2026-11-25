"""Push a table to Google Sheets — with a service-account JSON, or with just a web-app link."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

from .settings import ROOT, env_str, load_env


def _rows(df: pd.DataFrame) -> list[list]:
    def clean(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, (pd.Timestamp, datetime, date)):
            return str(v)
        if hasattr(v, "item"):
            return v.item()
        return v
    return [list(map(str, df.columns))] + [[clean(v) for v in row] for row in df.itertuples(index=False)]


def available_method() -> str | None:
    """'webapp', 'service_account' or None (nothing configured)."""
    load_env()
    if env_str("GOOGLE_SHEETS_WEBAPP_URL"):
        return "webapp"
    sa = Path(env_str("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
    if not sa.is_absolute():
        sa = ROOT / sa
    if sa.exists() and (env_str("GOOGLE_SHEET_ID") or env_str("GOOGLE_SHEET_NAME")):
        return "service_account"
    return None


def push(df: pd.DataFrame, tab: str | None = None) -> str:
    """Overwrite the tab with the table. Returns a short status message."""
    load_env()
    tab = tab or env_str("GOOGLE_SHEET_TAB", "Live") or "Live"
    rows = _rows(df)
    method = available_method()
    if method == "webapp":
        resp = requests.post(env_str("GOOGLE_SHEETS_WEBAPP_URL"), timeout=30, json={
            "token": env_str("GOOGLE_SHEETS_WEBAPP_TOKEN"), "sheet": tab, "rows": rows})
        if resp.status_code >= 400 or "forbidden" in resp.text.lower():
            raise RuntimeError(f"Web app replied {resp.status_code}: {resp.text[:150]}")
        return f"Sent {len(rows) - 1} rows to '{tab}' via the web-app link"
    if method == "service_account":
        import gspread  # optional dependency

        sa = Path(env_str("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
        client = gspread.service_account(filename=str(sa if sa.is_absolute() else ROOT / sa))
        book = (client.open_by_key(env_str("GOOGLE_SHEET_ID")) if env_str("GOOGLE_SHEET_ID")
                else client.open(env_str("GOOGLE_SHEET_NAME")))
        try:
            ws = book.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = book.add_worksheet(title=tab, rows=max(len(rows) + 10, 100), cols=max(len(rows[0]) + 2, 10))
        ws.clear()
        ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
        return f"Sent {len(rows) - 1} rows to '{tab}' with the service account"
    raise RuntimeError("Google Sheets is not set up: add GOOGLE_SHEETS_WEBAPP_URL or a service_account.json in .env")
