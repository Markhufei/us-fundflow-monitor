"""指数模块存储层测试（红态验收，镜像 test_sector_storage.py）。

覆盖: 新表创建 + UPSERT 幂等 + kind 三态 + 升序读取 + 每标的最新快照 +
历史事件过滤条件逐项（trigger_type / rs_momentum 容差 / spy_above_ma200 regime）+
计数 + get_index_alerts 只取 index_ 前缀规则。
query_index_events 契约: (symbol, trigger_type, rs_momentum=None, momentum_tol=0.5,
spy_above_ma200=None, db_path=...)——同 symbol+同类型必须匹配，rs 容差仅 sustained 用。
日期涉及 days/date('now') 窗口的一律动态生成（时间炸弹教训）。
"""
import sqlite3
from datetime import date, timedelta

from src.storage import store


def test_init_db_creates_index_tables(tmp_path):
    """init_db 应创建 index_snapshot + index_history_events 两张新表，快照含 kind 列。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    snap_cols = [r[1] for r in conn.execute("PRAGMA table_info(index_snapshot)").fetchall()]
    conn.close()
    assert "index_snapshot" in tables
    assert "index_history_events" in tables
    assert "kind" in snap_cols


def test_index_snapshot_upsert_idempotent(tmp_path):
    """同日同 symbol 重复写不产生重复行，最新值覆盖（幂等 UPSERT）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    row = {"date": "2026-08-10", "symbol": "GSPC", "kind": "index",
           "close": 5000.0, "volume": None, "dd20": -0.02, "dd50": -0.01,
           "ma20": 5050.0, "ma50": 5000.0, "above_ma200": 1,
           "rs_ratio": None, "rs_momentum": None, "cmf": 0.05, "mfi": 60.0,
           "vol_zscore": None}
    store.store_index_snapshot(row, db)
    row["close"] = 5100.0   # 盘后全量数据重跑覆盖
    store.store_index_snapshot(row, db)
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM index_snapshot").fetchone()[0]
    close = conn.execute("SELECT close FROM index_snapshot").fetchone()[0]
    conn.close()
    assert n == 1
    assert close == 5100.0


def test_index_snapshot_kind_roundtrip(tmp_path):
    """kind 三态（index/proxy/leveraged）落库可读回，互不覆盖（PK 含 symbol）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    for sym, kind in [("GSPC", "index"), ("SPY", "proxy"), ("SPXL", "leveraged")]:
        store.store_index_snapshot({"date": "2026-08-10", "symbol": sym,
                                    "kind": kind, "close": 100.0}, db)
    hist = store.load_index_history("SPXL", days=5, db_path=db)
    assert len(hist) == 1
    assert hist[0]["kind"] == "leveraged"
    latest = store.get_latest_index_snapshots(db)
    kinds = {r["symbol"]: r["kind"] for r in latest}
    assert kinds == {"GSPC": "index", "SPY": "proxy", "SPXL": "leveraged"}


def test_load_index_history_ascending(tmp_path):
    """历史按日期升序返回（旧->新）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    for d in ["2026-08-01", "2026-08-05", "2026-08-10"]:
        store.store_index_snapshot({"date": d, "symbol": "IXIC", "kind": "index",
                                    "close": 20000.0}, db)
    hist = store.load_index_history("IXIC", days=10, db_path=db)
    assert [h["date"] for h in hist] == ["2026-08-01", "2026-08-05", "2026-08-10"]


def test_get_latest_index_snapshots(tmp_path):
    """取每个 symbol 最新一条快照（同 symbol 多日取 max(date)）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    store.store_index_snapshot({"date": "2026-08-09", "symbol": "IXIC",
                                "kind": "index", "close": 20000.0}, db)
    store.store_index_snapshot({"date": "2026-08-10", "symbol": "IXIC",
                                "kind": "index", "close": 20100.0}, db)
    store.store_index_snapshot({"date": "2026-08-10", "symbol": "QQQ",
                                "kind": "proxy", "close": 500.0}, db)
    latest = store.get_latest_index_snapshots(db)
    by_sym = {r["symbol"]: r for r in latest}
    assert by_sym["IXIC"]["close"] == 20100.0
    assert by_sym["QQQ"]["close"] == 500.0


def test_store_and_count_index_events(tmp_path):
    """历史事件库批量插入 + 计数；绝对收益/max_dd_20d 字段落库可读回。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    events = [
        {"event_date": "2020-02-20", "symbol": "GSPC", "trigger_type": "pullback_start",
         "rs_momentum": None, "dd20": -0.052, "cmf": 0.0, "vol_zscore": None,
         "ret_5d": -0.03, "ret_10d": -0.05, "ret_20d": -0.08, "ret_60d": -0.12,
         "max_dd_20d": -0.09, "duration_days": 12, "spy_above_ma200": 1},
        {"event_date": "2020-03-24", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 0.8, "dd20": -0.18, "cmf": 0.02, "vol_zscore": 2.2,
         "ret_5d": 0.04, "ret_10d": 0.09, "ret_20d": 0.15, "ret_60d": 0.30,
         "max_dd_20d": -0.01, "duration_days": 20, "spy_above_ma200": 0},
    ]
    store.store_index_history_events(events, db)
    assert store.count_index_events(db) == 2
    rows = store.query_index_events("GSPC", "pullback_start", db_path=db)
    assert len(rows) == 1
    assert rows[0]["max_dd_20d"] == -0.09


def test_query_index_events_filters(tmp_path):
    """过滤条件逐项: trigger_type 必选 / rs_momentum ±容差 / spy_above_ma200 regime / symbol。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    store.store_index_history_events([
        {"event_date": "2020-01-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.5, "ret_20d": 0.05, "spy_above_ma200": 1},
        {"event_date": "2020-02-01", "symbol": "IXIC", "trigger_type": "pullback_start",
         "rs_momentum": 1.6, "ret_20d": -0.05, "spy_above_ma200": 1},
        {"event_date": "2020-03-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 5.0, "ret_20d": 0.02, "spy_above_ma200": 0},
        {"event_date": "2020-04-01", "symbol": "GSPC", "trigger_type": "bottom_fishing",
         "rs_momentum": None, "ret_20d": 0.03, "spy_above_ma200": 1},
    ], db)
    # trigger_type 必选过滤
    r = store.query_index_events("IXIC", "bottom_fishing", db_path=db)
    assert len(r) == 2
    # rs_momentum ±0.5 绝对容差: 命中 1.5（5.0 窗口外）
    r = store.query_index_events("IXIC", "bottom_fishing", rs_momentum=1.5,
                                 momentum_tol=0.5, db_path=db)
    assert len(r) == 1
    assert r[0]["rs_momentum"] == 1.5
    # spy_above_ma200 regime 过滤
    r = store.query_index_events("IXIC", "bottom_fishing", spy_above_ma200=0, db_path=db)
    assert len(r) == 1
    assert r[0]["rs_momentum"] == 5.0
    # symbol 隔离
    r = store.query_index_events("GSPC", "bottom_fishing", db_path=db)
    assert len(r) == 1


def test_get_index_alerts_only_index_prefix(tmp_path):
    """get_index_alerts 只取 rule 以 index_ 开头的告警，按 days 窗口过滤。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    today = date.today().isoformat()   # 动态日期: alerts 查询用 date('now', -N days)
    ts = f"{today}T00:00:00"
    store.store_alerts([
        {"date": today, "symbol": "IXIC", "rule": "index_pullback_start",
         "severity": "MEDIUM", "message": "IXIC 回撤开始", "value": -0.065,
         "created_at": ts},
        {"date": today, "symbol": "MU", "rule": "volume_spike",
         "severity": "HIGH", "message": "成交量异常放大", "value": 2.5,
         "created_at": ts},
    ], db)
    alerts = store.get_index_alerts(days=7, db_path=db)
    assert len(alerts) == 1
    assert alerts[0]["rule"] == "index_pullback_start"
    assert alerts[0]["symbol"] == "IXIC"
    # 老于窗口的 index_ 告警不返回
    old = (date.today() - timedelta(days=30)).isoformat()
    store.store_alerts([
        {"date": old, "symbol": "GSPC", "rule": "index_capital_withdrawal",
         "severity": "MEDIUM", "message": "m", "value": -0.12, "created_at": ts},
    ], db)
    assert len(store.get_index_alerts(days=7, db_path=db)) == 1
    assert len(store.get_index_alerts(days=60, db_path=db)) == 2
