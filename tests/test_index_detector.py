"""指数实时检测器测试（红态验收；已按 2026-10-18 实现语义对齐）。

detect_index_signals = 对 DB 历史快照（最近 index_hist_days 天）跑 identify_index_events
同一函数，过滤出 event_date == 当日 的事件，查概率生成告警 —— 建库与检测一套代码。

对齐的实现语义（与 lead 契约的差异点已上报 team-lead）:
  - 历史门槛: DB 历史不足 60 天（特征窗口未热）不出信号 -> 播种 60+ 行
  - 告警形态: {date, symbol, rule, severity, message, value=probability}，
    rule = "index_" + trigger_type；概率/样本经 message 文本 + value 断言
  - 概率口径 per-type（pullback_start=max_dd_20d 命中率），容差仅 sustained_uptrend
快照日期一律动态生成（date.today() - timedelta），杜绝硬编码日期时间炸弹。
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.storage import store

index_detector = pytest.importorskip(
    "src.analyzers.index_detector",
    reason="src/analyzers/index_detector.py 未落地（红态验收，impl-index 完成后自动启用）",
)

TH = {
    "pullback_dd": -0.05,
    "pullback_lookback": 15,
    "cmf_withdrawal": -0.10,
    "rebound_min_ret": 0.015,
    "confirm_days": 2,
    "vol_confirm": 1.5,
    "sustained_amplitude": 0.02,
    "momentum_tol_abs": 0.5,
    "prob_min_sample": 10,
    "prob_high_sample": 30,
}
PERIODS = {"rs_period": 30, "index_hist_days": 250}
NEUTRAL_DAYS = 58   # 58 天中性 + 尾部触发段 -> >60 行过实现的历史门槛


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _snap(symbol, dd20, days_ago, close=98.0, kind="index", name=None, rs_ratio=101.0,
          rs_momentum=0.5, above_ma200=0, ma20=None, ma50=None, cmf=0.0,
          vol_zscore=0.5, ret_1d=0.0):
    """构造 index_snapshot 行（identify_index_events 消费的全列 + ret_1d）。"""
    row = {"date": _d(days_ago), "symbol": symbol, "kind": kind,
           "close": close, "volume": None,
           "ret_1d": ret_1d,
           "dd20": dd20, "dd50": -0.01,
           "ma20": 101.0 if ma20 is None else ma20,
           "ma50": 99.0 if ma50 is None else ma50,
           "above_ma200": above_ma200,
           "rs_ratio": rs_ratio, "rs_momentum": rs_momentum,
           "cmf": cmf, "mfi": 55.0, "vol_zscore": vol_zscore}
    if name:
        row["name"] = name
    return row


def _seed_series(db, rows):
    for r in rows:
        store.store_index_snapshot(r, db)


def _pullback_rows(symbol, dd20_tail, name=None):
    """NEUTRAL_DAYS 天中性（dd20=-0.01，close<ma20/ma50，无任何触发）+ 尾部 dd20 序列。

    尾部 close 缓跌（ret_1d < 0）杜绝 fishing/rebound 干扰；rs_momentum 0.5 但
    above_ma200=0 阻断 sustained。
    """
    rows = []
    for i in range(NEUTRAL_DAYS):
        days_ago = NEUTRAL_DAYS + len(dd20_tail) - i
        rows.append(_snap(symbol, -0.01, days_ago, close=98.0, name=name))
    for i, dd in enumerate(dd20_tail):
        days_ago = len(dd20_tail) - 1 - i
        rows.append(_snap(symbol, dd, days_ago, close=98.0 - 0.5 * (i + 1),
                          ret_1d=-0.005, name=name))
    return rows


def _pullback_events(symbol, n, rs_momentum=0.5):
    """n 个 pullback_start 历史事件（命中率口径: max_dd_20d <= pullback_dd 计命中）。"""
    return [{"event_date": (date.today() - timedelta(days=400 * (k + 1))).isoformat(),
             "symbol": symbol, "trigger_type": "pullback_start",
             "rs_momentum": rs_momentum, "dd20": -0.06, "cmf": 0.0, "vol_zscore": None,
             "ret_20d": 0.01, "max_dd_20d": -0.08, "duration_days": 8,
             "spy_above_ma200": 1}
            for k in range(n)]


# ---------- 当日过滤与历史门槛 ----------

def test_detect_yesterday_event_not_alerted(tmp_path):
    """昨日已确认的 pullback_start 事件（今日延续但非新鲜）-> 今日不告警。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    # 事件确认日 = d-2（窗口 [d-3, d-2] fresh），d-1/今日均为段中（非新鲜）
    _seed_series(db, _pullback_rows("IXIC", [-0.06, -0.065, -0.068, -0.07]))
    cur = [_snap("IXIC", -0.07, 0, close=96.0, ret_1d=-0.005)]
    alerts = index_detector.detect_index_signals(cur, db, TH, PERIODS)
    assert alerts == []


def test_detect_fires_today(tmp_path):
    """今日新鲜确认（窗口 [d-1, 今日] fresh）-> 1 条 index_pullback_start 告警。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, _pullback_rows("IXIC", [-0.04, -0.06, -0.065]))
    cur = [_snap("IXIC", -0.065, 0, close=95.5, ret_1d=-0.005, name="纳斯达克综合")]
    alerts = index_detector.detect_index_signals(cur, db, TH, PERIODS)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["symbol"] == "IXIC"
    assert a["rule"] == "index_pullback_start"
    assert a["date"] == _d(0)
    assert a["severity"] == "MEDIUM"   # 无事件库 -> probability None 不发 HIGH


def test_detect_empty_and_insufficient_history(tmp_path):
    """空快照列表与历史不足（仅当日，未过 60 天特征窗口门槛）都返回空且不报错。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    assert index_detector.detect_index_signals([], db, TH, PERIODS) == []
    cur = [_snap("IXIC", -0.065, 0)]
    assert index_detector.detect_index_signals(cur, db, TH, PERIODS) == []


# ---------- 概率拼接与 severity ----------

def test_detect_probability_attached_severity_medium(tmp_path):
    """低样本（4 < prob_min_sample）命中 3/4 -> 命中率 75% + 低置信 + MEDIUM 不误发 HIGH。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, _pullback_rows("IXIC", [-0.04, -0.06, -0.065]))
    events = _pullback_events("IXIC", 4)
    events[3]["max_dd_20d"] = -0.01   # 第 4 个未真回撤 -> 命中 3/4
    store.store_index_history_events(events, db)
    cur = [_snap("IXIC", -0.065, 0, close=95.5, ret_1d=-0.005)]
    alerts = index_detector.detect_index_signals(cur, db, TH, PERIODS)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["value"] == pytest.approx(0.75, rel=1e-4)
    assert a["severity"] == "MEDIUM"          # 低置信 -> 不误发 HIGH（回归旧版缺陷）
    assert "样本4" in a["message"]
    assert "CI95[" in a["message"]
    assert "⚠️低置信度" in a["message"]


def test_detect_severity_high_requires_high_confidence(tmp_path):
    """30 样本全命中（p=1.0 且置信高）-> HIGH。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, _pullback_rows("IXIC", [-0.04, -0.06, -0.065]))
    store.store_index_history_events(_pullback_events("IXIC", 30), db)
    cur = [_snap("IXIC", -0.065, 0, close=95.5, ret_1d=-0.005)]
    alerts = index_detector.detect_index_signals(cur, db, TH, PERIODS)
    assert alerts[0]["severity"] == "HIGH"
    assert alerts[0]["value"] == pytest.approx(1.0)
    assert "样本30" in alerts[0]["message"]


# ---------- GSPC 纯回撤路径 ----------

def test_detect_gspc_null_rs_sustained_uptrend(tmp_path):
    """GSPC rs 全 NULL: fresh 上穿 MA50 + MA50 上行 + above_ma200 仍可触发 sustained；
    概率走 rs_momentum=NULL 无容差路径（同 symbol+trigger_type 全部事件，样本 2）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    rows = []
    # 中性段: close 97 < ma50 99（不上穿），rs 全 NULL，above_ma200=1
    for i in range(NEUTRAL_DAYS):
        days_ago = NEUTRAL_DAYS + 4 - i
        rows.append(_snap("GSPC", -0.01, days_ago, close=97.0, rs_ratio=None,
                          rs_momentum=None, above_ma200=1, ma20=96.5, ma50=99.0,
                          cmf=0.05, vol_zscore=0.5))
    # 尾部: ma50 上行 98.3->98.8，close 98.5(下) -> 99.0(上) fresh 上穿
    tail = [(3, 97.0, 98.0, -0.01), (2, 98.4, 98.3, -0.01),
            (1, 98.5, 98.6, 0.0), (0, 99.0, 98.8, 0.0)]
    for days_ago, close, ma50, dd20 in tail:
        rows.append(_snap("GSPC", dd20, days_ago, close=close, rs_ratio=None,
                          rs_momentum=None, above_ma200=1, ma20=96.5, ma50=ma50,
                          cmf=0.05, vol_zscore=0.5, ret_1d=0.005))
    _seed_series(db, rows)
    store.store_index_history_events([
        {"event_date": "2020-01-01", "symbol": "GSPC", "trigger_type": "sustained_uptrend",
         "rs_momentum": None, "ret_20d": 0.05, "duration_days": 8, "spy_above_ma200": 1},
        {"event_date": "2020-05-01", "symbol": "GSPC", "trigger_type": "sustained_uptrend",
         "rs_momentum": None, "ret_20d": 0.01, "duration_days": 4, "spy_above_ma200": 1},
    ], db)
    cur = [_snap("GSPC", 0.0, 0, close=99.0, rs_ratio=None, rs_momentum=None,
                 above_ma200=1, ma20=96.5, ma50=98.8, cmf=0.05, vol_zscore=0.5,
                 ret_1d=0.005, name="标普500")]
    alerts = index_detector.detect_index_signals(cur, db, TH, PERIODS)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["symbol"] == "GSPC"
    assert a["rule"] == "index_sustained_uptrend"
    assert a["value"] == pytest.approx(0.5, rel=1e-4)   # NULL 容差路径: 2 事件 1 命中
    assert "样本2" in a["message"]


# ---------- 反幻觉标注 ----------

def test_detect_message_disclaimer(tmp_path):
    """message 含"历史统计参考非预测"诚实性标注（lead 契约显式要求，通知/页面同源可见）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, _pullback_rows("IXIC", [-0.04, -0.06, -0.065]))
    store.store_index_history_events(_pullback_events("IXIC", 1), db)
    cur = [_snap("IXIC", -0.065, 0, close=95.5, ret_1d=-0.005)]
    alerts = index_detector.detect_index_signals(cur, db, TH, PERIODS)
    assert len(alerts) == 1
    assert "非预测" in alerts[0]["message"]
