import backtrader as bt
import pandas as pd
from datetime import time
import os
import shutil
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import run_strategy

class CombinedMABreakoutStrategy(bt.Strategy):
    """
    結合日K波段多頭與當沖早盤空頭的混合策略
    
    多頭條件 (日K級別):
    1. 當前日K收盤價 > 20MA
    2. 20MA > 60MA
    進場：滿足條件時，隔日早盤自動進場做多。
    出場：當日K收盤價跌破 20MA 時，隔日早盤平多單。
    
    空頭條件 (當沖5分K級別 - Morning Breakout)：
    1. 9:15 分前達昨量 20% 或 9:30 前達昨量 30%
    2. 振幅 > 100點 且跌破昨日下關價
    進場：掛 MIT 觸價單於當日最低點。若已持有多單，反手做空 (平多單+建空單)。
    出場：停損 60點，或 13:30 強制平倉。
    """
    params = (
        ('fast_ma_period', 20),
        ('slow_ma_period', 60),
        ('amp_threshold', 100),
        ('vol_pct_915', 0.20),
        ('vol_pct_930', 0.30),
        ('time_915', time(9, 15)),      
        ('time_930', time(9, 30)),
        ('stop_loss_pts', 60),
        ('exit_time', time(13, 30)),
    )

    def __init__(self):
        # 取用 Data Feed (data0=5m, data1=Daily)
        self.data_daily = self.datas[1]
        
        # 建立日K移動平均線
        self.fast_ma = bt.indicators.SMA(self.data_daily.close, period=self.p.fast_ma_period)
        self.slow_ma = bt.indicators.SMA(self.data_daily.close, period=self.p.slow_ma_period)
        
        # 盤中追蹤變數
        self.current_day_session = None
        self.short_triggered_today = False
        self.setup_triggered = False
        
        self.intraday_high = 0
        self.intraday_low = float('inf')
        self.cum_vol = 0
        
        # 每日重算變數
        self.yesterday_vol = None
        self.lower_pivot = None
        
        # 訂單與防守
        self.active_order = None
        self.entry_price = 0
        self.stop_price = 0

    def log(self, txt, dt=None):
        dt = dt or self.data.datetime.datetime(0)
        print(f'{dt.isoformat()} - {txt}')

    def next(self):
        dt = self.data.datetime.datetime(0)
        t = dt.time()
        d = dt.date()
        
        # 每日第一根 K 棒的初始化與日K邏輯判定
        if self.current_day_session != d:
            self.current_day_session = d
            self.short_triggered_today = False
            self.setup_triggered = False
            self.intraday_high = self.data.high[0]
            self.intraday_low = self.data.low[0]
            self.cum_vol = 0
            
            if self.active_order:
                self.cancel(self.active_order)
                self.active_order = None
                
            # 計算前一日量能與三關價
            if len(self.data_daily) > 0:
                self.yesterday_vol = self.data_daily.volume[-1]
                yesterday_high = self.data_daily.high[-1]
                yesterday_low = self.data_daily.low[-1]
                
                y_range = yesterday_high - yesterday_low
                self.lower_pivot = yesterday_high - y_range * 1.382
            else:
                self.yesterday_vol = None
                self.lower_pivot = None

            # --- 執行日 K 長線交易邏輯 ---
            if len(self.fast_ma) > 0 and len(self.slow_ma) > 0:
                if self.position.size > 0:
                    # 多單出場條件: 跌破 20MA
                    if self.data_daily.close[0] < self.fast_ma[0]:
                        self.log(f"日K跌破20MA ({self.data_daily.close[0]:.2f} < {self.fast_ma[0]:.2f})，平倉多單")
                        self.close()
                elif self.position.size == 0:
                    # 多單進場條件
                    if self.data_daily.close[0] > self.fast_ma[0] and self.fast_ma[0] > self.slow_ma[0]:
                        self.log(f"符合多頭趨勢(日K > 20MA > 60MA)，開倉做多")
                        self.buy()

        # 即時更新當日高低點與累積量
        self.intraday_high = max(self.intraday_high, self.data.high[0])
        self.intraday_low = min(self.intraday_low, self.data.low[0])
        self.cum_vol += self.data.volume[0]

        # ----------------------------------------
        # 當沖空單出場邏輯 (13:30 強制平倉)
        # ----------------------------------------
        if t >= self.p.exit_time:
            if self.active_order:
                self.cancel(self.active_order)
                self.active_order = None
            if self.position.size < 0:
                self.log("時間抵達 13:30，當沖空單強制平倉。")
                self.close()
            return

        # ----------------------------------------
        # 空單停損檢查 (防守 60點)
        # ----------------------------------------
        if self.position.size < 0:
            if self.data.close[0] >= self.stop_price or self.data.high[0] >= self.stop_price:
                self.log(f"空單價格觸及防守點 {self.stop_price}，執行停損出場。")
                self.close()
            return # 若為空單持倉中，不需再進場

        # ----------------------------------------
        # 當沖空單進場與反手邏輯 (Morning Breakout)
        # ----------------------------------------
        if not self.short_triggered_today and not self.setup_triggered and self.yesterday_vol is not None and self.yesterday_vol > 0:
            amplitude = self.intraday_high - self.intraday_low
            amp_ok = (amplitude >= self.p.amp_threshold)
            
            vol_ok = False
            if t <= self.p.time_915:
                if self.cum_vol >= self.yesterday_vol * self.p.vol_pct_915:
                    vol_ok = True
            elif t <= self.p.time_930:
                if self.cum_vol >= self.yesterday_vol * self.p.vol_pct_930:
                    vol_ok = True

            pivot_ok = False
            if self.lower_pivot is not None:
                pivot_ok = (self.data.close[0] < self.lower_pivot) and (self.intraday_low < self.lower_pivot)

            if amp_ok and vol_ok and pivot_ok:
                self.setup_triggered = True
                # 若原本有多單，要平倉並做空，只需要設定 size = 2 (因為基本預設口數參數是 1)
                order_size = 2 if self.position.size > 0 else 1
                self.log(f"爆破與三關價雙重確認！掛 MIT 觸價空單於 {self.intraday_low} (Size: {order_size})")
                
                if self.active_order is None:
                    self.active_order = self.sell(exectype=bt.Order.Stop, price=self.intraday_low, size=order_size)

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
            
        if order.status == order.Completed:
            if order.isbuy():
                if self.position.size > 0:
                    self.log(f"多單買進成交！進場價: {order.executed.price}")
            elif order.issell():
                if self.position.size < 0:
                    self.entry_price = order.executed.price
                    self.short_triggered_today = True
                    self.stop_price = self.entry_price + self.p.stop_loss_pts
                    self.log(f"MIT 空單成交 (或觸發反手)！進場價: {self.entry_price}, 設定停損點: {self.stop_price}")
                elif self.position.size == 0:
                    self.log(f"多單平倉成交！出場價: {order.executed.price}")
            self.active_order = None
            
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.active_order = None

def run_strategy_api(init_cash=100000.0, mtx_mult=10.0, mtx_comm=15.0, slippage=2.0):
    """
    API 呼叫端點: 接收來自前端的資金與成本參數，並回傳策略回測結果 (Dict)
    """
    # 確保資料路徑正確 (指向 backend 內的複製檔)
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

    df_5m = df.resample('5min', label='right', closed='right').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    }).dropna()

    data0 = bt.feeds.PandasData(
        dataname=df_5m,
        open='Open',
        high='High',
        low='Low',
        close='Close',
        volume='Volume',
        openinterest=-1
    )
    
    data1 = bt.feeds.PandasData(
        dataname=df_daily,
        open='Open',
        high='High',
        low='Low',
        close='Close',
        volume='Volume',
        openinterest=-1,
    )
    
    # 執行回測框架，收集 JSON 格式的返回字典
    result_dict = run_strategy(
        CombinedMABreakoutStrategy,
        data_feeds=[data0, data1],
        plot_name=None,
        json_name=None,  # 停用寫檔
        cash=init_cash,
        mult=mtx_mult,
        commission=mtx_comm,
        slippage=slippage
    )
    
    return result_dict
