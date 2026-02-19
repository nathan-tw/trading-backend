import os
import jwt
import datetime
from datetime import date, timedelta
from flask import Flask, jsonify, request
from flask_migrate import Migrate
from flask_cors import CORS
from sqlalchemy import text
from functools import wraps
from models import db, DailySnapshot, Instrument, PortfolioHolding

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check for X-API-KEY
        api_key = os.environ.get('API_KEY')
        if request.headers.get('X-API-KEY') == api_key:
            return f(*args, **kwargs)
        
        # Check for JWT Token
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(" ")[1]
        
        if token:
            try:
                jwt.decode(token, os.environ.get('JWT_SECRET_KEY', 'fallback-secret-key'), algorithms=["HS256"])
                return f(*args, **kwargs)
            except:
                pass
                
        return jsonify({"error": "Unauthorized"}), 401
    return decorated

def create_app():
    app = Flask(__name__)
    CORS(app)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///local.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'fallback-secret-key')
    
    db.init_app(app)
    Migrate(app, db)

    # 確認資料庫連線
    with app.app_context():
        try:
            db.session.execute(text('SELECT 1'))
            print("Database connection successful!")
        except Exception as e:
            print(f"Database connection failed: {e}")

    @app.route('/', methods=['GET'])
    def index():
        return jsonify({
            "message": "Trading Room Backend API is running.",
            "status": "success",
            "frontend_port": 5173,
            "backend_port": 5001
        })

    @app.route('/api/login', methods=['POST'])
    def login():
        auth = request.json
        if not auth or not auth.get('username') or not auth.get('password'):
            return jsonify({'message': 'Missing credentials'}), 400
        
        # Simple hardcoded check for demonstration
        admin_user = os.environ.get('ADMIN_USERNAME', 'admin')
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        
        if auth.get('username') == admin_user and auth.get('password') == admin_pass:
            token = jwt.encode({
                'user': admin_user,
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            }, app.config['JWT_SECRET_KEY'], algorithm="HS256")
            
            return jsonify({'token': token})
        
        return jsonify({'message': 'Invalid credentials'}), 401

    @app.route('/api/admin/update-assets', methods=['POST'])
    @require_auth
    def update_assets():
        """
        Admin route to update or add assets.
        Expects a list of assets: [{symbol, market, quantity, current_price}, ...]
        """
        data = request.json
        if not isinstance(data, list):
            return jsonify({"error": "Expected a list of assets"}), 400
        
        try:
            processed_instrument_ids = []
            
            for item in data:
                symbol = item.get('symbol')
                market = item.get('market')
                qty = float(item.get('quantity', 0))
                price = float(item.get('current_price', 0))
                
                if not symbol or not market:
                    continue
                
                instrument = Instrument.query.filter_by(symbol=symbol, market=market).first()
                if not instrument:
                    instrument = Instrument(symbol=symbol, market=market, name=symbol)
                    db.session.add(instrument)
                    db.session.flush()
                
                processed_instrument_ids.append(instrument.id)
                
                holding = PortfolioHolding.query.filter_by(instrument_id=instrument.id).first()
                if holding:
                    holding.quantity = qty
                    holding.current_price = price
                else:
                    new_holding = PortfolioHolding(
                        instrument_id=instrument.id,
                        quantity=qty,
                        average_cost=price,
                        current_price=price
                    )
                    db.session.add(new_holding)
            
            # Remove any holdings that were NOT in the provided list
            # This handles the "remove" functionality from the UI
            if processed_instrument_ids:
                PortfolioHolding.query.filter(~PortfolioHolding.instrument_id.in_(processed_instrument_ids)).delete(synchronize_session=False)
            else:
                # If the list is empty, remove all holdings
                PortfolioHolding.query.delete()
                
            db.session.commit()
            return jsonify({"message": "Assets updated successfully"})
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route('/api/portfolio/trade', methods=['POST'])
    @require_auth
    def execute_trade():
        """
        執行交易並更新持倉
        邏輯：
        1. BUY: 增加數量，重新計算平均成本 (加權平均)。
        2. SELL: 減少數量，平均成本不變 (只計算實現損益，但這裡先專注更新庫存)。
        """
        data = request.json
        
        # 1. 驗證資料
        required_fields = ['symbol', 'market', 'side', 'quantity', 'price']
        if not all(field in data for field in required_fields):
            return jsonify({"error": "Missing fields"}), 400

        symbol = data['symbol']
        market = data['market']
        side = data['side'].upper() # BUY / SELL
        trade_qty = float(data['quantity'])
        trade_price = float(data['price'])

        try:
            # 2. 查找或建立商品 (Instrument)
            # 如果是第一次買這檔股票，系統自動建立 Instrument
            instrument = Instrument.query.filter_by(symbol=symbol, market=market).first()
            if not instrument:
                if side == 'SELL':
                    return jsonify({"error": "Cannot sell an instrument you don't own"}), 400
                
                instrument = Instrument(symbol=symbol, market=market, name=symbol) # Name 暫時用 symbol 代替
                db.session.add(instrument)
                db.session.flush() # 為了拿到 instrument.id

            # 3. 查找目前持倉 (Holding)
            holding = PortfolioHolding.query.filter_by(instrument_id=instrument.id).first()

            if not holding:
                # 如果沒有持倉
                if side == 'SELL':
                    return jsonify({"error": "Position not found"}), 400
                
                # 建立新持倉
                new_holding = PortfolioHolding(
                    instrument_id=instrument.id,
                    quantity=trade_qty,
                    average_cost=trade_price,
                    current_price=trade_price # 假設現價等於成交價
                )
                db.session.add(new_holding)
                holding = new_holding
                msg = f"Opened new position: {symbol}"

            else:
                # 已有持倉，進行更新
                current_qty = float(holding.quantity)
                current_avg_cost = float(holding.average_cost)

                if side == 'BUY':
                    # === 買進邏輯 (加權平均) ===
                    # 新總成本 = (舊數量 * 舊均價) + (新數量 * 新買價)
                    total_cost = (current_qty * current_avg_cost) + (trade_qty * trade_price)
                    new_qty = current_qty + trade_qty
                    new_avg_cost = total_cost / new_qty
                    
                    holding.quantity = new_qty
                    holding.average_cost = new_avg_cost
                    holding.current_price = trade_price # 更新現價
                    msg = f"Added to position: {symbol}. New Cost: {new_avg_cost:.2f}"

                elif side == 'SELL':
                    # === 賣出邏輯 ===
                    if current_qty < trade_qty:
                        return jsonify({"error": "Not enough quantity to sell"}), 400
                    
                    new_qty = current_qty - trade_qty
                    
                    if new_qty == 0:
                        # 如果賣光了，可以選擇刪除持倉或保留數量為 0
                        db.session.delete(holding)
                        msg = f"Closed position: {symbol}"
                    else:
                        # 賣出時，平均成本「不變」，只減少數量
                        holding.quantity = new_qty
                        holding.current_price = trade_price
                        msg = f"Reduced position: {symbol}"

            # 4. 提交交易
            db.session.commit()
            return jsonify({"message": msg, "current_holding": {
                "symbol": symbol,
                "quantity": float(holding.quantity) if holding else 0
            }})

        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500 

    @app.route('/api/snapshots/check', methods=['GET'])
    @require_auth
    def check_snapshot():
        snapshot_date = request.args.get('date')
        if not snapshot_date:
            return jsonify({"error": "Date is required"}), 400
        
        snapshot = DailySnapshot.query.filter_by(snapshot_date=snapshot_date).first()
        return jsonify({"exists": snapshot is not None})

    @app.route('/api/snapshots', methods=['POST'])
    @require_auth
    def create_snapshot():
        data = request.json
        try:
            # Check if exists to avoid double creation
            snapshot_date = data.get('snapshot_date')
            existing = DailySnapshot.query.filter_by(snapshot_date=snapshot_date).first()
            
            if existing:
                return jsonify({"message": "Snapshot already exists"}), 409

            snapshot = DailySnapshot(
                snapshot_date=snapshot_date,
                total_net_worth=data.get('total_net_worth'),
                equity_us=data.get('equity_us', 0),
                equity_tw=data.get('equity_tw', 0),
                equity_futures=data.get('equity_futures', 0),
                cash_balance=data.get('cash_balance', 0),
                usd_twd_rate=data.get('usd_twd_rate'),
                holdings_snapshot=data.get('holdings_snapshot')
            )
            db.session.add(snapshot)
            db.session.commit()
            return jsonify({"message": "Snapshot created successfully"}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route('/api/assets/overview', methods=['GET'])
    @require_auth
    def get_assets_overview():
        """
        Returns current asset overview.
        Structure: [{ symbol: 'NVDA', value_twd: 450000, market: 'US', quantity: 10, average_cost: 120, current_price: 150 }, ...]
        """
        holdings = PortfolioHolding.query.all()
        data = []
        usd_rate = 32.5 # Hardcoded for now, should be dynamic later

        for h in holdings:
            instrument = h.instrument
            market_val = float(h.quantity) * float(h.current_price)
            
            # Simple FX conversion
            val_twd = market_val * usd_rate if instrument.market == 'US' else market_val

            data.append({
                "symbol": instrument.symbol,
                "value_twd": round(val_twd, 2),
                "market": instrument.market,
                "quantity": float(h.quantity),
                "average_cost": float(h.average_cost) if h.average_cost else 0,
                "current_price": float(h.current_price) if h.current_price else 0
            })
            
        return jsonify(data)

    @app.route('/api/assets/history', methods=['GET'])
    @require_auth
    def get_assets_history():
        """
        Returns daily equity history from database.
        Optional query params: start_date, end_date (YYYY-MM-DD)
        """
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        query = DailySnapshot.query

        if start_date:
            query = query.filter(DailySnapshot.snapshot_date >= start_date)
        if end_date:
            query = query.filter(DailySnapshot.snapshot_date <= end_date)

        snapshots = query.order_by(DailySnapshot.snapshot_date.asc()).all()
        
        history = []
        for s in snapshots:
            history.append({
                "snapshot_date": s.snapshot_date.isoformat(),
                "total_net_worth": float(s.total_net_worth),
                "equity_us": float(s.equity_us),
                "equity_tw": float(s.equity_tw),
                "equity_futures": float(s.equity_futures),
                "cash_balance": float(s.cash_balance),
                "usd_twd_rate": float(s.usd_twd_rate) if s.usd_twd_rate else None
            })
        
        return jsonify(history)
        
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
