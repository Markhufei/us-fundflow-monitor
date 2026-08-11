"""Web 面板共享代码：DB 路径、初始化、版块映射、数据加载封装。

所有页面通过 `from web import common` 复用，避免重复代码。
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from src.storage import store

DB_PATH = "data/fundflow.db"

# 11 SPDR 版块 ETF + 中文名
SECTORS = {
    "XLK": "科技", "XLE": "能源", "XLF": "金融", "XLV": "医疗",
    "XLY": "可选消费", "XLP": "必需消费", "XLI": "工业",
    "XLB": "材料", "XLRE": "房地产", "XLU": "公用事业", "XLC": "通信",
}


def _load_env():
    """从 .env 加载环境变量到 os.environ。

    Streamlit 进程不自动 source .env（cron 已通过 `set -a && . ./.env` source），
    此处用 setdefault 不覆盖已存在的环境变量。
    """
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def init():
    """初始化：加载 .env 环境变量 + 数据库（幂等）。"""
    _load_env()
    store.init_db(DB_PATH)


def load_snapshots():
    """最新版块快照（含 RS 指标）。"""
    return store.get_latest_sector_snapshots(DB_PATH)


def load_signals(days=30):
    """轮动信号列表。"""
    return store.get_rotation_events(days=days, db_path=DB_PATH)


def count_events():
    """历史事件库总数。"""
    return store.count_history_events(DB_PATH)


def query_history(symbol, rs_momentum=None):
    """查历史相似事件（rs_momentum=None 时返回该版块全部事件）。"""
    return store.query_history_events(symbol, rs_momentum, db_path=DB_PATH)


def load_sector_history(symbol, days=120):
    """单版块快照历史（用于 RS 走势图）。"""
    return store.load_sector_history(symbol, days=days, db_path=DB_PATH)


def event_date_span():
    """历史事件日期跨度 (min_date, max_date)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            "SELECT MIN(event_date) min_d, MAX(event_date) max_d FROM sector_history_events",
            conn,
        )
        if df.empty or df["min_d"][0] is None:
            return None, None
        return df["min_d"][0], df["max_d"][0]
    finally:
        conn.close()


def history_stats_by_symbol():
    """各版块事件统计（事件数/平均持续/平均收益/持续概率）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            """SELECT symbol, sector, COUNT(*) AS 事件数,
                      AVG(duration_days) AS 平均持续天数,
                      AVG(ret_10d) AS 平均10日超额收益,
                      SUM(CASE WHEN duration_days >= 10 AND ret_10d >= 0.03 THEN 1 ELSE 0 END) AS 持续事件数
               FROM sector_history_events
               GROUP BY symbol ORDER BY 事件数 DESC""",
            conn,
        )
        df["平均10日超额%"] = (df["平均10日超额收益"] * 100).round(2)
        df["持续概率%"] = (df["持续事件数"] / df["事件数"] * 100).round(1)
        return df.drop(columns=["平均10日超额收益"])
    finally:
        conn.close()


def query_events_page(symbol=None, page_size=50, offset=0):
    """分页查询历史事件（按日期倒序）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        query = (
            "SELECT event_date, symbol, sector, trigger_type, rs_momentum, "
            "vol_zscore, ret_5d, ret_10d, ret_20d, ret_60d, duration_days "
            "FROM sector_history_events"
        )
        params = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY event_date DESC LIMIT ? OFFSET ?"
        params += [page_size, offset]
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()
