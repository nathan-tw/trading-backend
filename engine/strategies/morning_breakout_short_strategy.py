import backtrader as bt
import pandas as pd
import numpy as np
from datetime import time
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import run_strategy
class MorningBreakoutShortStrategy(bt.Strategy):
    """
    早盤高低點突破做空策略 (Morning Breakout Short Strategy)
    
    條件設定：
    1. 9:15 分前：若成交量已達昨量 20%
    2. 9:30 分前：若成交量已達昨量 30%
    3. 當日高低點價差已達 100 點
    
    一旦符合上述任一爆量條件 + 振幅條件，即可準備做空。一天只進場一次。
    進場方式：掛 MIT 觸價單，跌破當日現有最低點即刻進場。
    防守機制：停損 60 點，停利設定 13:30 強制出場。
    """
    params = (
        ('amp_threshold', 100),         # 振幅要求 100 點
        ('vol_pct_915', 0.20),          # 9:15 前量能達昨量 20%
        ('vol_pct_930', 0.30),          # 9:30 前量能達昨量 30%
        ('time_915', time(9, 15)),      
        ('time_930', time(9, 30)),
        ('stop_loss_pts', 60),          # 停損 60 點
        ('exit_time', time(13, 30)),    # 13:30 平倉
    )

    def __init__(self):
        # 取得 Daily 資料 (data1 為 Daily K-bars)
        self.data_daily = self.datas[1]
        
        # 盤中追蹤變數
        self.current_day_session = None
        self.traded_today = False
        
        self.intraday_high = 0
        self.intraday_low = float('inf')
        self.cum_vol = 0
        
        # 昨日總量
        self.yesterday_vol = None
        
        # 訂單與防守追蹤
        self.active_order = None
        self.entry_price = 0
        # 當日觸發旗標
        self.setup_triggered = False
        
        # 三關價追蹤
        self.yesterday_high = None
        self.yesterday_low = None
        self.lower_pivot = None

    def log(self, txt, dt=None):
        dt = dt or self.data.datetime.datetime(0)
        print(f'{dt.isoformat()} - {txt}')

    def next(self):
        dt = self.data.datetime.datetime(0)
        t = dt.time()
        d = dt.date()
        
        # 每日重置追蹤狀態
        if self.current_day_session != d:
            self.current_day_session = d
            self.traded_today = False
            self.setup_triggered = False
            self.intraday_high = self.data.high[0]
            self.intraday_low = self.data.low[0]
            self.cum_vol = 0
            
            if self.active_order:
                self.cancel(self.active_order)
                self.active_order = None
                
            # 獲取昨日總量與計算三關價
            # data_daily 在剛開盤的第一根會抓到昨天的收盤K，所以 index 直接用 -1 即可
            if len(self.data_daily) > 0:
                self.yesterday_vol = self.data_daily.volume[-1]
                self.yesterday_high = self.data_daily.high[-1]
                self.yesterday_low = self.data_daily.low[-1]
                
                y_range = self.yesterday_high - self.yesterday_low
                self.lower_pivot = self.yesterday_high - y_range * 1.382
            else:
                self.yesterday_vol = None
                self.lower_pivot = None
        # 及時更新當日的 High / Low 邊界與累積成交量
        self.intraday_high = max(self.intraday_high, self.data.high[0])
        self.intraday_low = min(self.intraday_low, self.data.low[0])
        
        vol = self.data.volume[0]
        self.cum_vol += vol
        
        # 收盤前強制出場
        if t >= self.p.exit_time:
            if self.active_order:
                self.cancel(self.active_order)
                self.active_order = None
            if self.position:
                self.log("時間抵達 13:30，當沖限制強制平倉。")
                self.close()
            return
            
        # 檢測停損機制
        if self.position:
            # 觸發實體停損出場
            if self.data.close[0] >= self.stop_price or self.data.high[0] >= self.stop_price:
                self.log(f"價格觸及防守點 {self.stop_price}，執行停損出場。")
                self.close()
            return # 若已持有倉位，則不再進行進場邏輯
            
        # 尚未交易且未觸發設定時，進行盤中檢驗
        if not self.traded_today and not self.setup_triggered and self.yesterday_vol is not None and self.yesterday_vol > 0:
            
            amplitude = self.intraday_high - self.intraday_low
            amp_ok = (amplitude >= self.p.amp_threshold)
            
            vol_ok = False
            # 在 09:15 之前
            if t <= self.p.time_915:
                if self.cum_vol >= self.yesterday_vol * self.p.vol_pct_915:
                    vol_ok = True
                    # self.log(f"09:15 前達成爆量條件：目前量 {self.cum_vol} >= 昨量 {self.yesterday_vol} * 20%")
            # 在 09:30 之前 (包含 09:15~09:30 期間)
            elif t <= self.p.time_930:
                if self.cum_vol >= self.yesterday_vol * self.p.vol_pct_930:
                    vol_ok = True
                    # self.log(f"09:30 前達成爆量條件：目前量 {self.cum_vol} >= 昨量 {self.yesterday_vol} * 30%")
            # 若振幅與爆量條件同時滿足，且當下價格具備三關價共振 (當前收盤與預備突破點皆低於下關價)
            pivot_ok = False
            if self.lower_pivot is not None:
                pivot_ok = (self.data.close[0] < self.lower_pivot) and (self.intraday_low < self.lower_pivot)
            
            if amp_ok and vol_ok and pivot_ok:
                self.setup_triggered = True
                self.log(f"爆破與三關價(下關價)雙重確認！振幅 {amplitude} 點, 下關價={self.lower_pivot:.1f}。掛出 MIT Sell Stop 於當日低點: {self.intraday_low}")
                
                # 掛 MIT 觸價停損空單
                if self.active_order is None:
                    self.active_order = self.sell(exectype=bt.Order.Stop, price=self.intraday_low)

        # 若已掛單，隨著當日新低的產生，動態下調 MIT 單的觸控點 (確保永遠破底才追)
        # 只有在 setup_triggered 且手上有掛單的時候才做
        elif self.setup_triggered and not self.position and self.active_order is not None:
            # 這邊為了避免訂單價格更新太頻繁，可以簡單起見取消後重掛，或者由 Backtrader 自動處理
            # 因為我們掛單的是 Stop Sell 在 self.intraday_low，如果新的 K 棒有更低的 Low，
            # 代表這根 K 棒本身就是「破底」的，我們的單子早就被觸發進場了。
            # 所以通常只要確保觸發的那瞬間低點即可。
            pass

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
            
        if order.status == order.Completed:
            if order.isbuy():
                pass # 我們只有做空
            elif order.issell():
                self.entry_price = order.executed.price
                self.traded_today = True
                self.stop_price = self.entry_price + self.p.stop_loss_pts
                self.log(f"MIT 空單成交進場！進場價: {self.entry_price}, 設定停損點: {self.stop_price}")
                
            self.active_order = None
            
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.active_order = None

def run_strategy_api(init_cash=100000.0, mtx_mult=10.0, mtx_comm=15.0, slippage=2.0):
    """
    API 呼叫端點: 接收來自前端的資金與成本參數，並回傳策略回測結果 (Dict)
    """
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
        MorningBreakoutShortStrategy,
        data_feeds=[data0, data1],
        plot_name=None,
        json_name=None,
        cash=init_cash,
        mult=mtx_mult,
        commission=mtx_comm,
        slippage=slippage
    )
    
    return result_dict
