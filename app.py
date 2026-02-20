import os
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
        # Only check for X-API-KEY
        api_key = os.environ.get('API_KEY')
        if request.headers.get('X-API-KEY') == api_key:
            return f(*args, **kwargs)
                
        return jsonify({"error": "Unauthorized"}), 401
    return decorated

def create_app():
    app = Flask(__name__)
    CORS(app)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///local.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
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
        1. 紀錄 Transaction (不可變事件)
        2. 更新 PortfolioHolding (當前狀態)
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
        reason = data.get('reason', '')
        tags = data.get('tags', [])

        try:
            from models import Transaction

            # 2. 查找或建立商品 (Instrument)
            instrument = Instrument.query.filter_by(symbol=symbol, market=market).first()
            if not instrument:
                if side == 'SELL':
                    return jsonify({"error": "Cannot sell an instrument you don't own"}), 400
                
                instrument = Instrument(symbol=symbol, market=market, name=symbol)
                db.session.add(instrument)
                db.session.flush()

            # 3. 建立交易紀錄 (Transaction - Source of Truth)
            new_tx = Transaction(
                instrument_id=instrument.id,
                side=side,
                quantity=trade_qty,
                price=trade_price,
                reason=reason,
                tags=tags
            )
            db.session.add(new_tx)

            # 4. 查找並更新目前持倉 (Holding - Calculated State)
            holding = PortfolioHolding.query.filter_by(instrument_id=instrument.id).first()

            if not holding:
                if side == 'SELL':
                    return jsonify({"error": "Position not found"}), 400
                
                new_holding = PortfolioHolding(
                    instrument_id=instrument.id,
                    quantity=trade_qty,
                    average_cost=trade_price,
                    current_price=trade_price
                )
                db.session.add(new_holding)
                holding = new_holding
                msg = f"Opened new position: {symbol}"
            else:
                current_qty = float(holding.quantity)
                current_avg_cost = float(holding.average_cost)

                if side == 'BUY':
                    total_cost = (current_qty * current_avg_cost) + (trade_qty * trade_price)
                    new_qty = current_qty + trade_qty
                    new_avg_cost = total_cost / new_qty
                    
                    holding.quantity = new_qty
                    holding.average_cost = new_avg_cost
                    holding.current_price = trade_price
                    msg = f"Added to position: {symbol}. New Cost: {new_avg_cost:.2f}"
                elif side == 'SELL':
                    if current_qty < trade_qty:
                        return jsonify({"error": "Not enough quantity to sell"}), 400
                    
                    new_qty = current_qty - trade_qty
                    if new_qty == 0:
                        db.session.delete(holding)
                        msg = f"Closed position: {symbol}"
                    else:
                        holding.quantity = new_qty
                        holding.current_price = trade_price
                        msg = f"Reduced position: {symbol}"

            db.session.commit()
            return jsonify({
                "message": msg, 
                "transaction_id": new_tx.id,
                "current_holding": {
                    "symbol": symbol,
                    "quantity": float(holding.quantity) if holding else 0
                }
            })

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

    @app.route('/api/ticks/check', methods=['GET'])
    @require_auth
    def check_tick_data():
        """
        Check if tick data exists for a given date.
        """
        from models import TickData
        trade_date = request.args.get('date')
        if not trade_date:
            return jsonify({"error": "Date is required"}), 400
        
        exists = TickData.query.filter_by(trade_date=trade_date).first() is not None
        return jsonify({"exists": exists})

    @app.route('/api/ticks/upload', methods=['POST'])
    @require_auth
    def upload_tick_data():
        """
        Bulk upload tick data.
        """
        from models import TickData
        data = request.json
        if not isinstance(data, list):
            return jsonify({"error": "Expected a list of tick data"}), 400
        
        try:
            # Using bulk_insert_mappings for better performance with large datasets
            # The data objects in the list should match the column names in TickData
            db.session.bulk_insert_mappings(TickData, data)
            db.session.commit()
            return jsonify({"message": f"Successfully uploaded {len(data)} ticks"}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route('/api/transactions', methods=['GET'])
    @require_auth
    def get_transactions():
        """
        Returns all transaction records.
        """
        from models import Transaction
        transactions = Transaction.query.order_by(Transaction.transaction_date.desc()).all()
        
        results = []
        for tx in transactions:
            instrument = tx.instrument
            results.append({
                "id": tx.id,
                "symbol": instrument.symbol,
                "market": instrument.market,
                "side": tx.side,
                "quantity": float(tx.quantity),
                "price": float(tx.price),
                "transaction_date": tx.transaction_date.isoformat(),
                "reason": tx.reason,
                "tags": tx.tags or []
            })
        
        return jsonify(results)

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
