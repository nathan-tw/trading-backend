import backtrader as bt

class SmaCross(bt.Strategy):
    """
    A simple Moving Average Crossover strategy.
    
    Parameters:
    - pfast (int): Period for the fast moving average. Default: 10.
    - pslow (int): Period for the slow moving average. Default: 30.
    """
    params = dict(
        pfast=10,
        pslow=30
    )

    def __init__(self):
        # Create moving averages
        sma1 = bt.ind.SMA(period=self.p.pfast)
        sma2 = bt.ind.SMA(period=self.p.pslow)
        # Create a crossover indicator
        self.crossover = bt.ind.CrossOver(sma1, sma2)

    def next(self):
        # Only buy if there's no open position
        if not self.position:
            # Fast MA crosses above Slow MA
            if self.crossover > 0:
                self.buy()
        # Fast MA crosses below Slow MA
        elif self.crossover < 0:
            self.close()
