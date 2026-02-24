import backtrader as bt
import pandas as pd
import numpy as np
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# 解決 matplotlib 中文顯示可能出現亂碼的問題
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS'] # macOS / Windows 支援的無襯線字體
plt.rcParams['axes.unicode_minus'] = False

# 定義佣金與手續費 (假設為台指期大台)
class CommInfo_Futures(bt.CommInfoBase):
    params = (
        ('commission', 53.0),  # 手續費 18 + 期交稅 35 = 53
        ('mult', 50.0),        # 每點 50 元 (小台，大台請改 200)
        ('margin', 0), 
        ('commtype', bt.CommInfoBase.COMM_FIXED),
        ('stocklike', False),
    )

class ExposureAnalyzer(bt.Analyzer):
    def __init__(self):
        self.exposed_bars = 0
        self.total_bars = 0
    def next(self):
        self.total_bars += 1
        if self.strategy.position.size != 0:
            self.exposed_bars += 1
    def get_analysis(self):
        return {'exposure_pct': (self.exposed_bars / self.total_bars * 100) if self.total_bars > 0 else 0.0}

class CommissionAnalyzer(bt.Analyzer):
    def __init__(self):
        self.total_commission = 0.0
    def notify_order(self, order):
        if order.status in [order.Completed]:
            self.total_commission += order.executed.comm
    def get_analysis(self):
        return {'total_commission': self.total_commission}

class TradeListAnalyzer(bt.Analyzer):
    def __init__(self):
        self.trades_dict = {}
    
    def notify_trade(self, trade):
        if trade.isopen or trade.isclosed:
            # Get the broker mult to calculate pure points
            # trade.pnl is the raw dollar pnl before commissions
            mult = self.strategy.broker.getcommissioninfo(trade.data).p.mult
            orig_size = trade.history[0].status.size if len(trade.history) > 0 else 0
            calc_size = orig_size if orig_size != 0 else 1
            points = trade.pnl / (abs(calc_size) * mult) if mult > 0 else 0
            
            is_closed = trade.isclosed
            
            self.trades_dict[trade.ref] = {
                "ref": trade.ref,
                "entry_date": bt.num2date(trade.dtopen).isoformat() if trade.dtopen else "-",
                "exit_date": bt.num2date(trade.dtclose).isoformat() if is_closed and trade.dtclose else "-",
                "entry_price": trade.price,
                "exit_price": trade.history[-1].status.price if is_closed and len(trade.history) > 0 else "-",
                "size": orig_size,
                "pnl": trade.pnl,
                "pnlcomm": trade.pnlcomm,
                "points": round(points, 2),
                "is_closed": is_closed
            }
            
    def get_analysis(self):
        return list(self.trades_dict.values())

def _calculate_sortino(returns_dict, risk_free_rate=0.0):
    returns = pd.Series(returns_dict)
    if len(returns) < 2:
        return 0.0
    
    mean_return = returns.mean()
    annualized_return = mean_return * 252
    
    downside_returns = returns[returns < risk_free_rate]
    if len(downside_returns) == 0:
        return float('inf')
        
    downside_std = np.sqrt(np.mean(downside_returns**2))
    annual_downside_std = downside_std * np.sqrt(252)
    
    sortino = (annualized_return - risk_free_rate) / annual_downside_std if annual_downside_std != 0 else 0
    return sortino



def run_strategy(strategy_cls, data_df=None, data_feeds=None, cash=250000.0, commission=53.0, mult=50.0, slippage=2.0, stake=1, plot_name='backtest_result.png', json_name=None, kwargs=None):
    """
    執行 Backtrader 回測並產生詳細報告與圖表的公用框架。
    
    :param strategy_cls: Backtrader 的 Strategy 類別
    :param data_df: 準備好的 Pandas DataFrame (舊版用法)
    :param data_feeds: bt.feeds.PandasData 列表 (支援多重商品與週期)
    :param cash: 初始資金
    :param commission: 每次交易手續費
    :param mult: 合約乘數 (每點價值)
    :param slippage: 固定滑價點數
    :param stake: 固定下單口數
    :param plot_name: 輸出的圖表檔案名稱 (空字串則不產圖)
    :param kwargs: 傳遞給策略的參數字典
    
    :return: 包含完整財務指標的 Dictionary
    """
    cerebro = bt.Cerebro(tradehistory=True)
    kwargs = kwargs or {}

    if data_df is not None:
        data_ref = bt.feeds.PandasData(
            dataname=data_df,
            open='Open',
            high='High',
            low='Low',
            close='Close',
            volume='Volume',
            openinterest=-1
        )
        cerebro.adddata(data_ref)
    elif data_feeds is not None:
        for feed in data_feeds:
            cerebro.adddata(feed)
        data_ref = data_feeds[0]
    else:
        raise ValueError("Must provide either data_df or data_feeds")

    cerebro.addstrategy(strategy_cls, **kwargs)

    # 初始資金
    cerebro.broker.setcash(cash) 
    
    # 自訂手續費與合約倍數
    my_comm = CommInfo_Futures(commission=commission, mult=mult)
    cerebro.broker.addcommissioninfo(my_comm)
    
    # 固定滑價
    if slippage > 0:
        cerebro.broker.set_slippage_fixed(slippage, slip_open=True, slip_match=True, slip_out=True)
    
    # 設定下單口數
    cerebro.addsizer(bt.sizers.FixedSize, stake=stake)
    
    # 加入分析器
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, timeframe=bt.TimeFrame.Years, _name="annual_return")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, timeframe=bt.TimeFrame.Months, _name="monthly_return")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, timeframe=bt.TimeFrame.Days, _name="daily_return")
    cerebro.addanalyzer(ExposureAnalyzer, _name="exposure")
    cerebro.addanalyzer(CommissionAnalyzer, _name="commissions")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, timeframe=bt.TimeFrame.Days, annualize=True, riskfreerate=0.0, _name='sharpe')
    cerebro.addanalyzer(bt.analyzers.Transactions, _name="txns")
    cerebro.addanalyzer(TradeListAnalyzer, _name="trade_list")

    print(f'=== 策略開始執行: {strategy_cls.__name__} ===')
    print(f'初始資金: {cerebro.broker.getvalue():.2f}')
    
    strats = cerebro.run()
    strat0 = strats[0]
    
    end_cash = cerebro.broker.getvalue()

    # 1. 萃取所有指標並計算
    ta = strat0.analyzers.ta.get_analysis()
    dd = strat0.analyzers.dd.get_analysis()
    daily_returns = strat0.analyzers.daily_return.get_analysis()
    exposure_pct = strat0.analyzers.exposure.get_analysis()['exposure_pct']
    commissions = strat0.analyzers.commissions.get_analysis()['total_commission']
    try:
        sharpe_ratio = strat0.analyzers.sharpe.get_analysis()['sharperatio']
    except:
        sharpe_ratio = 0.0
    sharpe_ratio = sharpe_ratio if sharpe_ratio is not None else 0.0

    net_profit = end_cash - cash
    total_return_pct = (net_profit / cash) * 100
    
    l_data = len(strat0.data)
    dates = [bt.num2date(dt) for dt in strat0.data.datetime.get(size=l_data)]
    if len(dates) > 1:
        total_days = (dates[-1] - dates[0]).days
        years = total_days / 365.25 if total_days > 0 else 0
        cagr = ((end_cash / cash) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    else:
        years = 0
        cagr = 0.0

    total_trades = ta.total.closed if 'total' in ta and 'closed' in ta.total else 0
    mdd_pct = dd.max.drawdown
    mdd_money = dd.max.moneydown
    mdd_duration = dd.max.len
    
    largest_losing_trade = abs(ta.lost.pnl.max) if 'lost' in ta and 'pnl' in ta.lost else 0.0
    sortino = _calculate_sortino(daily_returns)
    calmar = (cagr / mdd_pct) if mdd_pct > 0 else float('inf')
    
    win_rate = (ta.won.total / total_trades * 100) if total_trades > 0 and 'won' in ta and 'total' in ta.won else 0.0
    avg_win = ta.won.pnl.average if 'won' in ta and 'pnl' in ta.won else 0.0
    avg_loss = abs(ta.lost.pnl.average) if 'lost' in ta and 'pnl' in ta.lost else 0.0
    rr_ratio = (avg_win / avg_loss) if avg_loss > 0 else float('inf')
    
    gross_profit = ta.won.pnl.total if 'won' in ta and 'pnl' in ta.won else 0.0
    gross_loss = abs(ta.lost.pnl.total) if 'lost' in ta and 'pnl' in ta.lost else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    
    expectancy = (win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss)
    
    max_consecutive_wins = ta.streak.won.longest if 'streak' in ta and 'won' in ta.streak else 0
    max_consecutive_losses = ta.streak.lost.longest if 'streak' in ta and 'lost' in ta.streak else 0
    
    comm_to_net_profit = (commissions / abs(net_profit) * 100) if net_profit != 0 else 0.0

    monthly_returns = strat0.analyzers.monthly_return.get_analysis()
    monthly_returns_export = {dt.strftime('%Y-%m'): round(v * 100, 2) for dt, v in monthly_returns.items()} if monthly_returns else {}

    annual_returns = strat0.analyzers.annual_return.get_analysis()
    annual_returns_export = {dt.strftime('%Y'): round(v * 100, 2) for dt, v in annual_returns.items()} if annual_returns else {}

    # 提取獨立交易紀錄與計算總獲利點數
    trade_list_data = strat0.analyzers.trade_list.get_analysis()
    total_net_points = sum(t['points'] for t in trade_list_data)

    # 構造完整匯出字典
    metrics_export = {
        "overview": {
            "net_profit": round(net_profit, 2),
            "total_net_points": round(total_net_points, 2),
            "total_return_pct": round(total_return_pct, 2),
                "cagr_pct": round(cagr, 2),
                "total_trades": total_trades,
                "exposure_pct": round(exposure_pct, 2)
            },
            "returns_analysis": {
                "monthly_returns": monthly_returns_export,
                "annual_returns": annual_returns_export
            },
            "risk_drawdown": {
                "mdd_pct": round(mdd_pct, 2),
                "mdd_money": round(mdd_money, 2),
                "mdd_duration_bars": mdd_duration,
                "largest_losing_trade": round(largest_losing_trade, 2)
            },
            "risk_adjusted_returns": {
                "sharpe_ratio": round(sharpe_ratio, 2),
                "sortino_ratio": round(sortino, 2),
                "calmar_ratio": round(calmar, 2)
            },
            "trade_statistics": {
                "win_rate_pct": round(win_rate, 2),
                "average_win": round(avg_win, 2),
                "average_loss": round(avg_loss, 2),
                "risk_reward_ratio": round(rr_ratio, 2),
                "profit_factor": round(profit_factor, 2),
                "expectancy": round(expectancy, 2)
            },
            "consecutive_metrics": {
                "max_consecutive_wins": max_consecutive_wins,
                "max_consecutive_losses": max_consecutive_losses
            },
            "friction_costs": {
                "total_commission": round(commissions, 2),
                "commission_to_net_profit_pct": round(comm_to_net_profit, 2),
                "slippage_pts_per_trade": slippage
            }
    }

    # 印出數據到終端機
    print("-" * 50)
    print("一、 核心績效概況 (Performance Overview)")
    print(f"  淨利 (Net Profit): {net_profit:.2f}")
    print(f"  總報酬率 (Total Return %): {total_return_pct:.2f}%")
    print(f"  年化報酬率 (CAGR): {cagr:.2f}%")
    print(f"  總交易次數 (Total Trades): {total_trades}")
    print(f"  市場曝險時間 (Exposure %): {exposure_pct:.2f}%")
    
    print("\n二、 風險與回撤評估 (Risk & Drawdown Analysis)")
    print(f"  最大回撤 (Max Drawdown, MDD): {mdd_pct:.2f}% (金額: {mdd_money:.2f})")
    print(f"  最大回撤恢復期 (Drawdown Duration): {mdd_duration} 根 K 棒")
    print(f"  單筆最大虧損 (Largest Losing Trade): {largest_losing_trade:.2f}")
    
    print("\n三、 風險調整後報酬 (Risk-Adjusted Returns)")
    print(f"  夏普比率 (Sharpe Ratio): {sharpe_ratio:.2f}")
    print(f"  索提諾比率 (Sortino Ratio): {sortino:.2f}")
    print(f"  卡瑪比率 (Calmar Ratio): {calmar:.2f}")
    
    print("\n四、 交易效率與勝率統計 (Trade Statistics)")
    print(f"  勝率 (Win Rate %): {win_rate:.2f}%")
    print(f"  平均獲利 (Average Win): {avg_win:.2f}")
    print(f"  平均虧損 (Average Loss): {avg_loss:.2f}")
    print(f"  盈虧比 (Risk-Reward Ratio): {rr_ratio:.2f}")
    print(f"  獲利因子 (Profit Factor): {profit_factor:.2f}")
    print(f"  數學期望值 (Expectancy): {expectancy:.2f} 元/次")
    
    print("\n五、 連續性與極端值分析 (Consecutive Metrics)")
    print(f"  最大連續獲利次數 (Max Consecutive Wins): {max_consecutive_wins}")
    print(f"  最大連續虧損次數 (Max Consecutive Losses): {max_consecutive_losses}")
    
    print("\n六、 收費與摩擦成本 (Friction & Execution Costs)")
    print(f"  總手續費 (Total Commission): {commissions:.2f}")
    print(f"  手續費佔淨利比 (Commission to Net Profit): {comm_to_net_profit:.2f}%")
    print(f"  總獲利點數 (Total Net Points): {total_net_points:.2f} pts")
    
    print("\n七、 年度與月份報酬率 (Annual & Monthly Returns)")
    if annual_returns_export:
        print("  [年度報酬]")
        for year, ret in annual_returns_export.items():
            print(f"    {year}: {ret:+.2f}%")
    if monthly_returns_export:
        print("  [月份報酬]")
        for month, ret in monthly_returns_export.items():
            print(f"    {month}: {ret:+.2f}%")
    print("-" * 50)
    print(f'回測結束後資金: {end_cash:.2f}')
    print('====================================\n')

    with open('/tmp/array_sizes.txt', 'w') as dbg_file:
        dbg_file.write(f"len(strat0) = {len(strat0)}\n")
        dbg_file.write(f"strat0.data buflen = {strat0.data.buflen()}\n")
        dbg_file.write(f"strat0.data.datetime len = {len(strat0.data.datetime.get(size=len(strat0)))}\n")
        if len(cerebro.runstrats[0][0].observers.broker) > 0:
             dbg_file.write(f"obs broker len = {len(cerebro.runstrats[0][0].observers.broker)}\n")
             dbg_file.write(f"obs broker get = {len(cerebro.runstrats[0][0].observers.broker.value.get(size=len(strat0)))}\n")

    # 2. 輸出資產變化圖表 (簡化為只印出文字，因為前端已經自動接管繪圖)
    if plot_name:
        print(f"前端已接管繪圖，跳過 Matplotlib 靜態圖產生以節省時間與記憶體。")

    # 3. 組合最終的 Dictionary (API Response)
    try:
        l_data = len(strat0.data)
        values = strat0.observers.broker.value.get(size=l_data)
        
        # Use l_data for dates extraction to avoid negative slicing bug
        dates = [bt.num2date(dt) for dt in strat0.data.datetime.get(size=l_data)]
        dates_iso = [dt.isoformat() for dt in dates]
        
        txns = strat0.analyzers.txns.get_analysis()
        formatted_txns = []
        for dt, txn_list in txns.items():
            for txn in txn_list:
                formatted_txns.append({
                    "date": dt.isoformat(),
                    "amount": txn[0],
                    "price": round(txn[1], 2) if len(txn) > 1 else 0.0,
                    "value": round(txn[4], 2) if len(txn) > 4 else 0.0,
                    "commission": round(txn[5], 2) if len(txn) > 5 else 0.0
                })
        
        # Extract OHLCV Data
        d_open = strat0.data.open.get(size=l_data)
        d_high = strat0.data.high.get(size=l_data)
        d_low = strat0.data.low.get(size=l_data)
        d_close = strat0.data.close.get(size=l_data)
        d_vol = strat0.data.volume.get(size=l_data)
        
        ohlcv_data = []
        min_len_data = min(len(dates_iso), len(d_open), len(d_high), len(d_low), len(d_close), len(d_vol), len(values))
        for i in range(min_len_data):
            idx = -min_len_data + i
            ohlcv_data.append({
                "time": dates_iso[idx],
                "open": round(d_open[idx], 2),
                "high": round(d_high[idx], 2),
                "low": round(d_low[idx], 2),
                "close": round(d_close[idx], 2),
                "volume": int(d_vol[idx])
            })
        
        export_data = {
            "strategy": strategy_cls.__name__,
            "metrics": metrics_export,
            "transactions": formatted_txns,
            "trades": trade_list_data,
            "equity_curve": {
                "timestamps": dates_iso,
                "values": [round(v, 2) for v in values]
            },
            "ohlcv": ohlcv_data
        }
        
        return export_data
        
    except Exception as e:
        print(f"匯出 JSON 結構時發生錯誤: {e}")
        return {"error": str(e)}
