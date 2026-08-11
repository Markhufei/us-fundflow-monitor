"""版块历史事件库 + 概率模型测试（Phase 3）。

用合成数据验证:
  - identify_events 正确识别两类触发
  - compute_outcomes 超额收益 + 持续天数手算验证
  - query_probability 概率计算 + 置信度分级
"""
import pandas as pd
import pytest

from src.analyzers import sector_stats
from src.storage import store


# ===================== identify_events =====================

def _make_rs_df(rs_ratio, rs_momentum, vol_zscore, dates=None):
    """构造 RS 时间序列 DataFrame。"""
    n = len(rs_ratio)
    if dates is None:
        dates = [f"2026-08-{i+1:02d}" for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "rs_ratio": rs_ratio,
        "rs_momentum": rs_momentum,
        "vol_zscore": vol_zscore,
    })


def test_identify_events_momentum_cross():
    """RS-Momentum 上穿 0 + 量能确认 -> 识别 momentum_cross 事件。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101, 101, 101],
        rs_momentum=[-1.0, -0.5, 0.8, 1.0, 1.2],   # idx2 从负转正
        vol_zscore=[0.5, 0.5, 1.8, 0.5, 0.5],       # idx2 量能确认 > 1.5
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "momentum_cross"
    assert events[0]["event_date"] == "2026-08-03"
    assert events[0]["rs_momentum"] == pytest.approx(0.8)


def test_identify_events_no_volume_confirm_no_event():
    """RS-Momentum 上穿 0 但量能不足 -> 不识别。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101],
        rs_momentum=[-1.0, -0.5, 0.8],
        vol_zscore=[0.5, 0.5, 1.0],  # < 1.5，量能不足
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5)
    # idx2: mom 上穿0 但 vz=1.0 < 1.5 -> momentum_cross 不触发
    #       rs 上穿100 且 mom>0 -> rs_cross 触发
    assert len(events) == 1
    assert events[0]["trigger_type"] == "rs_cross"


def test_identify_events_rs_cross():
    """RS-Ratio 上穿 100 + RS-Momentum > 0 -> 识别 rs_cross。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101, 101],
        rs_momentum=[0.5, 0.5, 0.5, 0.5],  # 始终正，不会触发 momentum_cross
        vol_zscore=[0.5, 0.5, 0.5, 0.5],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "rs_cross"
    assert events[0]["event_date"] == "2026-08-03"


def test_identify_events_same_day_no_duplicate():
    """同日同时满足两类条件只记一条（优先 momentum_cross）。"""
    df = _make_rs_df(
        rs_ratio=[99, 101],          # idx1 上穿 100
        rs_momentum=[-1.0, 0.8],     # idx1 上穿 0
        vol_zscore=[0.5, 1.8],       # idx1 量能确认
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "momentum_cross"


def test_identify_events_no_signal():
    """无明显信号时无事件。"""
    df = _make_rs_df(
        rs_ratio=[100, 100, 100],
        rs_momentum=[1.0, 1.0, 1.0],  # 始终正，无上穿
        vol_zscore=[0.5, 0.5, 0.5],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5)
    assert len(events) == 0


def test_identify_events_handles_nan():
    """前若干行 RS 为 NaN（数据不足 period）时跳过不报错。"""
    df = _make_rs_df(
        rs_ratio=[None, None, 100, 101],
        rs_momentum=[None, None, -0.5, 0.8],
        vol_zscore=[None, None, 0.5, 1.8],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5)
    # idx3: mom 上穿0 + vz=1.8 -> momentum_cross
    assert len(events) == 1
    assert events[0]["trigger_type"] == "momentum_cross"


# ===================== compute_outcomes =====================

def test_compute_outcomes_basic():
    """手算超额收益：sector 涨 10%，spy 涨 2% -> 超额 8%。"""
    sector = [100, 101, 102, 105, 110]   # 事件日 idx0，idx4 涨到 110 (+10%)
    spy = [400, 401, 402, 405, 408]       # idx4 涨到 408 (+2%)
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[4])
    assert out["ret_4d"] == pytest.approx(0.10 - 0.02, rel=1e-4)  # 0.08


def test_compute_outcomes_partial_horizon():
    """事件后数据不足 60 天时 ret_60d=None，其他可得。"""
    sector = [100, 101, 102, 103, 104, 105]
    spy = [400, 400, 400, 400, 400, 400]
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[5, 60])
    assert out["ret_5d"] is not None
    assert out["ret_60d"] is None  # 数据不足


def test_compute_outcomes_duration_continuous():
    """持续天数 = 连续超额收益>0 的天数。"""
    # 事件日 idx0=100/400。idx1-3 sector 跑赢，idx4 跑输
    sector = [100, 102, 104, 106, 105]   # idx1-3 超 spy，idx4 回落
    spy = [400, 400, 400, 400, 400]
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[5])
    # idx1: sec+2% spy+0% -> 超；idx2: +4%；idx3: +6%；idx4: +5% 但 spy+0% 仍超？
    # 持续天数定义为"累计超额>0"连续，idx4 sec_ret=5% > spy_ret=0% 仍 >0，所以持续 4
    assert out["duration_days"] == 4


def test_compute_outcomes_duration_breaks_on_underperform():
    """超额收益转负后中断持续计数。"""
    sector = [100, 102, 99, 101, 102]    # idx2 sector 跌破基准
    spy = [400, 400, 400, 400, 400]
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[5])
    # idx1: sec+2% > spy+0% 持续；idx2: sec-1% < spy+0% 中断
    assert out["duration_days"] == 1


# ===================== query_probability =====================

def _seed_history(db, events):
    """建库 + 插入合成历史事件。"""
    store.init_db(db)
    store.store_history_events(events, db)


def test_query_probability_basic(tmp_path):
    """概率 = 相似事件中"持续"的比例。"""
    db = str(tmp_path / "t.db")
    # 4 个相似事件（rs_momentum≈1.5），其中 3 个"持续"（duration>=10 且 ret_10d>=0.03）
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5,
         "duration_days": 15, "ret_10d": 0.05},
        {"event_date": "2020-03-01", "symbol": "XLE", "rs_momentum": 1.6,
         "duration_days": 20, "ret_10d": 0.08},
        {"event_date": "2020-06-01", "symbol": "XLE", "rs_momentum": 1.4,
         "duration_days": 12, "ret_10d": 0.04},
        {"event_date": "2020-09-01", "symbol": "XLE", "rs_momentum": 1.5,
         "duration_days": 3, "ret_10d": 0.01},  # 未持续
    ])
    r = sector_stats.query_probability("XLE", 1.5, db_path=db, thresholds={
        "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert r["sample_size"] == 4
    assert r["probability"] == pytest.approx(0.75, rel=1e-4)  # 3/4
    assert r["confidence"] == "低"  # 4 < 10


def test_query_probability_confidence_levels(tmp_path):
    """置信度按样本量分级：>=30 高，10-29 中，<10 低。"""
    db = str(tmp_path / "t.db")
    # 造 30 个相似事件
    events = [
        {"event_date": f"2020-01-{i+1:02d}", "symbol": "XLE", "rs_momentum": 1.5,
         "duration_days": 12, "ret_10d": 0.04}
        for i in range(30)
    ]
    _seed_history(db, events)
    r = sector_stats.query_probability("XLE", 1.5, db_path=db, thresholds={
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert r["sample_size"] == 30
    assert r["confidence"] == "高"


def test_query_probability_no_sample(tmp_path):
    """无相似事件时 probability=None，置信度低。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    r = sector_stats.query_probability("XLE", 1.5, db_path=db)
    assert r["probability"] is None
    assert r["sample_size"] == 0
    assert r["confidence"] == "低"


def test_query_probability_avg_amplitude_in_percent(tmp_path):
    """avg_amplitude 返回百分号形式（0.05 -> 5.0）。"""
    db = str(tmp_path / "t.db")
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5,
         "duration_days": 15, "ret_10d": 0.05},
        {"event_date": "2020-02-01", "symbol": "XLE", "rs_momentum": 1.5,
         "duration_days": 18, "ret_10d": 0.07},
    ])
    r = sector_stats.query_probability("XLE", 1.5, db_path=db)
    # (0.05 + 0.07) / 2 * 100 = 6.0
    assert r["avg_amplitude"] == pytest.approx(6.0, rel=1e-4)


def test_query_probability_none_momentum_uses_all(tmp_path):
    """rs_momentum=None 时查该 symbol 全部事件做概率。"""
    db = str(tmp_path / "t.db")
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5,
         "duration_days": 15, "ret_10d": 0.05},
        {"event_date": "2020-02-01", "symbol": "XLE", "rs_momentum": 5.0,  # 不在 1.5±20%
         "duration_days": 20, "ret_10d": 0.08},
    ])
    # rs_momentum=1.5 ±20% 只命中 1 条
    r1 = sector_stats.query_probability("XLE", 1.5, db_path=db)
    assert r1["sample_size"] == 1
    # None 查全部
    r2 = sector_stats.query_probability("XLE", None, db_path=db)
    assert r2["sample_size"] == 2
