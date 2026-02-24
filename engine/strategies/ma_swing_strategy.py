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
            # 進場條件: 20ma > 60ma 且 k線(Close)在 20ma 以上
            if self.data.close[0] > self.fast_ma[0] and self.fast_ma[0] > self.slow_ma[0]:
                self.log(f"進場: Close({self.data.close[0]:.2f}) > 20MA({self.fast_ma[0]:.2f}) > 60MA({self.slow_ma[0]:.2f})")
                self.buy()
        else:
            # 出場條件: k線跌破 20ma
            if self.data.close[0] < self.fast_ma[0]:
                self.log(f"出場: Close({self.data.close[0]:.2f}) < 20MA({self.fast_ma[0]:.2f})")
                self.close()

def run_strategy_api(init_cash=100000.0, mtx_mult=10.0, mtx_comm=15.0, slippage=2.0):
    import os
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    data_path = os.path.join(backend_dir, 'data', 'kbar', 'TXFR1.csv')
    
    if not os.path.exists(data_path):
        return {"error": f"Data file not found: {data_path}"}
        
    df = pd.read_csv(data_path, parse_dates=['ts'])
    df.set_index('ts', inplace=True)
    df.sort_index(inplace=True)
    
    df_daily = df.resample('D').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    }).dropna()

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
