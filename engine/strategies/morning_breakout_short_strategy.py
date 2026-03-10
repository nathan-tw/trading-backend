import os
import sys
import pandas as pd
import backtrader as bt
from datetime import time
# 將上層目錄加入路徑以引入 framework
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
    
    注意：昨量是昨天日盤的成交量，不包含夜盤。
    """
    params = (
        ('amp_threshold', 100),         # 振幅要求 100 點
        ('vol_pct_915', 0.20),          # 9:15 前量能達昨量 20%
        ('vol_pct_930', 0.30),          # 9:30 前量能達昨量 30%
        ('time_915', time(9, 15)),      
        ('time_930', time(9, 30)),
        ('stop_loss_pts', 60),          # 停損 60 點
        ('time_845', time(8, 45)),      # 開盤時間
        ('time_1345', time(13, 45)),    # 收盤時間
        ('exit_time', time(13, 30)),    # 13:30 平倉
    )

    def __init__(self):
        # 盤中追蹤變數
        self.current_day_date = None
        self.traded_today = False
        
        self.intraday_high = 0
        self.intraday_low = float('inf')
        
        # 累積量
        self.cum_vol = 0                 # 當天日盤累積量
        self.yesterday_day_vol = None    # 昨日純日盤量
        
        # 訂單與防守追蹤
        self.active_order = None
        self.entry_price = 0
        self.stop_price = 0
        self.setup_triggered = False
        self.waiting_for_entry = False

    def log(self, txt, dt=None):
        dt = dt or self.data.datetime.datetime(0)
        # print(f'{dt.isoformat()} - {txt}')

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
            
        if order.status == order.Completed:
            if order.issell():
                self.entry_price = order.executed.price
                self.traded_today = True
                self.stop_price = self.entry_price + self.p.stop_loss_pts
                self.log(f"MIT 空單成交進場！進場價: {self.entry_price}, 設定停損點: {self.stop_price}")
                
            self.active_order = None
            
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.active_order = None

    def next(self):
        dt = self.data.datetime.datetime(0)
        t = dt.time()
        d = dt.date()
        
        # 每日重置追蹤狀態
        if self.current_day_date != d:
            self.current_day_date = d
            self.traded_today = False
            self.setup_triggered = False
            self.waiting_for_entry = False
            self.intraday_high = self.data.high[0]
            self.intraday_low = self.data.low[0]
            
            # 將前一日的「日盤累積總量」移轉給 yesterday_day_vol
            if self.cum_vol > 0:
                self.yesterday_day_vol = self.cum_vol
            
            # 開始計算今天的日盤累積量
            self.cum_vol = self.data.volume[0]
            
            if self.active_order:
                self.cancel(self.active_order)
                self.active_order = None
        else:
            # 判斷是否為「日盤時段」(08:45 ~ 13:45)
            is_day_session = self.p.time_845 < t <= self.p.time_1345
            
            if not is_day_session:
                # 非日盤時段，強制清零任何殘留狀態
                if self.position:
                    self.close()
                if self.active_order:
                    self.cancel(self.active_order)
                    self.active_order = None
                self.setup_triggered = False
                self.waiting_for_entry = False
                return
                
            # 及時更新當日的 High / Low 邊界與累積成交量 (僅限日盤)
            self.intraday_high = max(self.intraday_high, self.data.high[0])
            self.intraday_low = min(self.intraday_low, self.data.low[0])
            self.cum_vol += self.data.volume[0]
        
        # 收盤前強制出場 (13:30)
        if t >= self.p.exit_time:
            if self.active_order:
                self.cancel(self.active_order)
                self.active_order = None
            if self.position:
                self.log("時間抵達 13:30，當沖限制強制平倉。")
                self.close()
            self.setup_triggered = False
            self.waiting_for_entry = False
            return # 超過 13:30 不再繼續判定進場
            
        # 檢測停損機制
        if self.position:
            # 觸發實體停損出場
            if self.data.close[0] >= self.stop_price or self.data.high[0] >= self.stop_price:
                self.log(f"價格觸及防守點 {self.stop_price}，執行停損出場。")
                self.close()
            return # 若已持有倉位，則不再進行進場邏輯
            
        # 尚未交易且未觸發設定時，進行盤中檢驗
        if not self.traded_today and not self.setup_triggered and self.yesterday_day_vol is not None and self.yesterday_day_vol > 0:
            
            amplitude = self.intraday_high - self.intraday_low
            amp_ok = (amplitude >= self.p.amp_threshold)
            
            vol_ok = False
            # 在 09:15 之前
            if t <= self.p.time_915:
                if self.cum_vol >= self.yesterday_day_vol * self.p.vol_pct_915:
                    vol_ok = True
            # 在 09:30 之前 (包含 09:15~09:30 期間)
            elif t <= self.p.time_930:
                if self.cum_vol >= self.yesterday_day_vol * self.p.vol_pct_930:
                    vol_ok = True
            
            if amp_ok and vol_ok:
                self.setup_triggered = True
                self.breakout_level = self.intraday_low
                self.log(f"爆破確認！振幅 {amplitude} 點, 昨日純日盤量 {self.yesterday_day_vol}。準備跌破當日低點: {self.breakout_level} 追空。")
                self.waiting_for_entry = True

        # 若已觸發監控，確保跌破新低時手動發出市價單 
        # 修改條件：只有在 9:15 或 9:30 剛觸發不久的時段允許進場，避免延後到盤中或尾盤才跌破
        elif self.setup_triggered and self.waiting_for_entry and not self.position:
            # 由於 Backtrader 的 next() 在收到 5 分 K 線時，時間標籤是該 K 線的結束時間。
            # 發出的市價單 (Market Order) 會在"下一根" K 棒的開盤價成交。
            # 如果要求絕對不能在 09:30 之後 (含 09:35 K棒) 才顯示進場，
            # 能夠「發出訊號」的最晚一根 K 棒必須是 09:30 (它會在 09:35 那根成交，標籤為 09:35)。
            # 若連 09:35 成交都不允許，那麼產生訊號的最晚時間必須是 09:25 (在 09:30 那根成交)。
            # 為了徹底防堵 9:30 以後的進場，當前 K 棒時間 `t` 必須小於 09:30。
            time_930 = time(9, 30)
            if t < time_930:
                # 這裡模擬 MIT 觸價：如果當前最低價低於 setup 當時的低點，則進場
                if self.data.low[0] < self.breakout_level:
                    self.log(f"價格 {self.data.low[0]} 跌破早盤低點 {self.breakout_level}，執行追空市價單！")
                    self.waiting_for_entry = False
                    self.active_order = self.sell(exectype=bt.Order.Market)
            else:
                if self.waiting_for_entry:
                    self.log("超過 09:30 進入點，今日不再發動進場，取消監控。")
                self.waiting_for_entry = False
                self.setup_triggered = False # Reset to avoid further entry attempts today

def run_strategy_api(init_cash=100000.0, mtx_mult=10.0, mtx_comm=15.0, slippage=2.0):
    from models import db
    from sqlalchemy import text
    
    # 抓取 5 分 K 資料 (用於盤中監控)
    query_5m = text('SELECT timestamp as ts, open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" FROM kbar_5m WHERE product_code=\'TXFR1\' ORDER BY timestamp ASC')
    df_5m = pd.read_sql(query_5m, db.engine)
    
    if df_5m.empty:
        return {"error": "Database returned empty for TXFR1 5m timeframe"}
        
    df_5m = df_5m.astype({'Open': float, 'High': float, 'Low': float, 'Close': float, 'Volume': int})
    df_5m.set_index('ts', inplace=True)
    df_5m.sort_index(inplace=True)

    # 抓取 日 K 資料 (用於前端對齊顯示，雖然策略內部會計算昨量)
    query_daily = text('SELECT timestamp as ts, open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" FROM kbar_1d WHERE product_code=\'TXFR1\' ORDER BY timestamp ASC')
    df_daily = pd.read_sql(query_daily, db.engine)
    
    if df_daily.empty:
        # 如果沒有日K，就用 5m 重採樣
        df_daily = df_5m.resample('D').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum',
        }).dropna()
    else:
        df_daily = df_daily.astype({'Open': float, 'High': float, 'Low': float, 'Close': float, 'Volume': int})
        df_daily.set_index('ts', inplace=True)
        df_daily.sort_index(inplace=True)

    # 建立 Data Feeds
    data0 = bt.feeds.PandasData(dataname=df_5m)
    data1 = bt.feeds.PandasData(dataname=df_daily)

    result_dict = run_strategy(
        strategy_cls=MorningBreakoutShortStrategy, 
        data_feeds=[data0, data1],
        cash=init_cash,
        commission=mtx_comm,
        mult=mtx_mult,
        slippage=slippage,
        stake=1,
        plot_name=None,
        json_name=None,
        daily_data_index=1 # 使用 data1 作為前端顯示基準
    )
    
    return result_dict
