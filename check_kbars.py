import pandas as pd
from app import create_app
from models import db, Kbar1M, Kbar5M, Kbar15M, Kbar1H, Kbar4H, Kbar1D, Kbar1W, Kbar1Mo
from sqlalchemy import func

def check_data():
    models = {
        '1m': Kbar1M, '5m': Kbar5M, '15m': Kbar15M,
        '1h': Kbar1H, '4h': Kbar4H, '1d': Kbar1D,
        '1w': Kbar1W, '1Mo': Kbar1Mo
    }
    
    app = create_app()
    with app.app_context():
        print("=== 資料庫 K 線資料檢查報告 ===")
        for name, model in models.items():
            # 取得總筆數
            total_count = db.session.query(func.count(model.id)).scalar()
            
            if total_count == 0:
                print(f"[{name}] 資料庫目前尚無資料！")
                continue
                
            # 取得最早與最晚時間
            min_time = db.session.query(func.min(model.timestamp)).scalar()
            max_time = db.session.query(func.max(model.timestamp)).scalar()
            
            # 檢查是否有重複的 timestamp
            duplicate_count = db.session.query(
                model.timestamp
            ).group_by(
                model.timestamp
            ).having(
                func.count(model.id) > 1
            ).count()
            
            print(f"[{name:>3}] 總筆數: {total_count:<8} | 開始: {min_time} | 結束: {max_time} | 重複時間點數量: {duplicate_count}")

            if "2022" not in str(min_time):
                print(f"    ⚠️ 警告：最早資料不是從 2022 開始！")
            if duplicate_count > 0:
                print(f"    ❌ 錯誤：發現 {duplicate_count} 個重複的時間點！")
        print("================================")

if __name__ == "__main__":
    check_data()
