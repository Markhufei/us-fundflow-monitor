"""数据 schema 与接口约定（Leader 定义，所有 teammate 遵守）。

数据流:
  collectors -> storage.store()  写入 daily_snapshot 表（部分字段）
  analyzers  <- storage.load()   读取历史数据计算指标
  analyzers  -> storage.store_alerts() + alerts.notify()  写异动信号 + 发通知

所有模块通过本文件的常量与类型保持接口一致，禁止各自为政。
"""

# =====================================================================
# SQLite 表结构
# =====================================================================

DAILY_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_snapshot (
    date         TEXT NOT NULL,        -- YYYY-MM-DD
    symbol       TEXT NOT NULL,        -- MU / SNDK / SOXX / SMH
    close        REAL,                 -- 收盘价
    volume       INTEGER,              -- 成交量
    obv          REAL,                 -- On-Balance Volume
    cmf          REAL,                 -- Chaikin Money Flow
    mfi          REAL,                 -- Money Flow Index
    vwap         REAL,                 -- VWAP
    vol_zscore   REAL,                 -- 成交量 Z-Score
    short_ratio  REAL,                 -- FINRA 短量占比
    inst_change  REAL,                 -- 13F 机构持仓环比（季度更新）
    insider_net  REAL,                 -- Form 4 内部人净买卖 USD（事件更新）
    PRIMARY KEY (date, symbol)
);
"""

ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    rule        TEXT NOT NULL,         -- 规则名: volume_spike / price_move / ...
    severity    TEXT NOT NULL,         -- HIGH / MEDIUM / LOW
    message     TEXT NOT NULL,         -- 人类可读描述
    value       REAL,                  -- 触发值
    detail      TEXT,                  -- 明细（内部人逐笔交易等，多行）
    created_at  TEXT NOT NULL,         -- ISO 时间戳
    UNIQUE(date, symbol, rule)         -- 幂等：同日同标的同规则不重复插入
);
"""

# =====================================================================
# 版块轮动监控表结构（扩展模块）
# =====================================================================

SECTOR_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_snapshot (
    date         TEXT NOT NULL,        -- YYYY-MM-DD
    symbol       TEXT NOT NULL,        -- XLK / XLE / ... / SPY
    sector       TEXT,                 -- 科技 / 能源 / ...（SPY 为 NULL）
    close        REAL,
    volume       INTEGER,
    rs_ratio     REAL,                 -- RS-Ratio（>100 = 相对自身均值上方，非跑赢大盘）
    rs_momentum  REAL,                 -- 强度动量（正值=强度上升）
    mfi          REAL,                 -- Money Flow Index（复用 flow_indicators）
    cmf          REAL,                 -- Chaikin Money Flow
    vol_zscore   REAL,                 -- 成交量 Z-Score
    PRIMARY KEY (date, symbol)
);
"""

SECTOR_ROTATION_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_rotation_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    from_sector   TEXT,                -- 转弱版块（如 科技）
    to_sector     TEXT NOT NULL,       -- 转强版块（如 能源）
    to_symbol     TEXT NOT NULL,       -- 转强版块 ETF 代号
    probability   REAL,                -- 持续概率 0-1
    avg_duration  INTEGER,             -- 历史平均持续天数
    avg_amplitude REAL,                -- 历史平均波动幅度 %
    sample_size   INTEGER,             -- 样本量
    confidence    TEXT,                -- 高 / 中 / 低
    severity      TEXT NOT NULL,
    message       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    trigger_type  TEXT,                -- momentum_cross / rs_cross
    UNIQUE(date, from_sector, to_sector)
);
"""

SECTOR_HISTORY_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_history_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date      TEXT NOT NULL,     -- 事件触发日
    symbol          TEXT NOT NULL,     -- 版块 ETF 代号
    sector          TEXT,
    trigger_type    TEXT,              -- momentum_cross / rs_cross
    rs_momentum     REAL,              -- 触发时 RS-Momentum
    vol_zscore      REAL,              -- 触发时量能 Z-Score
    ret_5d          REAL,              -- 事件后 5 天超额收益
    ret_10d         REAL,
    ret_20d         REAL,
    ret_60d         REAL,
    duration_days   INTEGER,           -- 持续天数（超额收益>0 的连续天数）
    spy_above_ma200 INTEGER            -- 事件日 SPY 收盘 > 其 MA200（1/0/NULL，regime 分桶用）
);
CREATE INDEX IF NOT EXISTS idx_history_symbol ON sector_history_events(symbol);
CREATE INDEX IF NOT EXISTS idx_history_momentum ON sector_history_events(rs_momentum);
"""

# =====================================================================
# 个股轮动监控表结构（扩展模块：个股 RS vs SPY，与版块同基准可同图对比）
# =====================================================================

STOCK_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_snapshot (
    date         TEXT NOT NULL,        -- YYYY-MM-DD
    symbol       TEXT NOT NULL,        -- SKHY / MU / SNDK / AAPL
    name         TEXT,                 -- 个股名称
    sector_symbol TEXT,                -- 归属版块 ETF 代号（如 XLK）
    close        REAL,
    volume       INTEGER,
    rs_ratio     REAL,                 -- 相对 SPY 强度（>100 跑赢大盘，与版块同基准）
    rs_momentum  REAL,                 -- 强度动量（正值=强度上升）
    mfi          REAL,                 -- Money Flow Index（复用 flow_indicators）
    cmf          REAL,                 -- Chaikin Money Flow
    vol_zscore   REAL,                 -- 成交量 Z-Score
    PRIMARY KEY (date, symbol)
);
"""

# =====================================================================
# Collector 返回 schema（dict）
# =====================================================================
# price_volume.collect(symbol, days) ->
#     {"date": str, "symbol": str, "close": float, "volume": int, "vwap": float}
#
# sec_edgar.collect_inst(symbol) -> {"inst_change": float|None}  (按季度)
# sec_edgar.collect_insider(symbol) -> {"insider_net": float|None, "insider_transactions": list}  (按事件)
#     insider_transactions: [{date, code(A买/D卖), tcode(P/S/M/F/G/A), shares, price, amount, insider, title}]
# finra_short.collect(symbol) -> {"short_ratio": float|None}  (按日/周)
# etf_flow.collect(symbol) -> {"flow": float}  (ETF 资金净流入，仅 ETF)
#
# 所有 collector 返回值可为 None（数据不可得时），store 以 UPSERT 写入，不报错。

# =====================================================================
# Analyzer 接口
# =====================================================================
# flow_indicators.calc(df: DataFrame) -> dict
#     返回 {"obv": float, "cmf": float, "mfi": float, "vwap": float, "vol_zscore": float}
#     输入 df 需含列: close, high, low, volume, date
#
# anomaly_detector.detect(snapshot: dict, thresholds: dict) -> list[Alert]
#     snapshot 为 daily_snapshot 单行（含所有指标）
#     返回 Alert 列表，每项: {"date","symbol","rule","severity","message","value","detail"}
#
# compare.compare(today: dict, past: dict, label: str) -> dict
#     返回对比指标 {"symbol","label","price_chg_pct","volume_chg_pct","cmf_delta"}

# =====================================================================
# Severity 级别
# =====================================================================
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"
SEVERITY_ORDER = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2}


def severity_ge(a: str, b: str) -> bool:
    """a 的严重级别 >= b"""
    return SEVERITY_ORDER.get(a, 0) >= SEVERITY_ORDER.get(b, 0)
