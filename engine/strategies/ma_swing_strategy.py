import os
import sys
import pandas as pd
import backtrader as bt
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import run_strategy

class MASwingStrategy(bt.Strategy):
    params = (
        ('fast_ma_period', 20),
        ('slow_ma_period', 60),
    )

    def __init__(self):
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_ma_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_ma_period)
        # 移除舊的 crossover，因為進場條件是狀態 (20MA > 60MA) 而不是交叉瞬間

    def log(self, txt, dt=None):
        dt = dt or self.data.datetime.date(0)
        # print(f'{dt.isoformat()} - {txt}')

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
            
        if order.status in [order.Completed]:
            pass # 可以選擇打開 print 觀察
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log('訂單取消/保證金不足/拒絕')

    def next(self):
        if not self.position:
            # 取得 20MA 的「扣抵值」 (20天前的收盤價)
            # 因為 Backtrader 的 index, [0] 是今天, [-1] 是昨天, [-20] 剛好就是今天要被剔除出 20MA 計算的那根 K 棒
            if len(self.data) > self.p.fast_ma_period:
                ma20_deduction_price = self.data.close[-self.p.fast_ma_period]
                
                # 進場條件: 
                # 1. 20ma > 60ma (多頭排列)
                # 2. k線(Close)在 20ma 以上 (站上均線)
                # 3. 今日收盤價 > 20天前的扣抵值 (確保明日 20MA 必定上揚翻揚)
                if self.data.close[0] > self.fast_ma[0] and self.fast_ma[0] > self.slow_ma[0] and self.data.close[0] > ma20_deduction_price:
                    self.log(f"進場: Close({self.data.close[0]:.2f}) > 20MA({self.fast_ma[0]:.2f}) > 60MA({self.slow_ma[0]:.2f}) | 且大於扣抵值({ma20_deduction_price:.2f})")
                    self.buy()
        else:
            # 出場條件: k線跌破 20ma
            if self.data.close[0] < self.fast_ma[0]:
                self.log(f"出場: Close({self.data.close[0]:.2f}) < 20MA({self.fast_ma[0]:.2f})")
                self.close()

def run_strategy_api(init_cash=100000.0, mtx_mult=10.0, mtx_comm=15.0, slippage=2.0):
    from models import db
    from sqlalchemy import text
    
    query_daily = text('SELECT timestamp as ts, open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" FROM kbar_1d WHERE product_code=\'TXFR1\' ORDER BY timestamp ASC')
    df_daily = pd.read_sql(query_daily, db.engine)
    
    if df_daily.empty:
        return {"error": "Database returned empty for TXFR1 1d timeframe"}
        
    df_daily = df_daily.astype({'Open': float, 'High': float, 'Low': float, 'Close': float, 'Volume': int})
    df_daily.set_index('ts', inplace=True)
    df_daily.sort_index(inplace=True)

    result_dict = run_strategy(
        strategy_cls=MASwingStrategy, 
        data_df=df_daily,
        cash=init_cash,
        commission=mtx_comm,
        mult=mtx_mult,
        slippage=slippage,
        stake=1,
        plot_name=None,
        json_name=None
    )
    
    return result_dict
