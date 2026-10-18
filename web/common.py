"""Web 面板共享代码：DB 路径、初始化、版块映射、数据加载封装。

所有页面通过 `from web import common` 复用，避免重复代码。
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.schema import INDEX_TRIGGER_LABELS
from src.storage import store

# 锚定项目根（与 _load_env 同款 dirname 模式），避免手动从其他目录
# streamlit run 时在错误 CWD 静默新建空库
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "data", "fundflow.db")

# 11 SPDR 版块 ETF + 太空（UFO 代理）+ 中文名
SECTORS = {
    "XLK": "科技", "XLE": "能源", "XLF": "金融", "XLV": "医疗",
    "XLY": "可选消费", "XLP": "必需消费", "XLI": "工业",
    "XLB": "材料", "XLRE": "房地产", "XLU": "公用事业", "XLC": "通信",
    "UFO": "太空",
}

# 监控个股 + 中文名（RS vs SPY，与版块同基准）
STOCKS = {
    "SKHY": "SK海力士 ADR", "MU": "美光科技",
    "SNDK": "闪迪 SanDisk", "AAPL": "苹果 Apple",
    "SPCX": "SpaceX",
    # 美股七姐妹（2026-08-29 新增，AAPL 原已在列）
    "MSFT": "微软 Microsoft", "NVDA": "英伟达 NVIDIA",
    "GOOGL": "谷歌 Alphabet", "META": "Meta 平台",
    "AMZN": "亚马逊 Amazon", "TSLA": "特斯拉 Tesla",
}

# 三大指数 + 中文名（指数观察模块）
INDEXES = {"GSPC": "标普500", "IXIC": "纳斯达克综合", "DJI": "道琼斯工业"}

# 指数触发类型标签（与 src.schema.INDEX_TRIGGER_LABELS 同源，勿单边改动）
TRIGGER_LABELS = INDEX_TRIGGER_LABELS


def _load_env():
    """从 .env 加载环境变量到 os.environ。

    Streamlit 进程不自动 source .env（cron 已通过 `set -a && . ./.env` source），
    此处用 setdefault 不覆盖已存在的环境变量。
    """
    env_path = os.path.join(ROOT_DIR, ".env")
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


def clear_data_cache():
    """清空数据缓存（扫描成功后 / 测试切换 DB 时调用）。"""
    st.cache_data.clear()


@st.cache_data(ttl=300)
def load_snapshots():
    """最新版块快照（含 RS 指标）。"""
    return store.get_latest_sector_snapshots(DB_PATH)


@st.cache_data(ttl=300)
def load_signals(days=30):
    """轮动信号列表。"""
    return store.get_rotation_events(days=days, db_path=DB_PATH)


def count_events():
    """历史事件库总数。"""
    return store.count_history_events(DB_PATH)


def query_history(symbol, rs_momentum=None):
    """查历史相似事件（rs_momentum=None 时返回该版块全部事件）。"""
    return store.query_history_events(symbol, rs_momentum, db_path=DB_PATH)


@st.cache_data(ttl=300)
def load_sector_history(symbol, days=120):
    """单版块快照历史（用于 RS 走势图）。"""
    return store.load_sector_history(symbol, days=days, db_path=DB_PATH)


@st.cache_data(ttl=300)
def load_all_sector_histories(days=30):
    """所有版块的历史快照 {symbol: [snapshot,...]}（升序，旧->新）。

    用于 RRG 轨迹图：每版块最近 N 天的 RS 点连成线，看版块移动趋势。
    """
    return {sym: store.load_sector_history(sym, days=days, db_path=DB_PATH) for sym in SECTORS}


@st.cache_data(ttl=300)
def load_stock_snapshots():
    """最新个股快照（含 RS 指标）。"""
    return store.get_latest_stock_snapshots(DB_PATH)


@st.cache_data(ttl=300)
def load_stock_history(symbol, days=120):
    """个股历史快照（升序，旧->新）。"""
    return store.load_stock_history(symbol, days=days, db_path=DB_PATH)


@st.cache_data(ttl=300)
def load_all_stock_histories(days=30):
    """所有个股历史快照 {symbol: [snapshot,...]}（升序，旧->新），用于个股 RRG 轨迹图。"""
    return {sym: store.load_stock_history(sym, days=days, db_path=DB_PATH) for sym in STOCKS}


# =====================================================================
# 指数观察模块加载函数
# =====================================================================

@st.cache_data(ttl=300)
def load_index_snapshots():
    """最新指数快照（指数本体 + 代理 + 杠杆 ETF，含 kind 列）。"""
    return store.get_latest_index_snapshots(DB_PATH)


@st.cache_data(ttl=300)
def load_index_history(symbol, days=250):
    """单指数/ETF 历史快照（升序，旧->新），用于回撤时间线。"""
    return store.load_index_history(symbol, days=days, db_path=DB_PATH)


@st.cache_data(ttl=300)
def load_index_alerts(days=30):
    """最近指数信号（alerts 表 rule LIKE 'index_%'）。"""
    return store.get_index_alerts(days=days, db_path=DB_PATH)


@st.cache_data(ttl=300)
def count_index_events():
    """指数历史事件库总数。"""
    return store.count_index_events(DB_PATH)


@st.cache_data(ttl=300)
def load_index_events(symbol=None, trigger_type=None, limit=300):
    """指数历史事件列表（按日期倒序，供统计表/明细展示）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        query = ("SELECT event_date, symbol, trigger_type, rs_momentum, vol_zscore, "
                 "cmf, dd20, ret_5d, ret_10d, ret_20d, ret_60d, max_dd_20d, "
                 "duration_days, spy_above_ma200 FROM index_history_events")
        params = []
        conds = []
        if symbol:
            conds.append("symbol = ?")
            params.append(symbol)
        if trigger_type:
            conds.append("trigger_type = ?")
            params.append(trigger_type)
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY event_date DESC LIMIT ?"
        params.append(limit)
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()


@st.cache_data(ttl=300)
def index_event_stats():
    """各指数 × 触发类型事件统计（事件数/平均持续/平均收益/胜率/平均最大回撤）。

    胜率口径按类型: pullback_start 为 max_dd_20d <= -5% 命中率，
    capital_withdrawal 为 ret_20d <= -2% 比例，其余为 ret_20d >= 2% 比例
    （与 query_index_probability 同口径）。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            """SELECT symbol, trigger_type, COUNT(*) AS 事件数,
                      AVG(duration_days) AS 平均持续天数,
                      AVG(ret_20d) AS 平均20日收益,
                      AVG(max_dd_20d) AS 平均最大回撤,
                      SUM(CASE WHEN trigger_type = 'pullback_start' AND max_dd_20d <= -0.05 THEN 1 ELSE 0 END
                          + CASE WHEN trigger_type = 'capital_withdrawal' AND ret_20d <= -0.02 THEN 1 ELSE 0 END
                          + CASE WHEN trigger_type NOT IN ('pullback_start', 'capital_withdrawal')
                                 AND ret_20d >= 0.02 THEN 1 ELSE 0 END) AS 命中数
               FROM index_history_events
               GROUP BY symbol, trigger_type ORDER BY symbol, 事件数 DESC""",
            conn,
        )
        if df.empty:
            return df
        df["指数"] = df["symbol"].map(INDEXES).fillna(df["symbol"])
        df["触发类型"] = df["trigger_type"].map(INDEX_TRIGGER_LABELS).fillna(df["trigger_type"])
        df["平均20日收益%"] = (df["平均20日收益"] * 100).round(2)
        df["平均最大回撤%"] = (df["平均最大回撤"] * 100).round(2)
        df["命中率%"] = (df["命中数"] / df["事件数"] * 100).round(1)
        return df.drop(columns=["平均20日收益", "平均最大回撤"])
    finally:
        conn.close()


def query_index_probability(symbol, trigger_type, spy_above_ma200=None):
    """条件概率查询（读当前阈值配置透传 index_signals；参数组合多不缓存）。"""
    import yaml
    from src.analyzers import index_signals
    with open(os.path.join(ROOT_DIR, "config", "watchlist.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return index_signals.query_index_probability(
        symbol, trigger_type, db_path=DB_PATH,
        thresholds=cfg.get("index_thresholds", {}),
        spy_above_ma200=spy_above_ma200)


def filter_valid_histories(histories, min_points=2):
    """筛出 RS 有效点 >= min_points 的标的，返回 (valid: {sym: hist}, dropped: [sym,...])。

    统一 `is not None` 语义（旧代码部分用 truthy，rs_ratio=0.0 会被误滤）；
    判定与 rrg_chart.render_rrg_trail 内部一致（rs_ratio 与 rs_momentum 均非空）。
    """
    valid, dropped = {}, []
    for sym, hist in histories.items():
        pts = [x for x in hist
               if x.get("rs_ratio") is not None and x.get("rs_momentum") is not None]
        if len(pts) >= min_points:
            valid[sym] = hist
        else:
            dropped.append(sym)
    return valid, dropped


def format_dropped(dropped):
    """被 RRG 图过滤标的的可读提示；空列表返回空串。"""
    if not dropped:
        return ""
    return f"未显示: {'、'.join(dropped)}（RS 有效点不足 2 天，多为采集失败/限流）"


def rs_snapshots(snapshots):
    """剔除 SPY 基准与 RS 缺失快照，返回可用于象限展示的列表。"""
    return [s for s in snapshots
            if s.get("symbol") != "SPY"
            and s.get("rs_ratio") is not None and s.get("rs_momentum") is not None]


# 状态表统一列（app 总览 + RRG 页共用）
SECTOR_TABLE_COLS = ["date", "symbol", "sector", "象限", "close", "volume",
                     "rs_ratio", "rs_momentum", "mfi", "cmf", "vol_zscore"]


def render_sector_table(snapshots):
    """渲染版块状态表（含象限列）；返回过滤后的快照列表（空列表=无数据）。"""
    rs = rs_snapshots(snapshots)
    if not rs:
        return rs
    from web.rrg_chart import quadrant
    df = pd.DataFrame(rs)
    df["象限"] = df.apply(lambda r: quadrant(r.get("rs_ratio"), r.get("rs_momentum")), axis=1)
    st.dataframe(df[[c for c in SECTOR_TABLE_COLS if c in df.columns]],
                 width="stretch", hide_index=True)
    return rs


def render_rs_chart(hist_df, height=420):
    """RS 双轴走势图（RS-Ratio 左轴 / RS-Momentum 右轴），版块详情与个股详情共用。

    Args:
        hist_df: DataFrame，需含 date / rs_ratio / rs_momentum 列（升序）
    """
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["rs_ratio"],
                             name="RS-Ratio", line=dict(color="#1f77b4")))
    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["rs_momentum"],
                             name="RS-Momentum", yaxis="y2", line=dict(color="#ff7f0e")))
    fig.add_hline(y=100, line_dash="dash", line_color="#1f77b4")
    fig.add_hline(y=0, line_dash="dash", line_color="#ff7f0e")
    fig.update_layout(
        yaxis=dict(title="RS-Ratio（>100 = 相对自身均值上方，非跑赢大盘）"),
        yaxis2=dict(title="RS-Momentum（>0 上升）", overlaying="y", side="right"),
        height=height, hovermode="x unified",
        legend=dict(orientation="h", y=1.1),
    )
    return fig


def latest_snapshot_date(snapshots):
    """快照列表中的最新日期（各标的日期可能不齐，取 max 而非第一行）。"""
    return max((s.get("date") or "" for s in snapshots), default="")


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
    """各版块事件统计（事件数/平均持续/平均收益/持续概率）。持续定义 = 事件后 20 交易日超额收益 ≥ 2%（现行标准）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            """SELECT symbol, sector, COUNT(*) AS 事件数,
                      AVG(duration_days) AS 平均持续天数,
                      AVG(ret_10d) AS 平均10日超额收益,
                      SUM(CASE WHEN ret_20d >= 0.02 THEN 1 ELSE 0 END) AS 持续事件数
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


@st.cache_data(ttl=3600)
def stage_stats_cached(symbol):
    """阶段历史类比统计（腾讯全史逐日判阶段，1 小时缓存；重计算故长 TTL）。"""
    from src.collectors import index_price
    from src.analyzers.index_stage import stage_history_stats
    df, _source = index_price.fetch_full_history_with_source(symbol)
    if df.empty:
        return {}
    return stage_history_stats(df["close"])


def stage_stats_frame(symbol, current_stage=None):
    """阶段统计转展示 DataFrame（当前阶段行排首 + ★ 标记；百分比格式化）。"""
    from src.analyzers.index_stage import STAGE_LABELS
    stats = stage_stats_cached(symbol)
    if not stats:
        return None
    stages = list(stats.keys())
    ordered = ([current_stage] if current_stage in stages else []) + \
              [s for s in stages if s != current_stage]
    rows = []
    for stage in ordered:
        s = stats[stage]
        rows.append({
            "阶段": ("★ " if stage == current_stage else "") + STAGE_LABELS.get(stage, stage),
            "样本n(20日)": s.get("n20"),
            "20日中位收益": _pct(s.get("median_ret_20d")),
            "20日上涨占比": _pct(s.get("up_ratio_20d")),
            "60日中位收益": _pct(s.get("median_ret_60d")),
            "样本n(60日)": s.get("n60"),
            "平均持续天数": round(s["avg_duration_days"], 1) if s.get("avg_duration_days") else None,
        })
    return pd.DataFrame(rows)


def _pct(v):
    return None if v is None else f"{v * 100:+.1f}%"


@st.cache_data(ttl=1800)
def stage_conclusion_cached(symbol):
    """阶段判定（30 分钟缓存；DB 不足时回退腾讯采集，网络调用需缓存防每次渲染拉取）。"""
    from src.analyzers.index_stage import stage_conclusion
    return stage_conclusion(symbol, DB_PATH)
