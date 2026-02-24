import os
import sys
import argparse
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine
import backtrader as bt
import backtrader.analyzers as btanalyzers

# Optional: Disable standard print outputs during cerebro.run() 
# if you want a clean API response, though Flask masks stdout normally.

# Setup the database connection
# This usually aligns with what is in your backend/app.py or docker-compose
DATABASE_URL = os.environ.get(
    'DATABASE_URL', 
    'postgresql://admin:admin123@localhost:5432/taifex_db'
)

def fetch_tick_data(product_code, start_date=None, end_date=None):
    """
    Fetch tick data from the PostgreSQL database, restricted to a specific product.
    Returns a pandas DataFrame.
    """
    engine = create_engine(DATABASE_URL)
    
    query = f"""
        SELECT trade_date, trade_time, price, volume 
        FROM tick_data 
        WHERE product_code = '{product_code}'
    """
    
    if start_date:
        query += f" AND trade_date >= '{start_date}'"
    if end_date:
        query += f" AND trade_date <= '{end_date}'"
        
    query += " ORDER BY trade_date, trade_time"
    
    print(f"Fetching data for '{product_code}'...")
    df = pd.read_sql(query, engine)
    
    if df.empty:
        print("No data found.")
        return df

    # Convert trade_date and trade_time to a single datetime column
    # trade_date is YYYY-MM-DD
    # trade_time is HHMMSS
    df['datetime_str'] = df['trade_date'].astype(str) + ' ' + df['trade_time']
    df['datetime'] = pd.to_datetime(df['datetime_str'], format='%Y-%m-%d %H%M%S')
    
    df.set_index('datetime', inplace=True)
    df.drop(columns=['trade_date', 'trade_time', 'datetime_str'], inplace=True)
    
    # Cast numerical columns just in case
    df['price'] = df['price'].astype(float)
    df['volume'] = df['volume'].astype(int)
    
    return df

def resample_to_ohlcv(df, timeframe='1min'):
    """
    Resample tick data (price, volume) into an OHLCV dataframe.
    Valid timeframes: '1min', '5min', '1H', '1D', etc.
    """
    if df.empty:
        return pd.DataFrame()

    print(f"Resampling tick data to {timeframe} OHLCV...")
    # Resample logic
    ohlc_dict = {
        'price': 'ohlc',
        'volume': 'sum'
    }
    
    # Resample using the datetime index
    ohlcv = df.resample(timeframe).apply(ohlc_dict)
    
    # Flatten multi-level columns from 'ohlc'
    ohlcv.columns = ['open', 'high', 'low', 'close', 'volume']
    
    # Drop rows without trades in the time period
    ohlcv.dropna(inplace=True)
    
    return ohlcv

# -----------------------------------------------------------------------------
# Main Execution / API Engine
# -----------------------------------------------------------------------------
def run_backtest_for_api(strategy_class, product_code, timeframe='1min', start_date=None, end_date=None, **kwargs):
    """
    Run a backtest for the API and return the results as a dictionary.
    
    :param strategy_class: The Backtrader Strategy class to run.
    :param product_code: The symbol/product to fetch (e.g., 'TX').
    :param timeframe: Resampling timeframe (e.g., '1min', '5min', '1D').
    :param start_date: YYYY-MM-DD
    :param end_date: YYYY-MM-DD
    :param kwargs: Additional parameters to pass to the strategy.
    :return: dict containing backtest results.
    """
    # 1. Fetch Tick Data
    tick_df = fetch_tick_data(product_code, start_date, end_date)
    
    if tick_df.empty:
        return {"error": f"No data available for product '{product_code}' in the given date range."}
        
    # 2. Resample to OHLCV
    ohlcv_df = resample_to_ohlcv(tick_df, timeframe=timeframe)
    
    if ohlcv_df.empty:
        return {"error": "Dataframe is empty after resampling. Try a different timeframe or date range."}

    # 3. Setup Backtrader
    cerebro = bt.Cerebro()
    
    # Add strategy with dynamic parameters
    cerebro.addstrategy(strategy_class, **kwargs)
    
    # Create a Data Feed
    data = bt.feeds.PandasData(dataname=ohlcv_df)
    cerebro.adddata(data)
    
    # Set initial cash and commission
    INITIAL_CASH = 1000000.0
    cerebro.broker.setcash(INITIAL_CASH)
    cerebro.broker.setcommission(commission=50.0, margin=None, mult=200.0) 
    
    # Size multiplier for the trade (e.g. trade 1 contract)
    cerebro.addsizer(bt.sizers.FixedSize, stake=1)

    # Add Analyzers to extract statistics
    cerebro.addanalyzer(btanalyzers.TradeAnalyzer, _name='trades')
    cerebro.addanalyzer(btanalyzers.DrawDown, _name='drawdown')
    
    # 4. Run Backtest
    results = cerebro.run()
    first_strat = results[0]
    
    final_value = cerebro.broker.getvalue()
    pnl = final_value - INITIAL_CASH
    
    # Extract trade statistics safely
    trade_analysis = first_strat.analyzers.trades.get_analysis()
    
    # Backtrader uses AutoDict, which raises KeyError if 'total' doesn't exist AND we try to access a subkey
    # We can use .get() to safely check if keys exist
    total_trades = trade_analysis.get('total', {}).get('closed', 0)
    won_trades = trade_analysis.get('won', {}).get('total', 0)
    lost_trades = trade_analysis.get('lost', {}).get('total', 0)
    
    winning_rate = (won_trades / total_trades * 100) if total_trades > 0 else 0
    
    # 5. Return Results Dictionary
    return {
        "status": "success",
        "product_code": product_code,
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "strategy": strategy_class.__name__,
        "params": kwargs,
        "metrics": {
            "initial_cash": INITIAL_CASH,
            "final_value": round(final_value, 2),
            "pnl": round(pnl, 2),
            "total_trades": total_trades,
            "won_trades": won_trades,
            "lost_trades": lost_trades,
            "winning_rate_percent": round(winning_rate, 2)
        }
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run backtest using database tick data.')
    parser.add_argument('--product', type=str, default='TX', help='Product code (e.g., TX, MTX)')
    parser.add_argument('--timeframe', type=str, default='1min', help='Resampling timeframe (e.g., 1min, 5min)')
    parser.add_argument('--start', type=str, help='Start date YYYY-MM-DD')
    parser.add_argument('--end', type=str, help='End date YYYY-MM-DD')
    
    args = parser.parse_args()
    
    # When running directly, default to SmaCross for backward compatibility testing
    from strategies.sma_cross import SmaCross

    results = run_backtest_for_api(
        strategy_class=SmaCross,
        product_code=args.product,
        timeframe=args.timeframe,
        start_date=args.start,
        end_date=args.end
    )
    
    # Print the json-like dictionary to stdout
    import json
    print(json.dumps(results, indent=2))
