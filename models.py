from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON
from sqlalchemy.orm import declared_attr
from datetime import datetime

# 初始化 DB 物件
db = SQLAlchemy()



# 5. K線資料抽象底層 (BaseKbar)
# 由於有多種時間標度，我們使用 SQLAlchemy 的 Mixin 來大幅減少重複代碼
class BaseKbar:
    id = db.Column(db.Integer, primary_key=True)
    product_code = db.Column(db.String(20), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False, index=True)
    
    open = db.Column(db.Numeric(15, 4), nullable=False)
    high = db.Column(db.Numeric(15, 4), nullable=False)
    low = db.Column(db.Numeric(15, 4), nullable=False)
    close = db.Column(db.Numeric(15, 4), nullable=False)
    volume = db.Column(db.Integer, nullable=False, default=0)
    amount = db.Column(db.Numeric(20, 2), nullable=False, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @declared_attr
    def __table_args__(cls):
        return (
            db.UniqueConstraint('product_code', 'timestamp', name=f'uq_{cls.__tablename__}'),
            db.Index(f'idx_{cls.__tablename__}_query', 'product_code', 'timestamp'),
        )

# 6. 具體的 8 種時間尺度 K 線資料表
class Kbar1M(BaseKbar, db.Model): __tablename__ = 'kbar_1m'
class Kbar5M(BaseKbar, db.Model): __tablename__ = 'kbar_5m'
class Kbar15M(BaseKbar, db.Model): __tablename__ = 'kbar_15m'
class Kbar1H(BaseKbar, db.Model): __tablename__ = 'kbar_1h'
class Kbar4H(BaseKbar, db.Model): __tablename__ = 'kbar_4h'
class Kbar1D(BaseKbar, db.Model): __tablename__ = 'kbar_1d'
class Kbar1W(BaseKbar, db.Model): __tablename__ = 'kbar_1w'
class Kbar1Mo(BaseKbar, db.Model): __tablename__ = 'kbar_1mo'