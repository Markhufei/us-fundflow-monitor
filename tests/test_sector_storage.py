"""版块轮动存储层测试（Phase 1）。

验证 3 张新表的创建 + 幂等 UPSERT + 历史事件查询逻辑。
所有测试用 tmp_path 隔离，不依赖真实数据。
"""
import sqlite3
from datetime import datetime, timedelta

from src.storage import store


def test_init_db_creates_sector_tables(tmp_path):
    """init_db 应创建 3 张版块表。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "sector_snapshot" in tables
    assert "sector_rotation_events" in tables
    assert "sector_history_events" in tables


def test_sector_snapshot_upsert_idempotent(tmp_path):
    """同日同 ETF 重复写不产生重复行，最新值覆盖。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    row = {"date": "2026-08-10", "symbol": "XLK", "sector": "科技",
           "close": 200.0, "volume": 1000000, "rs_ratio": 102.5,
           "rs_momentum": 1.2, "mfi": 65.0, "cmf": 0.1, "vol_zscore": 1.8}
    store.store_sector_snapshot(row, db)
    row["close"] = 201.0  # 改值再写
    store.store_sector_snapshot(row, db)
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM sector_snapshot").fetchone()[0]
    close = conn.execute("SELECT close FROM sector_snapshot").fetchone()[0]
    conn.close()
    assert n == 1
    assert close == 201.0


def test_load_sector_history_ascending(tmp_path):
    """历史按日期升序返回（旧->新）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    for d in ["2026-08-01", "2026-08-05", "2026-08-10"]:
        store.store_sector_snapshot({"date": d, "symbol": "XLK", "close": 100.0}, db)
    hist = store.load_sector_history("XLK", days=10, db_path=db)
    assert [h["date"] for h in hist] == ["2026-08-01", "2026-08-05", "2026-08-10"]


def test_get_latest_sector_snapshots(tmp_path):
    """取每个 symbol 最新一条快照。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    store.store_sector_snapshot({"date": "2026-08-09", "symbol": "XLK", "close": 100.0}, db)
    store.store_sector_snapshot({"date": "2026-08-10", "symbol": "XLK", "close": 105.0}, db)
    store.store_sector_snapshot({"date": "2026-08-10", "symbol": "SPY", "close": 500.0}, db)
    latest = store.get_latest_sector_snapshots(db)
    by_sym = {r["symbol"]: r for r in latest}
    assert by_sym["XLK"]["close"] == 105.0  # 最新日
    assert by_sym["SPY"]["close"] == 500.0


def test_store_and_count_history_events(tmp_path):
    """历史事件库批量插入 + 计数。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    events = [
        {"event_date": "2020-03-23", "symbol": "XLE", "sector": "能源",
         "trigger_type": "momentum_cross", "rs_momentum": 1.5, "vol_zscore": 1.8,
         "ret_5d": 0.02, "ret_10d": 0.05, "ret_20d": 0.08, "ret_60d": 0.12,
         "duration_days": 15},
        {"event_date": "2020-11-09", "symbol": "XLE", "sector": "能源",
         "trigger_type": "rs_cross", "rs_momentum": 2.0, "vol_zscore": 2.1,
         "ret_5d": 0.03, "ret_10d": 0.06, "ret_20d": 0.10, "ret_60d": 0.15,
         "duration_days": 20},
    ]
    store.store_history_events(events, db)
    assert store.count_history_events(db) == 2


def test_query_history_events_by_momentum(tmp_path):
    """按 RS-Momentum 量级相近查询相似事件（概率查询核心）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    events = [
        {"event_date": "2020-03-23", "symbol": "XLE", "rs_momentum": 1.5, "vol_zscore": 1.8,
         "ret_10d": 0.05, "duration_days": 15},
        {"event_date": "2021-06-01", "symbol": "XLE", "rs_momentum": 5.0, "vol_zscore": 2.0,
         "ret_10d": 0.08, "duration_days": 25},
        {"event_date": "2022-01-10", "symbol": "XLK", "rs_momentum": 1.6, "vol_zscore": 1.9,
         "ret_10d": 0.04, "duration_days": 12},
    ]
    store.store_history_events(events, db)
    # 查 XLE rs_momentum=1.5 ±20% -> 范围 [1.2, 1.8]，命中 1.5（5.0 不在范围）
    similar = store.query_history_events("XLE", 1.5, momentum_tol=0.2, db_path=db)
    assert len(similar) == 1
    assert similar[0]["rs_momentum"] == 1.5
    # XLK 1.6 不应被 XLE 查询命中
    assert all(r["symbol"] == "XLE" for r in similar)


def test_query_history_events_none_momentum_returns_all(tmp_path):
    """rs_momentum=None 时返回该 symbol 全部事件。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    store.store_history_events([
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.0, "duration_days": 5},
        {"event_date": "2020-06-01", "symbol": "XLE", "rs_momentum": 3.0, "duration_days": 8},
    ], db)
    all_xle = store.query_history_events("XLE", None, db_path=db)
    assert len(all_xle) == 2


def test_store_rotation_events_idempotent(tmp_path):
    """同日同 from/to 轮动事件不重复插入。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    ts = datetime.now().isoformat()
    e = {"date": "2026-08-10", "from_sector": "科技", "to_sector": "能源",
         "to_symbol": "XLE", "probability": 0.72, "avg_duration": 18,
         "avg_amplitude": 8.2, "sample_size": 42, "confidence": "高",
         "severity": "HIGH", "message": "test", "created_at": ts}
    store.store_rotation_events([e], db)
    store.store_rotation_events([e], db)  # 重复
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM sector_rotation_events").fetchone()[0]
    conn.close()
    assert n == 1


def test_get_rotation_events_recent(tmp_path):
    """get_rotation_events 按日期过滤。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    ts = datetime.now().isoformat()
    # 动态日期: 硬编码日期会随时间滑出 get_rotation_events 的 30 天窗口（时间炸弹）
    event_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    store.store_rotation_events([
        {"date": event_date, "from_sector": "科技", "to_sector": "能源",
         "to_symbol": "XLE", "probability": 0.7, "avg_duration": 18, "avg_amplitude": 8.0,
         "sample_size": 40, "confidence": "高", "severity": "HIGH",
         "message": "m1", "created_at": ts},
    ], db)
    recent = store.get_rotation_events(days=30, db_path=db)
    assert len(recent) >= 1
    assert recent[0]["to_sector"] == "能源"


def test_top2_migration_and_trigger_type_roundtrip(tmp_path):
    """init_db 建库含新列；rotation 事件 trigger_type 落库可读回（Top2）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    conn = sqlite3.connect(db)
    rot_cols = [r[1] for r in conn.execute("PRAGMA table_info(sector_rotation_events)").fetchall()]
    hist_cols = [r[1] for r in conn.execute("PRAGMA table_info(sector_history_events)").fetchall()]
    conn.close()
    assert "trigger_type" in rot_cols
    assert "spy_above_ma200" in hist_cols

    ts = datetime.now().isoformat()
    d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    store.store_rotation_events([
        {"date": d, "from_sector": "科技", "to_sector": "能源", "to_symbol": "XLE",
         "probability": 0.3, "avg_duration": 15, "avg_amplitude": 5.0,
         "sample_size": 20, "confidence": "中", "severity": "MEDIUM",
         "message": "m", "created_at": ts, "trigger_type": "rs_cross"},
    ], db)
    events = store.get_rotation_events(days=30, db_path=db)
    assert events[0]["trigger_type"] == "rs_cross"
