from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import traceback

app = Flask(__name__)
CORS(app)

IST = pytz.timezone('Asia/Kolkata')

def get_kite_client(api_key, access_token):
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite

def get_nifty_instruments(kite):
    instruments = kite.instruments("NFO")
    df = pd.DataFrame(instruments)
    nifty_opts = df[
        (df['name'] == 'NIFTY') &
        (df['instrument_type'].isin(['CE', 'PE']))
    ].copy()
    nifty_futs = df[
        (df['name'] == 'NIFTY') &
        (df['instrument_type'] == 'FUT')
    ].copy()
    return nifty_opts, nifty_futs

def get_expiry_dates(kite):
    nifty_opts, _ = get_nifty_instruments(kite)
    expiries = sorted(nifty_opts['expiry'].unique())
    return [str(e) for e in expiries]

def fetch_historical_1min(kite, instrument_token, from_dt, to_dt):
    try:
        data = kite.historical_data(
            instrument_token=int(instrument_token),
            from_date=from_dt,
            to_date=to_dt,
            interval="minute"
        )
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df.columns = [c.lower() for c in df.columns]
        df['date'] = pd.to_datetime(df['date'])

        # Rename OI column variants
        for col in list(df.columns):
            if col in ['open_interest', 'openinterest'] and col != 'oi':
                df = df.rename(columns={col: 'oi'})

        # Ensure numeric
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        if 'oi' in df.columns:
            df['oi'] = pd.to_numeric(df['oi'], errors='coerce').fillna(0)

        print(f"Token {instrument_token}: {len(df)} candles, cols: {list(df.columns)}")
        return df
    except Exception as e:
        print(f"Error fetching token {instrument_token}: {e}")
        return pd.DataFrame()

def compute_sma(series, period):
    s = pd.to_numeric(series, errors='coerce').dropna()
    if len(s) < period:
        return None
    val = s.rolling(period).mean().iloc[-1]
    if pd.isna(val):
        return None
    return round(float(val), 2)

def compute_oi_change(df):
    if 'oi' not in df.columns or len(df) < 2:
        return None, 'neutral'
    oi_series = pd.to_numeric(df['oi'], errors='coerce').dropna()
    if len(oi_series) < 2:
        return None, 'neutral'
    change = float(oi_series.iloc[-1]) - float(oi_series.iloc[-2])
    direction = 'increasing' if change > 0 else ('decreasing' if change < 0 else 'neutral')
    return int(change), direction

def get_atm_strike(spot_price):
    return round(spot_price / 50) * 50

def safe_int(val):
    try:
        if val is None:
            return None
        f = float(val)
        if np.isnan(f):
            return None
        return int(f)
    except:
        return None

def safe_float(val, dec=2):
    try:
        if val is None:
            return None
        f = float(val)
        if np.isnan(f):
            return None
        return round(f, dec)
    except:
        return None

def process_option(kite, row, from_dt, to_dt):
    token = int(row['instrument_token'])
    df = fetch_historical_1min(kite, token, from_dt, to_dt)

    result = {
        'strike': int(row['strike']),
        'instrument_type': str(row['instrument_type']),
        'trading_symbol': str(row['tradingsymbol']),
        'atp': None, 'ltp': None, 'oi': None,
        'oi_change': None, 'oi_change_direction': 'neutral',
        'volume': None, 'volume_sma10': None,
        'atp_ltp_diff': None, 'ltp_sma5': None, 'ltp_sma8': None,
    }

    if df.empty or 'close' not in df.columns:
        return result

    last = df.iloc[-1]
    result['ltp'] = safe_float(last.get('close'))
    result['volume'] = safe_int(last.get('volume', 0))

    if 'oi' in df.columns:
        result['oi'] = safe_int(last.get('oi', 0))

    # ATP = VWAP
    if 'volume' in df.columns and float(df['volume'].sum()) > 0:
        atp = float((df['close'] * df['volume']).sum()) / float(df['volume'].sum())
        result['atp'] = safe_float(atp)
    else:
        result['atp'] = safe_float(float(df['close'].mean()))

    if result['atp'] is not None and result['ltp'] is not None:
        result['atp_ltp_diff'] = safe_float(result['ltp'] - result['atp'])

    if 'volume' in df.columns:
        result['volume_sma10'] = compute_sma(df['volume'], 10)
    result['ltp_sma5'] = compute_sma(df['close'], 5)
    result['ltp_sma8'] = compute_sma(df['close'], 8)

    oi_change, direction = compute_oi_change(df)
    result['oi_change'] = oi_change
    result['oi_change_direction'] = direction

    return result

def process_futures(kite, fut_rows, from_dt, to_dt):
    results = []
    for _, row in fut_rows.iterrows():
        token = int(row['instrument_token'])
        df = fetch_historical_1min(kite, token, from_dt, to_dt)

        fut_data = {
            'trading_symbol': str(row['tradingsymbol']),
            'expiry': str(row['expiry']),
            'ltp': None, 'oi': None,
            'oi_change': None, 'oi_change_direction': 'neutral',
            'volume': None, 'volume_sma10': None,
            'ltp_sma5': None, 'ltp_sma8': None,
        }

        if df.empty or 'close' not in df.columns:
            results.append(fut_data)
            continue

        last = df.iloc[-1]
        fut_data['ltp'] = safe_float(last.get('close'))
        fut_data['volume'] = safe_int(last.get('volume', 0))
        if 'oi' in df.columns:
            fut_data['oi'] = safe_int(last.get('oi', 0))

        if 'volume' in df.columns:
            fut_data['volume_sma10'] = compute_sma(df['volume'], 10)
        fut_data['ltp_sma5'] = compute_sma(df['close'], 5)
        fut_data['ltp_sma8'] = compute_sma(df['close'], 8)

        oi_change, direction = compute_oi_change(df)
        fut_data['oi_change'] = oi_change
        fut_data['oi_change_direction'] = direction

        results.append(fut_data)
    return results

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/expiries', methods=['POST'])
def get_expiries():
    data = request.json
    api_key = data.get('api_key', '').strip()
    access_token = data.get('access_token', '').strip()
    if not api_key or not access_token:
        return jsonify({'error': 'API key and access token are required'}), 400
    try:
        kite = get_kite_client(api_key, access_token)
        expiries = get_expiry_dates(kite)
        return jsonify({'expiries': expiries})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/options_chain', methods=['POST'])
def options_chain():
    data = request.json
    api_key = data.get('api_key', '').strip()
    access_token = data.get('access_token', '').strip()
    expiry_str = data.get('expiry', '').strip()
    num_strikes = int(data.get('num_strikes', 10))

    if not api_key or not access_token or not expiry_str:
        return jsonify({'error': 'Missing required parameters'}), 400

    try:
        kite = get_kite_client(api_key, access_token)

        now_ist = datetime.now(IST)
        to_dt = now_ist.replace(tzinfo=None)
        from_dt = to_dt - timedelta(hours=6)

        # Get spot price
        quote = kite.quote(['NSE:NIFTY 50'])
        spot_price = float(quote['NSE:NIFTY 50']['last_price'])
        atm = get_atm_strike(spot_price)
        print(f"Spot: {spot_price}, ATM: {atm}")

        nifty_opts, nifty_futs = get_nifty_instruments(kite)

        expiry_date = pd.to_datetime(expiry_str).date()
        opts_expiry = nifty_opts[nifty_opts['expiry'] == expiry_date].copy()
        print(f"Options for {expiry_date}: {len(opts_expiry)}")

        if len(opts_expiry) == 0:
            return jsonify({'error': f'No options found for expiry {expiry_str}'}), 400

        all_strikes = sorted(opts_expiry['strike'].unique())
        atm = min(all_strikes, key=lambda x: abs(x - atm))
        atm_idx = list(all_strikes).index(atm)
        low_idx = max(0, atm_idx - num_strikes)
        high_idx = min(len(all_strikes) - 1, atm_idx + num_strikes)
        selected_strikes = all_strikes[low_idx:high_idx + 1]

        opts_filtered = opts_expiry[opts_expiry['strike'].isin(selected_strikes)].copy()

        chain_data = {}
        for _, row in opts_filtered.iterrows():
            strike = int(row['strike'])
            opt_type = str(row['instrument_type'])
            chain_data[f"{strike}_{opt_type}"] = process_option(kite, row, from_dt, to_dt)

        chain_rows = []
        for strike in selected_strikes:
            chain_rows.append({
                'strike': int(strike),
                'is_atm': bool(strike == atm),
                'ce': chain_data.get(f"{strike}_CE", {}),
                'pe': chain_data.get(f"{strike}_PE", {})
            })

        futs_expiry = nifty_futs[nifty_futs['expiry'] == expiry_date]
        if futs_expiry.empty:
            futs_expiry = nifty_futs.sort_values('expiry').head(1)

        futures_data = process_futures(kite, futs_expiry, from_dt, to_dt)

        return jsonify({
            'spot_price': round(float(spot_price), 2),
            'atm_strike': int(atm),
            'timestamp': now_ist.strftime('%Y-%m-%d %H:%M:%S IST'),
            'chain': chain_rows,
            'futures': futures_data
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
