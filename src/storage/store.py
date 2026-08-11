"""
SQLite 数据存储层
处理每日快照和提醒数据的持久化
"""
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Any

from src.schema import (
    DAILY_SNAPSHOT_SCHEMA,
    ALERTS_SCHEMA,
    SECTOR_SNAPSHOT_SCHEMA,
    SECTOR_ROTATION_EVENTS_SCHEMA,
    SECTOR_HISTORY_EVENTS_SCHEMA,
)


def init_db(db_path: str = "data/fundflow.db"):
    """
    初始化数据库，创建所需的表

    Args:
        db_path: SQLite 数据库文件路径
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(DAILY_SNAPSHOT_SCHEMA)
    cursor.execute(ALERTS_SCHEMA)
    cursor.execute(SECTOR_SNAPSHOT_SCHEMA)
    cursor.execute(SECTOR_ROTATION_EVENTS_SCHEMA)
    # SECTOR_HISTORY_EVENTS_SCHEMA 含 CREATE TABLE + 2 个 CREATE INDEX，需 executescript
    cursor.executescript(SECTOR_HISTORY_EVENTS_SCHEMA)

    # 迁移：旧 alerts 表无 detail 列则添加（幂等）
    cols = [row[1] for row in cursor.execute("PRAGMA table_info(alerts)").fetchall()]
    if "detail" not in cols:
        cursor.execute("ALTER TABLE alerts ADD COLUMN detail TEXT")

    conn.commit()
    conn.close()


def store_snapshot(row: Dict[str, Any], db_path: str = "data/fundflow.db"):
    """
    存储单条每日快照数据（幂等：同日同标的重复写入不会创建重复记录）

    Args:
        row: 包含快照数据的字典
        db_path: SQLite 数据库文件路径
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 使用 INSERT OR REPLACE 实现幂等性
    cursor.execute("""
        INSERT OR REPLACE INTO daily_snapshot
        (date, symbol, close, volume, obv, cmf, mfi, vwap, vol_zscore, short_ratio, inst_change, insider_net)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row.get("date"),
        row.get("symbol"),
        row.get("close"),
        row.get("volume"),
        row.get("obv"),
        row.get("cmf"),
        row.get("mfi"),
        row.get("vwap"),
        row.get("vol_zscore"),
        row.get("short_ratio"),
        row.get("inst_change"),
        row.get("insider_net")
    ))

    conn.commit()
    conn.close()


def load_history(symbol: str, days: int = 30, db_path: str = "data/fundflow.db") -> List[Dict[str, Any]]:
    """
    加载指定标的的历史数据

    Args:
        symbol: 标的代码
        days: 获取的天数
        db_path: SQLite 数据库文件路径

    Returns:
        历史数据字典列表
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM daily_snapshot
        WHERE symbol = ?
        ORDER BY date DESC
        LIMIT ?
    """, (symbol, days))

    rows = cursor.fetchall()
    result = [dict(row) for row in rows]

    conn.close()

    # 按日期升序返回
    return list(reversed(result))


def store_alerts(alerts: List[Dict[str, Any]], db_path: str = "data/fundflow.db"):
    """
    批量存储提醒数据

    Args:
        alerts: 提醒数据字典列表
        db_path: SQLite 数据库文件路径
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for alert in alerts:
        cursor.execute("""
            INSERT OR IGNORE INTO alerts (date, symbol, rule, severity, message, value, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.get("date"),
            alert.get("symbol"),
            alert.get("rule"),
            alert.get("severity"),
            alert.get("message"),
            alert.get("value"),
            alert.get("detail"),
            alert.get("created_at", datetime.now().isoformat())
        ))

    conn.commit()
    conn.close()


def get_recent_alerts(
    symbol: Optional[str] = None,
    severity: Optional[str] = None,
    days: int = 7,
    db_path: str = "data/fundflow.db"
) -> List[Dict[str, Any]]:
    """
    获取最近的提醒

    Args:
        symbol: 可选，按标的过滤
        severity: 可选，按严重程度过滤
        days: 获取最近几天
        db_path: SQLite 数据库文件路径

    Returns:
        提醒数据字典列表
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM alerts WHERE date >= date('now', ?)"
    params = [f"-{days} days"]

    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)

    if severity:
        query += " AND severity = ?"
        params.append(severity)

    query += " ORDER BY created_at DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    result = [dict(row) for row in rows]

    conn.close()
    return result


# =====================================================================
# 版块轮动监控存储函数
# =====================================================================

def store_sector_snapshot(row: Dict[str, Any], db_path: str = "data/fundflow.db"):
    """存储版块每日快照（幂等 UPSERT：同日同 ETF 重写不重复）。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO sector_snapshot
        (date, symbol, sector, close, volume, rs_ratio, rs_momentum, mfi, cmf, vol_zscore)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row.get("date"), row.get("symbol"), row.get("sector"),
        row.get("close"), row.get("volume"),
        row.get("rs_ratio"), row.get("rs_momentum"),
        row.get("mfi"), row.get("cmf"), row.get("vol_zscore"),
    ))
    conn.commit()
    conn.close()


def load_sector_history(symbol: str, days: int = 60, db_path: str = "data/fundflow.db") -> List[Dict[str, Any]]:
    """加载某版块 ETF 历史快照（升序：旧->新）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM sector_snapshot WHERE symbol = ? ORDER BY date DESC LIMIT ?",
        (symbol, days),
    )
    rows = cursor.fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def get_latest_sector_snapshots(db_path: str = "data/fundflow.db") -> List[Dict[str, Any]]:
    """获取每个 symbol 的最新一条版块快照（含 SPY 基准）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.* FROM sector_snapshot s
        INNER JOIN (
            SELECT symbol, MAX(date) AS max_date FROM sector_snapshot GROUP BY symbol
        ) m ON s.symbol = m.symbol AND s.date = m.max_date
        ORDER BY s.symbol
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def store_history_events(events: List[Dict[str, Any]], db_path: str = "data/fundflow.db"):
    """批量存储历史事件库（建库用，非幂等；重建前应先 DELETE）。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for e in events:
        cursor.execute("""
            INSERT INTO sector_history_events
            (event_date, symbol, sector, trigger_type, rs_momentum, vol_zscore,
             ret_5d, ret_10d, ret_20d, ret_60d, duration_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            e.get("event_date"), e.get("symbol"), e.get("sector"),
            e.get("trigger_type"), e.get("rs_momentum"), e.get("vol_zscore"),
            e.get("ret_5d"), e.get("ret_10d"), e.get("ret_20d"), e.get("ret_60d"),
            e.get("duration_days"),
        ))
    conn.commit()
    conn.close()


def query_history_events(symbol: str, rs_momentum: Optional[float],
                         momentum_tol: float = 0.2,
                         db_path: str = "data/fundflow.db") -> List[Dict[str, Any]]:
    """查询相似历史事件（同版块 + RS-Momentum 量级相近 ±momentum_tol）。

    用于概率查询：新事件触发时找历史相似事件。
    rs_momentum 可能为 None，此时返回该 symbol 全部事件。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if rs_momentum is None:
        cursor.execute(
            "SELECT * FROM sector_history_events WHERE symbol = ?",
            (symbol,),
        )
    else:
        # 容差按绝对量级计算（避免负值翻转）
        span = abs(rs_momentum) * momentum_tol
        lo = rs_momentum - span
        hi = rs_momentum + span
        cursor.execute(
            "SELECT * FROM sector_history_events WHERE symbol = ? AND rs_momentum BETWEEN ? AND ?",
            (symbol, lo, hi),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_history_events(db_path: str = "data/fundflow.db") -> int:
    """历史事件库总条数（建库后验证用）。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sector_history_events")
    n = cursor.fetchone()[0]
    conn.close()
    return n


def store_rotation_events(events: List[Dict[str, Any]], db_path: str = "data/fundflow.db"):
    """存储版块轮动事件（幂等：同日同 from/to 不重复）。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for e in events:
        cursor.execute("""
            INSERT OR IGNORE INTO sector_rotation_events
            (date, from_sector, to_sector, to_symbol, probability, avg_duration,
             avg_amplitude, sample_size, confidence, severity, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            e.get("date"), e.get("from_sector"), e.get("to_sector"), e.get("to_symbol"),
            e.get("probability"), e.get("avg_duration"), e.get("avg_amplitude"),
            e.get("sample_size"), e.get("confidence"), e.get("severity"),
            e.get("message"), e.get("created_at"),
        ))
    conn.commit()
    conn.close()


def get_rotation_events(days: int = 30, db_path: str = "data/fundflow.db") -> List[Dict[str, Any]]:
    """获取最近的版块轮动事件。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM sector_rotation_events WHERE date >= date('now', ?) ORDER BY created_at DESC",
        (f"-{days} days",),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
