import os
import argparse
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import create_app
from models import db, OhlcvData

DATABASE_URL = os.environ.get(
    'DATABASE_URL', 
    'postgresql://admin:admin123@localhost:5432/taifex_db'
)

# Standard backtesting timeframes
TIMEFRAMES = {
    '1min': '1min',
    '5min': '5min',
    '15min': '15min',
    '1H': '1h',
    '4H': '4h',
    '1D': '1D',
    '1W': '1W',
    '1M': 'ME'  # Pandas uses ME for month end frequency
}

def fetch_tick_data_for_build(product_code, target_date=None, start_date=None, end_date=None):
    """
    Fetch tick data for a specific product and date(s).
    """
    engine = create_engine(DATABASE_URL)
    
    query = f"""
        SELECT trade_date, trade_time, price, volume 
        FROM tick_data 
        WHERE product_code = '{product_code}'
    """
    
    if target_date:
        query += f" AND trade_date = '{target_date}'"
    elif start_date and end_date:
        query += f" AND trade_date >= '{start_date}' AND trade_date <= '{end_date}'"
        
    query += " ORDER BY trade_date, trade_time"
    
    print(f"Fetching tick data for '{product_code}'...")
    df = pd.read_sql(query, engine)
    
    if df.empty:
        return df

    df['datetime_str'] = df['trade_date'].astype(str) + ' ' + df['trade_time']
    df['datetime'] = pd.to_datetime(df['datetime_str'], format='%Y-%m-%d %H%M%S')
    
    df.set_index('datetime', inplace=True)
    df.drop(columns=['trade_date', 'trade_time', 'datetime_str'], inplace=True)
    
    df['price'] = df['price'].astype(float)
    df['volume'] = df['volume'].astype(int)
    
    return df

def build_ohlcv_for_date(app, product_code, target_date):
    """
    Build and save OHLCV data for a specific product and date.
    Returns the number of rows inserted across all timeframes.
    """
    df = fetch_tick_data_for_build(product_code, target_date=target_date)
    
    if df.empty:
        print(f"No tick data found for {product_code} on {target_date}.")
        return 0
        
    total_inserted = 0
    
    with app.app_context():
        # Make sure we don't have duplicates for this specific day
        # In a real system, you might want to delete existing and rebuild, or skip if exists
        # For simplicity, we'll delete any existing OHLCV for this date/product first
        try:
            db.session.query(OhlcvData).filter(
                OhlcvData.product_code == product_code,
                db.func.date(OhlcvData.timestamp) == target_date
            ).delete(synchronize_session=False)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error clearing old data: {e}")
            
        ohlc_dict = {
            'price': 'ohlc',
            'volume': 'sum'
        }
            
        for tf_name, pd_tf in TIMEFRAMES.items():
            print(f"Building {tf_name} OHLCV...")
            
            # Resample
            ohlcv = df.resample(pd_tf).apply(ohlc_dict)
            ohlcv.columns = ['open', 'high', 'low', 'close', 'volume']
            ohlcv.dropna(inplace=True)
            
            if ohlcv.empty:
                continue
                
            # Prepare rows for bulk insert
            rows = []
            for timestamp, row in ohlcv.iterrows():
                rows.append(dict(
                    product_code=product_code,
                    timeframe=tf_name,
                    timestamp=timestamp.to_pydatetime(),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=int(row['volume'])
                ))
                
            if rows:
                try:
                    db.session.bulk_insert_mappings(OhlcvData, rows)
                    db.session.commit()
                    total_inserted += len(rows)
                except Exception as e:
                    db.session.rollback()
                    print(f"Error inserting {tf_name} rows: {e}")
                    
    print(f"Successfully built and inserted {total_inserted} OHLCV rows for {product_code} on {target_date}.")
    return total_inserted

def backfill_all_history(app, product_code):
    """
    Iterate through all unique dates in tick_data and build OHLCV.
    """
    engine = create_engine(DATABASE_URL)
    query = f"SELECT DISTINCT trade_date FROM tick_data WHERE product_code = '{product_code}' ORDER BY trade_date"
    dates_df = pd.read_sql(query, engine)
    
    if dates_df.empty:
        print(f"No dates found for {product_code}.")
        return
        
    total_dates = len(dates_df)
    print(f"Found {total_dates} dates to process for {product_code}.")
    
    total_inserted = 0
    for i, row in dates_df.iterrows():
        trade_date = row['trade_date'].isoformat()
        print(f"Processing day {i+1}/{total_dates}: {trade_date}")
        inserted = build_ohlcv_for_date(app, product_code, target_date=trade_date)
        total_inserted += inserted
        
    print(f"Backfill complete! Total rows inserted: {total_inserted}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build OHLCV data from tick data.')
    parser.add_argument('--product', type=str, default='TX', help='Product code (e.g., TX)')
    parser.add_argument('--date', type=str, help='Specific date to build YYYY-MM-DD')
    parser.add_argument('--backfill', action='store_true', help='Backfill all historical data')
    
    args = parser.parse_args()
    
    app = create_app()
    
    if args.backfill:
        backfill_all_history(app, args.product)
    elif args.date:
        build_ohlcv_for_date(app, args.product, args.date)
    else:
        print("Please provide either --date YYYY-MM-DD or --backfill")
