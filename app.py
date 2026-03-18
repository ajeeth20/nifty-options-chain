from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
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
            instrument_token=instrument_token,
            from_date=from_dt,
            to_date=to_dt,
            interval="minute"
        )
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        return df
    except Exception as e:
        print(f"Error fetching historical data for token {instrument_token}: {e}")
        return pd.DataFrame()

def compute_sma(series, period):
    if len(series) < period:
        return None
    return round(float(series.rolling(period).mean().iloc[-1]), 2)

def compute_oi_change(df):
    if df.empty or 'oi' not in df.columns or len(df) < 2:
        return None, 'neutral'
    current_oi = df['oi'].iloc[-1]
    prev_oi = df['oi'].iloc[-2]
    change = current_oi - prev_oi
    direction = 'increasing' if change > 0 else ('decreasing' if change < 0 else 'neutral')
    return int(change), direction

def get_atm_strike(spot_price):
    return round(spot_price / 50) * 50

def process_option(kite, row, from_dt, to_dt):
    token = int(row['instrument_token'])
    df = fetch_historical_1min(kite, token, from_dt, to_dt)
    
    result = {
        'strike': int(row['strike']),
        'instrument_type': row['instrument_type'],
        'trading_symbol': row['tradingsymbol'],
        'atp': None,
        'ltp': None,
        'oi': None,
        'oi_change': None,
        'oi_change_direction': 'neutral',
        'volume': None,
        'volume_sma10': None,
        'atp_ltp_diff': None,
        'ltp_sma5': None,
        'ltp_sma8': None,
    }
    
    if df.empty:
        return result
    
    last = df.iloc[-1]
    result['ltp'] = round(float(last['close']), 2)
    result['volume'] = int(last['volume'])
    result['oi'] = int(last['oi']) if 'oi' in df.columns else None
    
    # ATP = VWAP-style average: sum(close*volume) / sum(volume)
    if df['volume'].sum() > 0:
        atp = (df['close'] * df['volume']).sum() / df['volume'].sum()
        result['atp'] = round(float(atp), 2)
    
    if result['atp'] is not None and result['ltp'] is not None:
        result['atp_ltp_diff'] = round(result['ltp'] - result['atp'], 2)
    
    result['volume_sma10'] = compute_sma(df['volume'].astype(float), 10)
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
        expiry = str(row['expiry'])
        
        fut_data = {
            'trading_symbol': row['tradingsymbol'],
            'expiry': expiry,
            'ltp': None,
            'oi': None,
            'oi_change': None,
            'oi_change_direction': 'neutral',
            'volume': None,
            'volume_sma10': None,
            'ltp_sma5': None,
            'ltp_sma8': None,
        }
        
        if df.empty:
            results.append(fut_data)
            continue
        
        last = df.iloc[-1]
        fut_data['ltp'] = round(float(last['close']), 2)
        fut_data['volume'] = int(last['volume'])
        fut_data['oi'] = int(last['oi']) if 'oi' in df.columns else None
        
        fut_data['volume_sma10'] = compute_sma(df['volume'].astype(float), 10)
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
        from_dt = to_dt - timedelta(hours=2)
        
        # Get spot price from NSE:NIFTY 50
        quote = kite.quote(['NSE:NIFTY 50'])
        spot_price = quote['NSE:NIFTY 50']['last_price']
        atm = get_atm_strike(spot_price)
        
        nifty_opts, nifty_futs = get_nifty_instruments(kite)
        
        # Filter by expiry
        expiry_date = pd.to_datetime(expiry_str).date()
        opts_expiry = nifty_opts[nifty_opts['expiry'] == expiry_date].copy()
        
        # Filter strikes around ATM
        all_strikes = sorted(opts_expiry['strike'].unique())
        if atm not in all_strikes and len(all_strikes) > 0:
            atm = min(all_strikes, key=lambda x: abs(x - atm))
        
        atm_idx = list(all_strikes).index(atm) if atm in all_strikes else len(all_strikes) // 2
        low_idx = max(0, atm_idx - num_strikes)
        high_idx = min(len(all_strikes) - 1, atm_idx + num_strikes)
        selected_strikes = all_strikes[low_idx:high_idx + 1]
        
        opts_filtered = opts_expiry[opts_expiry['strike'].isin(selected_strikes)].copy()
        
        # Process options
        chain_data = {}
        for _, row in opts_filtered.iterrows():
            strike = int(row['strike'])
            opt_type = row['instrument_type']
            key = f"{strike}_{opt_type}"
            chain_data[key] = process_option(kite, row, from_dt, to_dt)
        
        # Build chain rows
        chain_rows = []
        for strike in selected_strikes:
            ce_key = f"{strike}_CE"
            pe_key = f"{strike}_PE"
            ce = chain_data.get(ce_key, {})
            pe = chain_data.get(pe_key, {})
            chain_rows.append({
                'strike': strike,
                'is_atm': bool(strike == atm),
                'ce': ce,
                'pe': pe
            })
        
        # Futures
        futs_near = nifty_futs[nifty_futs['expiry'] == expiry_date]
        if futs_near.empty:
            futs_near = nifty_futs.head(1)
        
        futures_data = process_futures(kite, futs_near, from_dt, to_dt)
        
        return jsonify({
            'spot_price': round(float(spot_price), 2),
            'atm_strike': atm,
            'timestamp': now_ist.strftime('%Y-%m-%d %H:%M:%S IST'),
            'chain': chain_rows,
            'futures': futures_data
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
