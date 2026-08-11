"""
数据存储层冒烟测试
"""
import tempfile
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage import init_db, store_snapshot, load_history, store_alerts, get_recent_alerts


def test_init_db():
    """测试数据库初始化"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        init_db(db_path)
        # 验证文件被创建
        assert os.path.exists(db_path)
    finally:
        os.unlink(db_path)


def test_store_and_load_snapshot():
    """测试存储和加载快照"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        init_db(db_path)

        # 存储测试数据
        test_row = {
            "date": "2024-07-21",
            "symbol": "MU",
            "close": 150.5,
            "volume": 1000000,
            "vwap": 151.0
        }
        store_snapshot(test_row, db_path)

        # 再次存储同一数据（幂等测试）
        store_snapshot(test_row, db_path)

        # 加载数据
        history = load_history("MU", days=10, db_path=db_path)
        assert len(history) == 1
        assert history[0]["symbol"] == "MU"
        assert history[0]["close"] == 150.5
    finally:
        os.unlink(db_path)


def test_store_and_get_alerts():
    """测试存储和获取提醒"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        init_db(db_path)

        # 存储测试提醒
        today = datetime.now().strftime("%Y-%m-%d")
        test_alerts = [
            {
                "date": today,
                "symbol": "MU",
                "rule": "volume_spike",
                "severity": "HIGH",
                "message": "成交量异常放大",
                "value": 2.5
            }
        ]
        store_alerts(test_alerts, db_path)

        # 获取提醒
        alerts = get_recent_alerts(symbol="MU", days=7, db_path=db_path)
        assert len(alerts) == 1
        assert alerts[0]["rule"] == "volume_spike"
    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    print("运行 storage 冒烟测试...")
    test_init_db()
    print("✓ init_db 测试通过")

    test_store_and_load_snapshot()
    print("✓ store_snapshot/load_history 测试通过")

    test_store_and_get_alerts()
    print("✓ store_alerts/get_recent_alerts 测试通过")

    print("\n所有 storage 冒烟测试通过！")
