from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON
from datetime import datetime

# 初始化 DB 物件
db = SQLAlchemy()

# 1. 商品表 (Instruments)
class Instrument(db.Model):
    __tablename__ = 'instruments'

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False)
    market = db.Column(db.String(10), nullable=False) # US, TW, FUTURES
    name = db.Column(db.String(100))
    currency = db.Column(db.String(5), default='TWD')
    type = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 設定唯一鍵
    __table_args__ = (db.UniqueConstraint('symbol', 'market', name='uq_instrument'),)
    
    # 建立關聯
    holding = db.relationship('PortfolioHolding', backref='instrument', uselist=False, cascade="all, delete-orphan")
    transactions = db.relationship('Transaction', backref='instrument', lazy=True, order_by="Transaction.transaction_date.desc()")

# 2. 持倉表 (PortfolioHoldings) - 這是 Transaction 計算後的結果 (Read Model)
class PortfolioHolding(db.Model):
    __tablename__ = 'portfolio_holdings'

    id = db.Column(db.Integer, primary_key=True)
    instrument_id = db.Column(db.Integer, db.ForeignKey('instruments.id'), unique=True, nullable=False)
    
    quantity = db.Column(db.Numeric(15, 4), nullable=False)
    average_cost = db.Column(db.Numeric(15, 4))
    current_price = db.Column(db.Numeric(15, 4))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# 3. 交易紀錄表 (Transactions) - 真相來源 (Source of Truth)
class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    instrument_id = db.Column(db.Integer, db.ForeignKey('instruments.id'), nullable=False)
    
    side = db.Column(db.String(10), nullable=False) # BUY, SELL
    quantity = db.Column(db.Numeric(15, 4), nullable=False)
    price = db.Column(db.Numeric(15, 4), nullable=False)
    
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)
    reason = db.Column(db.Text) # 支援 Markdown 紀錄
    tags = db.Column(JSON)     # 標籤陣列
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# 4. 每日快照表 (DailySnapshots)
class DailySnapshot(db.Model):
    __tablename__ = 'daily_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.Date, unique=True, nullable=False)
    
    total_net_worth = db.Column(db.Numeric(15, 2), nullable=False)
    
    equity_us = db.Column(db.Numeric(15, 2), default=0)
    equity_tw = db.Column(db.Numeric(15, 2), default=0)
    equity_futures = db.Column(db.Numeric(15, 2), default=0)
    cash_balance = db.Column(db.Numeric(15, 2), default=0)
    
    usd_twd_rate = db.Column(db.Numeric(10, 4))
    
    # 使用 PostgreSQL 專用的 JSONB 格式
    holdings_snapshot = db.Column(JSON)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)