"""指数信号分析测试（红态验收；impl 已落地部分按 2026-10-18 实现语义对齐）。

覆盖（镜像 test_sector_stats.py 的 mock/合成数据策略）:
  - calc_drawdown 手算值 + NaN 前导行（window-1 行热身，与 calc_rs_ratio 同语义）
  - identify_index_events 5 类触发: 各正例/负例/去抖/新鲜交叉/同日优先级
    （语义对齐实现: pullback_start 为预警型与其他类型正交可同日并存；
      其余 4 型互斥只记一条，优先级 rebound > fishing > uptrend > withdrawal；
      序列起点视为"窗口前一日未达"即新鲜——边界语义与 sector identify 一致）
  - compute_index_outcomes: T+1 入场绝对收益 + max_dd_20d（窗口内运行峰回撤）
    + duration 方向（看多型连涨/看空型连跌）；关键字调用兼容参数顺序
  - query_index_probability: per-type 命中口径（pullback_start=max_dd_20d<=阈值 /
    withdrawal=ret_20d<=-阈值 / 其余=ret_20d>=阈值）+ per-trigger 过滤
    + regime 分桶回退 + 容差仅 sustained_uptrend 生效（GSPC rs=NULL 无容差路径）

特征帧契约（identify_index_events 消费列，由 build_features 产出）:
  date, close, ma20, ma50, dd20, dd50, cmf, vol_zscore, ret_1d, above_ma200,
  rs_ratio, rs_momentum
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from src.storage import store

index_signals = pytest.importorskip(
    "src.analyzers.index_signals",
    reason="src/analyzers/index_signals.py 未落地（红态验收，impl-index 完成后自动启用）",
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


def _frame(dates, close, dd20, cmf, vol_zscore, ma20, ret_1d,
           ma50=None, above_ma200=0, ma_fall=False, rs_ratio=101.0, rs_momentum=0.5):
    """构造特征帧（identify_index_events 消费的全列），中性值默认不触发任何规则。

    ma50 默认中性上行序列（可显式覆盖）；above_ma200 默认 0（阻断 sustained）；
    ma_fall=True 时 ma50 下行（阻断 sustained）。
    """
    n = len(dates)
    if ma50 is None:
        ma50 = [100.0 - 0.2 * i for i in range(n)] if ma_fall else [99.0 + 0.1 * i for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "close": close,
        "ma20": ma20,
        "ma50": ma50,
        "dd20": dd20,
        "dd50": [-0.01] * n,
        "cmf": cmf,
        "vol_zscore": vol_zscore,
        "ret_1d": ret_1d,
        "above_ma200": [above_ma200] * n,
        "rs_ratio": [rs_ratio] * n,
        "rs_momentum": [rs_momentum] * n,
    })


def _dates(n):
    """合成帧用固定序列日期（identify 直接消费帧，无 days 窗口查询，无时间炸弹风险）。"""
    return [f"2026-08-{i + 1:02d}" for i in range(n)]


# ===================== calc_drawdown =====================

def test_calc_drawdown_hand_values():
    """手算: 前 4 行 NaN（window-1 热身），破位日 dd20 = 9/10-1 = -0.10。"""
    close = [10.0] * 5 + [9.0]
    dd = index_signals.calc_drawdown(pd.Series(close), window=5)
    assert len(dd) == len(close)
    for i in range(4):
        assert pd.isna(dd.iloc[i])
    assert dd.iloc[4] == pytest.approx(0.0)
    assert dd.iloc[5] == pytest.approx(-0.10, rel=1e-6)


def test_calc_drawdown_never_positive():
    """滚动最大值含当日 -> dd 恒 <= 0（上涨序列 dd=0）。"""
    close = [100.0 + i for i in range(25)]
    dd = index_signals.calc_drawdown(pd.Series(close), window=20)
    tail = dd.iloc[19:]
    assert (tail.dropna() <= 0).all()
    assert tail.iloc[-1] == pytest.approx(0.0)


def test_calc_drawdown_default_window_is_20():
    """默认 window=20：25 点序列首 19 行 NaN。"""
    close = [100.0] * 24 + [90.0]
    dd = index_signals.calc_drawdown(pd.Series(close))
    for i in range(19):
        assert pd.isna(dd.iloc[i])
    assert dd.iloc[24] == pytest.approx(-0.10, rel=1e-6)


# ===================== identify_index_events: pullback_start =====================

def test_identify_pullback_start_fresh_confirm():
    """正例: dd20 连续 2 天 <= -0.05 且窗口前一日 > -0.05 -> 确认日触发。"""
    df = _frame(
        dates=_dates(4),
        close=[100.0, 99.0, 96.0, 95.0],
        dd20=[-0.01, -0.01, -0.06, -0.065],
        cmf=[0.0] * 4, vol_zscore=[0.5] * 4,
        ma20=[101.0, 100.5, 100.0, 99.5],
        ret_1d=[None, -0.01, -0.03, -0.01],
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "pullback_start"
    assert events[0]["event_date"] == "2026-08-04"


def test_identify_pullback_start_long_streak_fires_once():
    """长回撤段只在起始确认日触发一次，延续日（非新鲜）不重复。"""
    df = _frame(
        dates=_dates(5),
        close=[100.0, 95.0, 94.5, 94.0, 93.5],
        dd20=[-0.01, -0.06, -0.065, -0.07, -0.075],
        cmf=[0.0] * 5, vol_zscore=[0.5] * 5,
        ma20=[101.0, 100.5, 100.0, 99.5, 99.0],
        ret_1d=[None, -0.05, -0.005, -0.005, -0.005],
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "pullback_start"
    assert events[0]["event_date"] == "2026-08-03"   # 第 2 个连续破位日（确认日）


def test_identify_pullback_start_needs_two_days():
    """负例: 单日破位不足 confirm_days 不触发（次日即收回）。"""
    df = _frame(
        dates=_dates(3),
        close=[100.0, 96.0, 100.0],
        dd20=[-0.01, -0.06, -0.01],
        cmf=[0.0] * 3, vol_zscore=[0.5] * 3,
        ma20=[101.0, 100.5, 100.0],
        ret_1d=[None, -0.04, 0.04],
    )
    events = index_signals.identify_index_events(df, TH)
    assert all(e["trigger_type"] != "pullback_start" for e in events)


# ===================== identify_index_events: pullback_end_rebound =====================

def test_identify_pullback_end_rebound_positive():
    """正例: 活跃回调段（15 日内 dd20 触及 -0.06）+ fresh 上穿 MA20 + cmf>0。

    dd20 只单日破位（无连续 2 天）-> 不产生 pullback_start（预警型正交性下
    精确断言 1 条事件）。
    """
    df = _frame(
        dates=_dates(5),
        close=[100.0, 99.0, 98.0, 99.0, 100.0],
        dd20=[-0.01, -0.06, -0.02, -0.02, -0.01],
        cmf=[0.0, -0.02, 0.0, 0.02, 0.05],            # 末日 cmf>0
        vol_zscore=[0.5] * 5,
        ma20=[101.0, 100.5, 100.0, 99.6, 99.3],       # close 99<99.6 -> 100>99.3 fresh 上穿
        ret_1d=[None, -0.01, -0.01, 0.0102, 0.0101],  # < rebound_min_ret -> 无 fishing
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "pullback_end_rebound"
    assert events[0]["event_date"] == "2026-08-05"


def test_identify_pullback_end_rebound_needs_pullback_context():
    """负例: 无活跃回调段（dd20 从未 <= -0.05）时上穿 MA20 不算 rebound。"""
    df = _frame(
        dates=_dates(5),
        close=[100.0, 99.0, 98.0, 99.0, 100.0],
        dd20=[-0.01, -0.02, -0.01, -0.02, -0.01],
        cmf=[0.0, 0.0, 0.0, 0.02, 0.05],
        vol_zscore=[0.5] * 5,
        ma20=[101.0, 100.5, 100.0, 99.6, 99.3],
        ret_1d=[None, -0.01, -0.01, 0.0102, 0.0101],
    )
    assert index_signals.identify_index_events(df, TH) == []


# ===================== identify_index_events: capital_withdrawal =====================

def test_identify_capital_withdrawal_fresh_confirm():
    """正例: cmf 连续 2 天 < -0.10 且前一日 >= -0.10 -> 确认日触发。"""
    df = _frame(
        dates=_dates(4),
        close=[100.0, 99.9, 99.5, 99.2],
        dd20=[-0.01] * 4,
        cmf=[0.0, 0.0, -0.12, -0.13],
        vol_zscore=[0.5] * 4,
        ma20=[101.0, 100.9, 100.8, 100.7],
        ret_1d=[None, -0.001, -0.004, -0.003],
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "capital_withdrawal"
    assert events[0]["event_date"] == "2026-08-04"


def test_identify_capital_withdrawal_long_below_fires_once():
    """长低于段只在起始确认日触发一次（-0.05 未达阈值 -> 起点新鲜）。"""
    df = _frame(
        dates=_dates(4),
        close=[100.0] * 4,
        dd20=[-0.01] * 4,
        cmf=[-0.05, -0.12, -0.13, -0.14],
        vol_zscore=[0.5] * 4,
        ma20=[101.0] * 4,
        ret_1d=[None, 0.0, 0.0, 0.0],
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "capital_withdrawal"
    assert events[0]["event_date"] == "2026-08-03"


def test_identify_capital_withdrawal_needs_two_days():
    """负例: 单日跌破（< confirm_days）不触发。"""
    df = _frame(
        dates=_dates(3),
        close=[100.0] * 3,
        dd20=[-0.01] * 3,
        cmf=[0.0, -0.05, -0.12],
        vol_zscore=[0.5] * 3,
        ma20=[101.0] * 3,
        ret_1d=[None, -0.005, -0.005],
    )
    assert index_signals.identify_index_events(df, TH) == []


# ===================== identify_index_events: bottom_fishing =====================

def test_identify_bottom_fishing_positive():
    """正例: 活跃回调段 + ret_1d >= 0.015 + vol_zscore >= 1.5 且前日未达 -> 触发。"""
    df = _frame(
        dates=_dates(5),
        close=[100.0, 99.0, 98.0, 98.0, 99.96],
        dd20=[-0.06, -0.02, -0.02, -0.02, -0.02],     # 单日破位制造活跃回调段
        cmf=[0.0, -0.02, -0.03, -0.01, 0.0],
        vol_zscore=[0.5, 0.5, 0.5, 0.5, 1.8],          # 末日 >= 1.5，前日 0.5
        ma20=[100.0, 99.8, 99.6, 99.5, 99.98],         # close 全程 < ma20 -> 无 rebound
        ret_1d=[None, -0.01, -0.01, 0.0, 0.02],        # 末日 >= 0.015，前日未达
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "bottom_fishing"
    assert events[0]["event_date"] == "2026-08-05"


def test_identify_bottom_fishing_no_refire():
    """去抖: 连续多日满足 fishing 条件只在首日触发一次（防大阳线连日重复）。"""
    df = _frame(
        dates=_dates(4),
        close=[102.0, 104.04, 106.12, 108.24],
        dd20=[-0.06, -0.02, -0.06, -0.02],   # 单日破位 -> 活跃回调段，无 pullback_start
        cmf=[0.0] * 4,
        vol_zscore=[1.8] * 4,
        ma20=[110.0] * 4,                    # close 全程 < ma20 -> 无 rebound
        ret_1d=[0.02, 0.02, 0.02, 0.02],     # 首日视为前日未达，后续被去抖阻断
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "bottom_fishing"
    assert events[0]["event_date"] == "2026-08-01"


# ===================== identify_index_events: sustained_uptrend =====================

def test_identify_sustained_uptrend_positive():
    """正例: fresh 上穿 MA50 + MA50 上行（> shift(2)）+ above_ma200 + rs > 0 -> 触发。"""
    df = _frame(
        dates=_dates(5),
        close=[97.0, 97.5, 98.0, 98.5, 99.2],
        dd20=[-0.03, -0.03, -0.02, -0.01, 0.0],   # 无活跃回调段
        cmf=[0.05] * 5,
        vol_zscore=[0.5] * 5,
        ma20=[96.0, 96.5, 97.0, 98.4, 98.9],      # close 全程 > ma20 -> 无 fresh 上穿
        ret_1d=[None, 0.005, 0.005, 0.005, 0.007],
        above_ma200=1,
    )
    df["ma50"] = [98.0, 98.1, 98.2, 98.6, 98.8]   # close 98.5<98.6 -> 99.2>98.8 fresh 上穿
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "sustained_uptrend"
    assert events[0]["event_date"] == "2026-08-05"


def test_identify_sustained_uptrend_negatives():
    """负例: rs_momentum < 0 阻断；MA50 下行（<= shift(2)）时上穿不算 sustained。"""
    base = dict(
        dates=_dates(5),
        close=[97.0, 97.5, 98.0, 98.5, 99.2],
        dd20=[-0.03, -0.03, -0.02, -0.01, 0.0],
        cmf=[0.05] * 5,
        vol_zscore=[0.5] * 5,
        ma20=[96.0, 96.5, 97.0, 98.4, 98.9],
        ret_1d=[None, 0.005, 0.005, 0.005, 0.007],
        above_ma200=1,
    )
    neg_rs = _frame(**base, rs_momentum=-0.5)
    neg_rs["ma50"] = [98.0, 98.1, 98.2, 98.6, 98.8]
    assert index_signals.identify_index_events(neg_rs, TH) == []

    falling_ma50 = _frame(**base, rs_momentum=0.5)
    falling_ma50["ma50"] = [99.2, 99.1, 99.0, 98.95, 98.9]   # 上穿但 MA50 下行
    assert index_signals.identify_index_events(falling_ma50, TH) == []


# ===================== 同日优先级 + NaN =====================

def test_identify_same_day_priority_rebound_top():
    """同日 rebound/fishing/withdrawal 条件同时成立只记一条，优先 pullback_end_rebound。

    构造: 末日 fresh 上穿 MA20（rebound）+ ret_1d/vol 达标（fishing）+
    cmf 连续 2 天 < -0.10 且前日未达（withdrawal 原始条件成立，被 elif 优先级吞掉）。
    """
    df = _frame(
        dates=_dates(6),
        close=[99.0, 98.5, 98.0, 98.5, 99.0, 101.0],
        dd20=[-0.06, -0.02, -0.03, -0.02, -0.06, -0.02],   # 活跃回调段，无连续 2 日破位
        cmf=[0.0, 0.0, 0.0, 0.0, -0.12, -0.13],            # 末日 fresh withdrawal 条件成立
        vol_zscore=[0.5, 0.5, 0.5, 0.5, 0.5, 1.8],          # 末日 >= 1.5
        ma20=[100.5, 100.2, 100.0, 99.8, 99.9, 100.3],      # 99<99.9 -> 101>100.3 fresh 上穿
        ret_1d=[None, -0.005, -0.005, 0.005, 0.005, 0.0202],  # 末日 >= 0.015，前日未达
        above_ma200=0,
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "pullback_end_rebound"
    assert events[0]["event_date"] == "2026-08-06"


def test_identify_pullback_start_orthogonal_to_rebound():
    """pullback_start 为预警型与其他类型正交: 同日确认可与 rebound 各记一条。"""
    df = _frame(
        dates=_dates(6),
        close=[99.0, 98.5, 98.0, 98.5, 99.0, 101.0],
        dd20=[-0.01, -0.06, -0.065, -0.03, -0.02, -0.01],  # idx2 处 fresh 确认 pullback_start
        cmf=[0.0] * 6,
        vol_zscore=[0.5, 0.5, 0.5, 0.5, 0.5, 1.8],          # 末日 vol 确认 rebound
        ma20=[100.5, 100.2, 100.0, 99.8, 99.9, 100.3],     # 末日 fresh 上穿
        ret_1d=[None, -0.005, -0.005, 0.005, 0.005, 0.0202],
        above_ma200=0,
    )
    events = index_signals.identify_index_events(df, TH)
    types = sorted(e["trigger_type"] for e in events)
    assert types == ["pullback_end_rebound", "pullback_start"]
    assert events[0]["event_date"] == "2026-08-03"   # pullback_start 确认日
    assert events[1]["event_date"] == "2026-08-06"   # rebound 日


def test_identify_handles_nan_leading_rows():
    """前导 NaN（指标热身期）跳过不报错、不产生幻影事件；后续正常触发。"""
    df = _frame(
        dates=_dates(5),
        close=[100.0, 100.0, 99.9, 96.0, 95.0],
        dd20=[None, None, -0.01, -0.06, -0.065],
        cmf=[None, None, 0.0, 0.0, 0.0],
        vol_zscore=[None, None, 0.5, 0.5, 0.5],
        ma20=[None, None, 101.0, 100.5, 100.0],
        ret_1d=[None, None, -0.001, -0.039, -0.01],
    )
    events = index_signals.identify_index_events(df, TH)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "pullback_start"
    assert events[0]["event_date"] == "2026-08-05"


def test_identify_empty_frame():
    """空帧返回空列表（边界）。"""
    assert index_signals.identify_index_events(pd.DataFrame(columns=[
        "date", "close", "ma20", "ma50", "dd20", "dd50", "cmf",
        "vol_zscore", "ret_1d", "above_ma200", "rs_ratio", "rs_momentum"]), TH) == []


# ===================== compute_index_outcomes =====================

def test_compute_index_outcomes_t1_base_and_max_dd_bearish():
    """T+1 入场绝对收益 + max_dd_20d（窗口内运行峰回撤）+ 空头方向 duration。

    close=[100,100,90,95,100,105,110], event_idx=0:
      入场 idx1=100 -> ret_5d = 110/100-1 = +0.10
      max_dd_20d = 窗口内运行峰 100 回撤至 90 -> -0.10
      duration（pullback_start 看空方向）= 入场次日起累计收益 < 0 连续天数 = 2
    """
    close = [100.0, 100.0, 90.0, 95.0, 100.0, 105.0, 110.0]
    out = index_signals.compute_index_outcomes(0, close, trigger_type="pullback_start",
                                               horizons=[5])
    assert out["ret_5d"] == pytest.approx(0.10, rel=1e-4)
    assert out["max_dd_20d"] == pytest.approx(-0.10, rel=1e-4)
    assert out["duration_days"] == 2


def test_compute_index_outcomes_duration_bullish_direction():
    """多头触发（bottom_fishing）duration = 入场次日起累计收益 > 0 连续天数。"""
    close = [100.0, 100.0, 105.0, 108.0, 107.0]
    out = index_signals.compute_index_outcomes(0, close, trigger_type="bottom_fishing",
                                               horizons=[5])
    # idx2 +5% / idx3 +8% / idx4 累计仍 +7% -> 持续 3
    assert out["duration_days"] == 3
    assert out["ret_5d"] is None   # 入场 idx1 + 5 = 6 越界


def test_compute_index_outcomes_partial_horizon():
    """数据不足的 horizon 为 None，可得 horizon 正常（镜像 compute_outcomes）。"""
    close = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]
    out = index_signals.compute_index_outcomes(0, close, trigger_type="sustained_uptrend",
                                               horizons=[5, 60])
    assert out["ret_5d"] == pytest.approx(106.0 / 101.0 - 1, rel=1e-4)   # close[1+5]=106
    assert out["ret_60d"] is None


def test_compute_index_outcomes_event_at_last_index():
    """事件在最后一天（无次日收盘）-> 全 None，持续 0。"""
    out = index_signals.compute_index_outcomes(4, [100.0, 101.0, 102.0, 103.0, 104.0],
                                               trigger_type="pullback_end_rebound")
    assert out["ret_5d"] is None
    assert out["ret_20d"] is None
    assert out["max_dd_20d"] is None
    assert out["duration_days"] == 0


# ===================== query_index_probability =====================

def _seed_index_events(db, events):
    store.init_db(db)
    store.store_index_history_events(events, db)


def test_query_index_probability_bullish_hit_and_type_isolation(tmp_path):
    """看多型命中口径 ret_20d >= sustained_amplitude；同 rs 的他类型事件不混入。"""
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": "2020-01-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.5, "ret_20d": 0.05, "duration_days": 8},
        {"event_date": "2020-03-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.6, "ret_20d": 0.08, "duration_days": 10},
        {"event_date": "2020-06-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.4, "ret_20d": 0.04, "duration_days": 6},
        {"event_date": "2020-09-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.5, "ret_20d": 0.01, "duration_days": 2},
        # 同 rs 的 capital_withdrawal 事件不应混入 bottom_fishing 查询
        {"event_date": "2021-01-01", "symbol": "IXIC", "trigger_type": "capital_withdrawal",
         "rs_momentum": 1.5, "ret_20d": -0.10, "duration_days": 1},
    ])
    r = index_signals.query_index_probability("IXIC", "bottom_fishing", db_path=db,
                                              thresholds=TH, rs_momentum=1.5)
    assert r["sample_size"] == 4
    assert r["probability"] == pytest.approx(0.75, rel=1e-4)
    assert r["confidence"] == "低"
    assert r["avg_amplitude"] == pytest.approx((0.05 + 0.08 + 0.04 + 0.01) / 4 * 100, rel=1e-4)
    assert r["avg_duration"] == pytest.approx(6.5, rel=1e-4)
    assert r["regime_used"] is False


def test_query_index_probability_pullback_start_hit_rate(tmp_path):
    """pullback_start 命中率口径: max_dd_20d <= pullback_dd 的比例（预警命中率）。"""
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": "2020-01-01", "symbol": "IXIC", "trigger_type": "pullback_start",
         "rs_momentum": None, "ret_20d": 0.01, "max_dd_20d": -0.08, "duration_days": 2},
        {"event_date": "2020-03-01", "symbol": "IXIC", "trigger_type": "pullback_start",
         "rs_momentum": None, "ret_20d": -0.03, "max_dd_20d": -0.10, "duration_days": 3},
        {"event_date": "2020-06-01", "symbol": "IXIC", "trigger_type": "pullback_start",
         "rs_momentum": None, "ret_20d": 0.00, "max_dd_20d": -0.06, "duration_days": 1},
        {"event_date": "2020-09-01", "symbol": "IXIC", "trigger_type": "pullback_start",
         "rs_momentum": None, "ret_20d": 0.05, "max_dd_20d": -0.01, "duration_days": 8},
    ])
    r = index_signals.query_index_probability("IXIC", "pullback_start", db_path=db,
                                              thresholds=TH)
    assert r["sample_size"] == 4
    assert r["probability"] == pytest.approx(0.75, rel=1e-4)   # 3/4 真回撤 >= 5%


def test_query_index_probability_withdrawal_downside_hit(tmp_path):
    """capital_withdrawal 下跌延续口径: ret_20d <= -sustained_amplitude 的比例。"""
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": "2020-01-01", "symbol": "IXIC", "trigger_type": "capital_withdrawal",
         "rs_momentum": None, "ret_20d": -0.05, "duration_days": 4},
        {"event_date": "2020-02-01", "symbol": "IXIC", "trigger_type": "capital_withdrawal",
         "rs_momentum": None, "ret_20d": -0.08, "duration_days": 6},
        {"event_date": "2020-03-01", "symbol": "IXIC", "trigger_type": "capital_withdrawal",
         "rs_momentum": None, "ret_20d": -0.03, "duration_days": 3},
        {"event_date": "2020-04-01", "symbol": "IXIC", "trigger_type": "capital_withdrawal",
         "rs_momentum": None, "ret_20d": 0.01, "duration_days": 2},
    ])
    r = index_signals.query_index_probability("IXIC", "capital_withdrawal", db_path=db,
                                              thresholds=TH)
    assert r["sample_size"] == 4
    assert r["probability"] == pytest.approx(0.75, rel=1e-4)   # 3/4 下跌 >= 2%


def test_query_index_probability_regime_bucket(tmp_path):
    """regime 匹配: spy_above_ma200 分桶样本足够时用桶内概率（regime_used=True）。"""
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": f"2020-{m:02d}-01", "symbol": "IXIC",
         "trigger_type": "bottom_fishing", "rs_momentum": 1.5,
         "ret_20d": ret, "spy_above_ma200": above, "duration_days": 5}
        for m, ret, above in
        [(1, 0.05, 1), (2, 0.08, 1), (3, 0.04, 1), (4, 0.01, 1),
         (5, 0.01, 0), (6, 0.00, 0), (7, -0.02, 0), (8, 0.01, 0)]
    ])
    th = dict(TH, prob_min_sample=3)
    above = index_signals.query_index_probability("IXIC", "bottom_fishing", db_path=db,
                                                  thresholds=th, spy_above_ma200=1)
    below = index_signals.query_index_probability("IXIC", "bottom_fishing", db_path=db,
                                                  thresholds=th, spy_above_ma200=0)
    assert above["regime_used"] is True and above["sample_size"] == 4
    assert above["probability"] == pytest.approx(0.75, rel=1e-4)
    assert below["regime_used"] is True and below["sample_size"] == 4
    assert below["probability"] == 0.0


def test_query_index_probability_regime_fallback_when_sparse(tmp_path):
    """regime 桶内样本 < prob_min_sample 时回退全样本，regime_used=False。"""
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": "2020-01-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.5, "ret_20d": 0.05, "spy_above_ma200": 1, "duration_days": 5},
        {"event_date": "2020-02-01", "symbol": "IXIC", "trigger_type": "bottom_fishing",
         "rs_momentum": 1.5, "ret_20d": 0.01, "spy_above_ma200": 0, "duration_days": 3},
    ])
    th = dict(TH, prob_min_sample=3)
    r = index_signals.query_index_probability("IXIC", "bottom_fishing", db_path=db,
                                              thresholds=th, spy_above_ma200=1)
    assert r["regime_used"] is False
    assert r["sample_size"] == 2


def test_query_index_probability_gspc_no_tolerance_path(tmp_path):
    """GSPC 无容差路径: rs_momentum=NULL 时查该 symbol+trigger_type 全部事件。

    GSPC 纯回撤框架 rs 全 NULL，sustained_uptrend 仍可触发（rs NULL 或 >0 分支），
    概率走同 symbol+同类型基准率，不按动量窗口过滤。
    """
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": "2020-01-01", "symbol": "GSPC", "trigger_type": "sustained_uptrend",
         "rs_momentum": None, "ret_20d": 0.05, "duration_days": 6},
        {"event_date": "2020-02-01", "symbol": "GSPC", "trigger_type": "sustained_uptrend",
         "rs_momentum": None, "ret_20d": -0.01, "duration_days": 3},
    ])
    r = index_signals.query_index_probability("GSPC", "sustained_uptrend", db_path=db,
                                              thresholds=TH, rs_momentum=None)
    assert r["sample_size"] == 2
    assert r["probability"] == pytest.approx(0.5, rel=1e-4)
    assert r["confidence"] == "低"


def test_query_index_probability_tolerance_only_for_sustained(tmp_path):
    """rs_momentum ±momentum_tol_abs 绝对容差仅 sustained_uptrend 生效。"""
    db = str(tmp_path / "t.db")
    _seed_index_events(db, [
        {"event_date": "2020-01-01", "symbol": "IXIC", "trigger_type": "sustained_uptrend",
         "rs_momentum": 1.5, "ret_20d": 0.05, "duration_days": 5},
        {"event_date": "2020-02-01", "symbol": "IXIC", "trigger_type": "sustained_uptrend",
         "rs_momentum": 1.0, "ret_20d": 0.05, "duration_days": 5},
        {"event_date": "2020-03-01", "symbol": "IXIC", "trigger_type": "sustained_uptrend",
         "rs_momentum": 2.0, "ret_20d": 0.05, "duration_days": 5},
        {"event_date": "2020-04-01", "symbol": "IXIC", "trigger_type": "sustained_uptrend",
         "rs_momentum": 0.9, "ret_20d": 0.05, "duration_days": 5},   # 窗口外
        {"event_date": "2020-05-01", "symbol": "IXIC", "trigger_type": "sustained_uptrend",
         "rs_momentum": 5.0, "ret_20d": 0.05, "duration_days": 5},   # 远离
    ])
    r = index_signals.query_index_probability("IXIC", "sustained_uptrend", db_path=db,
                                              thresholds=TH, rs_momentum=1.5)
    assert r["sample_size"] == 3


def test_query_index_probability_no_sample(tmp_path):
    """无相似事件 -> probability=None、sample 0、置信低。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    r = index_signals.query_index_probability("IXIC", "pullback_start", db_path=db,
                                              thresholds=TH)
    assert r["probability"] is None
    assert r["sample_size"] == 0
    assert r["confidence"] == "低"
    assert r["avg_duration"] is None and r["avg_amplitude"] is None
