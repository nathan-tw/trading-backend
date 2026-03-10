import os
import pandas as pd

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data', 'kbar')
    input_file = os.path.join(data_dir, 'TXFR1.csv')
    
    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found.")
        return

    print("Loading 1-min data...")
    df = pd.read_csv(input_file, parse_dates=['ts'])
    df.set_index('ts', inplace=True)
    df.sort_index(inplace=True)

    print("Aggregating to 5-minute K-bars...")
    df_5m = df.resample('5min', label='right', closed='right').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    out_5m = os.path.join(data_dir, 'TXFR1_5m.csv')
    df_5m.to_csv(out_5m)
    print(f"Saved 5-minute data to {out_5m} ({len(df_5m)} rows)")

    print("Aggregating to 1-hour K-bars...")
    df_1h = df.resample('1h', label='right', closed='right').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    out_1h = os.path.join(data_dir, 'TXFR1_1h.csv')
    df_1h.to_csv(out_1h)
    print(f"Saved 1-hour data to {out_1h} ({len(df_1h)} rows)")

    print("Aggregating to Daily K-bars (custom trade hours)...")
    _df = df.copy()
    _df['time'] = _df.index.time
    # 利用時間偏移將 (15:00 ~ 13:45) 裝進同一個日期日期區間內
    _df['trade_date'] = (_df.index + pd.Timedelta(hours=9)).date
    
    daily_records = []
    grouped = _df.groupby('trade_date')
    
    for t_date, group in grouped:
        if group.empty: continue
        
        # 開高低量：涵蓋整個 (夜盤+日盤) 視窗
        open_price = group['Open'].iloc[0]
        high_price = group['High'].max()
        low_price = group['Low'].min()
        vol_sum = group['Volume'].sum()
        
        # 收盤價 (13:45 為最後一根)
        close_price = group['Close'].iloc[-1]
            
        # timestamp 設定為 13:45 以對齊日盤結算時間
        ts = pd.Timestamp(f"{t_date} 13:45:00")
            
        daily_records.append({
            'ts': ts,
            'Open': open_price,
            'High': high_price,
            'Low': low_price,
            'Close': close_price,
            'Volume': vol_sum
        })
        
    df_daily = pd.DataFrame(daily_records)
    if not df_daily.empty:
        df_daily.set_index('ts', inplace=True)
        df_daily.sort_index(inplace=True)
        
    out_daily = os.path.join(data_dir, 'TXFR1_daily.csv')
    df_daily.to_csv(out_daily)
    print(f"Saved custom Daily data to {out_daily} ({len(df_daily)} rows)")

    print("All aggregations completed successfully.")

if __name__ == "__main__":
    main()
