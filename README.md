# Trading Agent

Backtests the two strategies from `hello.ipynb` across a **range of profit targets
(5.0 %, 5.1 % … 10.0 %)** for every stock list, writes a detailed **Excel report with
charts**, and shows everything in a **Streamlit app** — including a **live market page**
that works on any computer through the Upstox links (no Excel/xlwings needed).

| Strategy | Buy | Sell |
|---|---|---|
| **DMA Trend** (`process_nifty_segment`) | Close > 50 DMA > 100 DMA > 200 DMA and Close < 1.10 × 200 DMA | at the profit target, or when Close < 50 < 100 < 200 DMA |
| **CAR** (`process_nifty_car_final`) | the average price since the 10-year high has risen 10, 20, 30… days in a row | at the profit target only |

The fixed **6.28 %** target from the notebook is replaced by the sweep.

## 1. Set up (once)

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then open .env and fill in your keys
```

`.env` holds your Upstox key/secret, the daily access token, Google Sheet settings and the
backtest defaults. It is in `.gitignore`, so it never goes to GitHub.

## 2. Open the app

```bash
streamlit run app.py
```

**Backtest page** (sidebar → *Run backtest*)
- **Start year** — any year up to today; the test always runs to the latest price.
- **Capital** — default `BACKTEST_CAPITAL=100000` (₹1 lakh) from `.env`; type another amount to override.
- **Money per trade** — capital is split into equal chunks of ₹5,000–₹9,000 (auto picks one chunk per
  stock while it fits: ₹1 lakh ÷ 50 stocks → 20 chunks of ₹5,000). Every stock gets the same chunk.
- **Profit targets** — from 5.0 % to 10.0 % in 0.1 % steps (51 tests per category).
- Views: **Overview** (best target per category, 5/6/7/8/9/10 % checkpoints, a chart per category),
  **Category deep-dive** (net total amount vs target chart, which trades hit, year-wise + rolling
  profit, every trade, hits per stock) and **Excel & data** (download the full report).

**Live market page** — log in to Upstox once a day:
1. Click *Open Upstox login*, sign in.
2. The browser lands on `https://127.0.0.1:5000/?code=…` (the page won't load — that's fine). Copy that address.
3. Paste it into the app → *Save token*. The token is written to `.env` (the notebooks use it too).

Then pick a watchlist: live LTP, 5-minute high/low, day high/low and today's DMA/CAR signals,
auto-refreshing. *Download this table* gives an Excel file on any OS.

**Google Sheet (optional)** — either
- **A. Link only (no JSON file):** paste `apps_script/LivePush.gs` into your sheet's Apps Script,
  deploy it as a web app, and put its link in `GOOGLE_SHEETS_WEBAPP_URL` (+ the shared word in
  `GOOGLE_SHEETS_WEBAPP_TOKEN`), or
- **B. Service account:** keep `service_account.json` next to `app.py` and set `GOOGLE_SHEET_ID`.

## 3. Or run the backtest from the terminal

```bash
python run_backtest.py                                    # .env defaults, DMA strategy
python run_backtest.py --strategy both --start-year 2015 --capital 200000
python run_backtest.py --strategy car --categories "Nifty 50" "My Portfolio" --fill high
```

The Excel report lands in `reports/`. Sheets: **Summary**, **All Targets**, and for each category
**Sweep** (all 51 targets, checkpoints, charts), **Yearly** (year-wise and rolling profit,
account value, year × target heat-map) and **Trades** (every trade coloured by result, per-stock
summary, hits per stock per target).

## How the test works (read before trusting numbers)

- Signals use daily closes (Yahoo Finance, split/dividend adjusted) and are acted on at that close,
  exactly like the notebook. A sold stock can be bought again the next day.
- One position per stock; whole shares only. While one share costs more than a chunk, that stock's
  signals are skipped (listed in the app and the Excel).
- If more stocks signal than there are free chunks, the earlier stocks in the list get them.
- Charges default to 0.1 % per side + ₹16 per sell (`BACKTEST_CHARGES_*`).
- *Net total amount* = cash + open positions at the last close.
- The stock lists are **today's** index members, so stocks that dropped out of the index are
  missing (survivorship bias) — real results would likely be lower. Not investment advice.
- Renamed NSE symbols are mapped automatically (e.g. ZOMATO → ETERNAL, LTIM → LTM); edit the lists
  and renames in `trading_agent/universe.py`.

## Project layout

```
app.py                     Streamlit app (Backtest + Live market pages)
run_backtest.py            command-line backtest → Excel
trading_agent/
  strategies.py            DMA + CAR signals (same rules as hello.ipynb)
  engine.py                portfolio simulator (chunks, whole shares, charges)
  backtest.py              5 %→10 % sweep, yearly / rolling tables
  excel_report.py          Excel workbook with native charts
  charts.py                Plotly charts for the app
  data.py                  Yahoo Finance download + cache (data_cache/)
  upstox.py                Upstox login link, token, quotes, instrument list
  gsheets.py               Google Sheets push (web-app link or service account)
  universe.py              stock lists + symbol renames
apps_script/LivePush.gs    Google Sheets web-app receiver (no service account needed)
tests/                     python -m pytest -q
hello*.ipynb               original notebooks (keys now read from .env)
```
