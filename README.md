# Nifty Options Chain — Live Web App

A real-time Nifty options chain viewer powered by Zerodha Kite Connect API.
Auto-refreshes every 30 seconds using 1-minute historical data as the baseline.

## Features

- Live options chain for any Nifty expiry (auto-detected)
- Per-strike metrics: LTP, ATP (VWAP), OI, OI Change (↑/↓), Volume
- SMAs: LTP SMA-5, LTP SMA-8, Volume SMA-10
- ATP–LTP difference (positive = LTP above ATP)
- Nifty Futures: LTP, SMA-5, SMA-8, OI, OI Change, Volume, Vol SMA-10
- Auto-refresh every 30 seconds with countdown bar
- Works on cloud (Render, Railway, Heroku)

## Local Run

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Deploy to Render (Free)

1. Push this folder to a GitHub repo
2. Go to https://render.com → New Web Service
3. Connect your GitHub repo
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT`
6. Deploy — get a public URL

## Deploy to Railway

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

## Deploy to Heroku

```bash
heroku create nifty-chain-app
git push heroku main
```

## Usage

1. Open the web URL
2. Enter your Kite API Key and Access Token
3. Click **Load Expiries** — all available expiry dates populate automatically
4. Select an expiry from the dropdown
5. Set Strikes ±ATM (default 10 = 21 strikes around ATM)
6. Click **▶ Start Live Feed**

## Data Logic

- Fetches last 2 hours of 1-minute OHLCV + OI data via `kite.historical_data()`
- ATP = Volume-Weighted Average Price (VWAP) across all fetched candles
- LTP = last candle close price
- SMAs calculated over the fetched candle series
- OI Change = current candle OI minus previous candle OI
- All calculations reset each session (not persisted)

## Notes

- Access token expires daily — regenerate from Kite developer portal
- Free Render instance sleeps after inactivity; first request may be slow
- For production use, upgrade to a paid cloud plan
