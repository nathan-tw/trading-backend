import os
import datetime
from datetime import date, timedelta
from flask import Flask, jsonify, request
from flask_migrate import Migrate
from flask_cors import CORS
from sqlalchemy import text
from models import db

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



    def get_kbar_model(timeframe):
        from models import Kbar1M, Kbar5M, Kbar15M, Kbar1H, Kbar4H, Kbar1D, Kbar1W, Kbar1Mo
        mapping = {
            '1m': Kbar1M,
            '5m': Kbar5M,
            '15m': Kbar15M,
            '1h': Kbar1H,
            '4h': Kbar4H,
            '1d': Kbar1D,
            '1w': Kbar1W,
            '1M': Kbar1Mo
        }
        return mapping.get(timeframe)

    @app.route('/api/kbars/check', methods=['GET'])
    def check_kbar_data():
        """
        Check if kbar data exists for a given date and timeframe.
        """
        target_date = request.args.get('date')
        timeframe = request.args.get('timeframe')
        product_code = request.args.get('product_code', 'TXFR1')
        
        if not target_date or not timeframe:
            return jsonify({"error": "date and timeframe are required"}), 400
            
        model = get_kbar_model(timeframe)
        if not model:
            return jsonify({"error": "Invalid timeframe"}), 400
            
        from sqlalchemy import func
        try:
            # Simple check: are there any records where DATE(timestamp) == target_date
            exists = model.query.filter(
                model.product_code == product_code,
                func.date(model.timestamp) == target_date
            ).first() is not None
            return jsonify({"exists": exists})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/kbars/upload', methods=['POST'])
    def upload_kbar_data():
        """
        Bulk upload kbar data.
        Accepts: {"timeframe": "1m", "product_code": "TXFR1", "data": [{timestamp, open, high, low, close, volume, amount}, ...]}
        """
        payload = request.json
        timeframe = payload.get('timeframe')
        product_code = payload.get('product_code', 'TXFR1')
        data = payload.get('data')
        
        if not timeframe or not data:
            return jsonify({"error": "timeframe and data are required"}), 400
            
        model = get_kbar_model(timeframe)
        if not model:
            return jsonify({"error": "Invalid timeframe"}), 400
            
        try:
            import dateutil.parser
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            
            for item in data:
                item['product_code'] = product_code
                if isinstance(item['timestamp'], str):
                    item['timestamp'] = dateutil.parser.isoparse(item['timestamp'])
            
            stmt = pg_insert(model).values(data)
            on_conflict_stmt = stmt.on_conflict_do_nothing(
                constraint=f'uq_{model.__tablename__}'
            )
            
            db.session.execute(on_conflict_stmt)
            db.session.commit()
            return jsonify({"message": f"Successfully processed {len(data)} {timeframe} kbars"}), 201
        except Exception as e:
            db.session.rollback()
            print(f"Error during bulk insert: {e}") 
            return jsonify({"error": str(e)}), 500


        
    @app.route('/api/backtest', methods=['POST'])
    def run_backtest_api():
        """
        Run a backtest using a specified strategy from backend/engine.
        Accepts: 
        {
          "strategy": "combined_ma_breakout", 
          "cash": 100000,
          "mult": 10,
          "commission": 15,
          "slippage": 2
        }
        """
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload provided"}), 400

        strategy_id = data.get('strategy')
        
        # Override parameters
        cash = float(data.get('cash', 100000.0))
        mult = float(data.get('mult', 10.0))
        commission = float(data.get('commission', 15.0))
        slippage = float(data.get('slippage', 2.0))

        if not strategy_id:
            return jsonify({"error": "Strategy ID is required"}), 400

        try:
            # Map frontend strategy dropdown IDs to actual engine modules
            # matching: 'combined_ma_breakout', 'morning_breakout_short', 'ma_swing'
            if strategy_id == "combined_ma_breakout":
                from engine.strategies.combined_ma_breakout_strategy import run_strategy_api
            elif strategy_id == "morning_breakout_short":
                from engine.strategies.morning_breakout_short_strategy import run_strategy_api
            elif strategy_id == "ma_swing":
                from engine.strategies.ma_swing_strategy import run_strategy_api
            else:
                return jsonify({"error": f"Strategy '{strategy_id}' not mapped in backend."}), 404

            # Execute the synchronous strategy build and get the JSON dictionary
            result_dict = run_strategy_api(
                init_cash=cash, 
                mtx_mult=mult, 
                mtx_comm=commission, 
                slippage=slippage
            )
            
            if "error" in result_dict:
                return jsonify(result_dict), 400
                
            return jsonify(result_dict), 200
        except Exception as e:
            import traceback
            return jsonify({"error": traceback.format_exc()}), 500

    @app.route('/api/kbars/<timeframe>', methods=['GET'])
    def get_kbars(timeframe):
        """
        Returns history kbars for a specific timeframe.
        Optional query params: product_code (default: TXFR1), before (UNIX timestamp), limit
        """
        model = get_kbar_model(timeframe)
        if not model:
            return jsonify({"error": "Invalid timeframe"}), 400
            
        product_code = request.args.get('product_code', 'TXFR1')
        before_ts = request.args.get('before', type=int)
        limit = request.args.get('limit', type=int, default=5000)
        
        query = model.query.filter_by(product_code=product_code)
        
        if before_ts:
            from datetime import datetime, timezone
            before_date = datetime.fromtimestamp(before_ts, tz=timezone.utc)
            query = query.filter(model.timestamp < before_date)
            
        # 1. Fetch the latest `limit` records descending (backward in time)
        records = query.order_by(model.timestamp.desc()).limit(limit).all()
        
        # 2. Reverse the array in-memory so lightweight charts receives it ascending (forward in time)
        records.reverse()
        
        results = []
        for r in records:
            results.append({
                "time": int(r.timestamp.timestamp()), # lightweight-charts prefers UNIX timestamp
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": int(r.volume),
                "amount": float(r.amount)
            })
            
        return jsonify(results)
            
        return jsonify(results)

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
