import os
import sys
import os
import argparse
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine
import backtrader as bt
import backtrader.analyzers as btanalyzers

# Optional: Disable standard print outputs during cerebro.run() 
# if you want a clean API response, though Flask masks stdout normally.

# Setup the database connection
DATABASE_URL = os.environ.get(
    'DATABASE_URL', 
    'postgresql://admin:admin123@localhost:5432/taifex_db'
)

def fetch_ohlcv_data(product_code, timeframe, start_date=None, end_date=None):
    """
    Fetch pre-calculated OHLCV data from the PostgreSQL database.
    Returns a pandas DataFrame formatted for Backtrader.
    """
    engine = create_engine(DATABASE_URL)
    
    query = f"""
        SELECT timestamp as datetime, open, high, low, close, volume 
        FROM ohlcv_data 
        WHERE product_code = '{product_code}' AND timeframe = '{timeframe}'
    """
    
    if start_date:
        query += f" AND DATE(timestamp) >= '{start_date}'"
    if end_date:
        query += f" AND DATE(timestamp) <= '{end_date}'"
        
    query += " ORDER BY timestamp"
    
    print(f"Fetching {timeframe} OHLCV data for '{product_code}'...")
    df = pd.read_sql(query, engine)
    
    if df.empty:
        print("No OHLCV data found. You may need to run the OHLCV builder first.")
        return df

    # Convert datetime column and set as index
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    
    # Ensure correct data types for Backtrader
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(int)
    
    return df

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
    # 1. Fetch OHLCV Data directly from DB
    ohlcv_df = fetch_ohlcv_data(product_code, timeframe, start_date, end_date)
    
    if ohlcv_df.empty:
        return {"error": f"No OHLCV {timeframe} data available for product '{product_code}' in the given date range. You might need to run the builder."}


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
