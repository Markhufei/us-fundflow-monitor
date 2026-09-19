"""版块历史事件库 + 概率模型测试（Phase 3，2026-09-01 方法学重做后）。

用合成数据验证:
  - identify_events 去抖确认语义（momentum_cross 需连续 N 天 + 新鲜交叉）
  - compute_outcomes T+1 入场基准的超额收益 + 持续天数手算验证
  - query_probability ret_20d 单阈值持续定义 + trigger_type 过滤 + 绝对容差
  - wilson_ci 置信区间
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


def test_identify_events_momentum_cross_needs_confirm_days():
    """去抖: RS-Momentum 上穿 0 后需连续 confirm_days 天为正才算确认，事件记在确认日。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101, 101, 101],
        rs_momentum=[-1.0, -0.5, 0.8, 1.0, 1.2],   # idx2 上穿，idx3 才满 2 天
        vol_zscore=[0.5, 0.5, 1.8, 1.8, 0.5],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=2)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "momentum_cross"
    assert events[0]["event_date"] == "2026-08-04"   # 确认日 = 第 2 个正日
    assert events[0]["rs_momentum"] == pytest.approx(1.0)


def test_identify_events_single_day_cross_with_confirm_days_1():
    """confirm_days=1 即旧语义：上穿当日即触发。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101, 101, 101],
        rs_momentum=[-1.0, -0.5, 0.8, 1.0, 1.2],
        vol_zscore=[0.5, 0.5, 1.8, 0.5, 0.5],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=1)
    assert len(events) == 1
    assert events[0]["event_date"] == "2026-08-03"
    assert events[0]["rs_momentum"] == pytest.approx(0.8)


def test_identify_events_long_streak_fires_once():
    """新鲜交叉约束: 长正段中只在确认日触发一次，不逐日重复。"""
    df = _make_rs_df(
        rs_ratio=[99, 100, 101, 102, 103],
        rs_momentum=[-1.0, 0.5, 0.6, 0.7, 0.8],
        vol_zscore=[2.0, 2.0, 2.0, 2.0, 2.0],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=2)
    assert len(events) == 1
    assert events[0]["event_date"] == "2026-08-03"


def test_identify_events_no_volume_confirm_no_momentum_event():
    """确认日量能不足 -> momentum_cross 不触发；rs 也未上穿 -> 无事件。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101, 101],
        rs_momentum=[-1.0, -0.5, 0.8, 1.0],
        vol_zscore=[0.5, 0.5, 0.5, 0.5],  # 全部 < 1.5
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=2)
    # rs_cross: RS-Ratio 99->101 发生在 idx2，但 idx2 动量未满 2 天；idx3 动量满但 rs 未上穿
    assert events == []


def test_identify_events_rs_cross():
    """RS-Ratio 上穿 100 + 动量连续 confirm_days 天为正 -> rs_cross（不要求新鲜）。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101, 101],
        rs_momentum=[0.5, 0.5, 0.5, 0.5],
        vol_zscore=[0.5, 0.5, 0.5, 0.5],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=2)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "rs_cross"
    assert events[0]["event_date"] == "2026-08-03"


def test_identify_events_same_day_priority_momentum_cross():
    """同日两类条件都满足只记一条（优先 momentum_cross）。"""
    df = _make_rs_df(
        rs_ratio=[99, 99, 101],
        rs_momentum=[-0.5, 0.8, 0.9],   # idx2 满去抖确认（idx1,2 正，idx0 负）
        vol_zscore=[0.5, 1.8, 1.8],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=2)
    assert len(events) == 1
    assert events[0]["trigger_type"] == "momentum_cross"


def test_identify_events_no_signal():
    """动量恒正且 RS 无上穿 -> 无事件。"""
    df = _make_rs_df(
        rs_ratio=[100, 100, 100],
        rs_momentum=[1.0, 1.0, 1.0],
        vol_zscore=[0.5, 0.5, 0.5],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=2)
    assert events == []


def test_identify_events_handles_nan():
    """前若干行 RS 为 NaN（数据不足 period）时跳过不报错（confirm_days=1 旧语义验证）。"""
    df = _make_rs_df(
        rs_ratio=[None, None, 100, 101],
        rs_momentum=[None, None, -0.5, 0.8],
        vol_zscore=[None, None, 0.5, 1.8],
    )
    events = sector_stats.identify_events(df, vol_confirm=1.5, confirm_days=1)
    # idx3: mom 上穿0 + vz=1.8 -> momentum_cross
    assert len(events) == 1
    assert events[0]["trigger_type"] == "momentum_cross"


# ===================== compute_outcomes =====================

def test_compute_outcomes_t1_base():
    """T+1 入场基准：超额收益以事件次日收盘为基，手算验证。"""
    sector = [100, 100, 110, 120, 130, 140]   # 事件日 idx0，入场 idx1=100，idx5=140 (+40%)
    spy = [400, 400, 400, 400, 400, 400]      # spy 平 -> 超额 = 40%
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[4])
    assert out["ret_4d"] == pytest.approx(0.40, rel=1e-4)


def test_compute_outcomes_excess_vs_spy():
    """sector 涨 10% spy 涨 2%（相对各自入场日）-> 超额 8%。"""
    sector = [100, 101, 102, 105, 110, 111.1]  # 入场 idx1=101, idx5: +10%
    spy = [400, 401, 402, 405, 408, 409.024]   # 入场 idx1=401, idx5: +2%
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[4])
    assert out["ret_4d"] == pytest.approx(0.10 - 0.02, rel=1e-3)


def test_compute_outcomes_partial_horizon():
    """入场后数据不足 60 天时 ret_60d=None，其他可得。"""
    sector = [100, 101, 102, 103, 104, 105, 106, 107]
    spy = [400] * 8
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[5, 60])
    assert out["ret_5d"] is not None    # 入场 idx1, end=6 < 8
    assert out["ret_60d"] is None       # end=61 越界


def test_compute_outcomes_event_at_last_index():
    """事件在最后一天（无次日收盘）-> 全 None，持续 0。"""
    out = sector_stats.compute_outcomes(4, [100, 101, 102, 103, 104], [400, 400, 400, 400, 400])
    assert out["ret_5d"] is None
    assert out["duration_days"] == 0


def test_compute_outcomes_duration_continuous():
    """持续天数 = 自入场次日起连续累计超额>0 的天数。"""
    # 入场 idx1=102。idx2-4 均相对入场日跑赢 spy（spy 平）
    sector = [100, 102, 104, 106, 105]
    spy = [400, 400, 400, 400, 400]
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[5])
    # idx2: +1.96% 超；idx3: +3.92% 超；idx4: +2.94% 仍超 -> 持续 3
    assert out["duration_days"] == 3


def test_compute_outcomes_duration_breaks_on_underperform():
    """超额收益转负后中断持续计数（入场次日即跑输 -> 0）。"""
    sector = [100, 102, 99, 101, 102]    # 入场 idx1=102, idx2 跌破
    spy = [400, 400, 400, 400, 400]
    out = sector_stats.compute_outcomes(0, sector, spy, horizons=[5])
    assert out["duration_days"] == 0


# ===================== query_probability =====================

def _seed_history(db, events):
    """建库 + 插入合成历史事件。"""
    store.init_db(db)
    store.store_history_events(events, db)


def test_query_probability_basic(tmp_path):
    """概率 = 相似事件中 ret_20d >= 阈值 的比例。"""
    db = str(tmp_path / "t.db")
    # 4 个相似事件（rs_momentum ∈ 1.5±0.5），其中 3 个"持续"（ret_20d >= 0.02）
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5, "ret_20d": 0.05},
        {"event_date": "2020-03-01", "symbol": "XLE", "rs_momentum": 1.6, "ret_20d": 0.08},
        {"event_date": "2020-06-01", "symbol": "XLE", "rs_momentum": 1.4, "ret_20d": 0.04},
        {"event_date": "2020-09-01", "symbol": "XLE", "rs_momentum": 1.5, "ret_20d": 0.01},  # 未持续
    ])
    r = sector_stats.query_probability("XLE", 1.5, db_path=db, thresholds={
        "sustained_amplitude": 0.02, "momentum_tol_abs": 0.5,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert r["sample_size"] == 4
    assert r["probability"] == pytest.approx(0.75, rel=1e-4)  # 3/4
    assert r["confidence"] == "低"  # 4 < 10


def test_query_probability_confidence_levels(tmp_path):
    """置信度按样本量分级：>=30 高，10-29 中，<10 低。"""
    db = str(tmp_path / "t.db")
    events = [
        {"event_date": f"2020-01-{i+1:02d}", "symbol": "XLE", "rs_momentum": 1.5,
         "ret_20d": 0.04}
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


def test_query_probability_avg_amplitude_uses_ret20(tmp_path):
    """avg_amplitude 返回 20 日超额均值百分号形式（0.05, 0.07 -> 6.0）。"""
    db = str(tmp_path / "t.db")
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5, "ret_20d": 0.05},
        {"event_date": "2020-02-01", "symbol": "XLE", "rs_momentum": 1.5, "ret_20d": 0.07},
    ])
    r = sector_stats.query_probability("XLE", 1.5, db_path=db)
    assert r["avg_amplitude"] == pytest.approx(6.0, rel=1e-4)


def test_query_probability_trigger_type_filter(tmp_path):
    """trigger_type 过滤：momentum_cross 查询不混入 rs_cross 事件（防跨类型污染）。"""
    db = str(tmp_path / "t.db")
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5,
         "trigger_type": "momentum_cross", "ret_20d": 0.05},
        {"event_date": "2020-02-01", "symbol": "XLE", "rs_momentum": 1.5,
         "trigger_type": "momentum_cross", "ret_20d": 0.06},
        {"event_date": "2020-03-01", "symbol": "XLE", "rs_momentum": 1.5,
         "trigger_type": "rs_cross", "ret_20d": -0.10},
        {"event_date": "2020-04-01", "symbol": "XLE", "rs_momentum": 1.5,
         "trigger_type": "rs_cross", "ret_20d": -0.20},
    ])
    r = sector_stats.query_probability("XLE", 1.5, db_path=db, trigger_type="momentum_cross")
    assert r["sample_size"] == 2
    assert r["probability"] == pytest.approx(1.0)
    # 不过滤则混入 rs_cross -> 概率被稀释（这正是审查发现的污染模式）
    r_all = sector_stats.query_probability("XLE", 1.5, db_path=db)
    assert r_all["sample_size"] == 4
    assert r_all["probability"] == pytest.approx(0.5)


def test_query_probability_absolute_tolerance(tmp_path):
    """绝对容差 ±0.5：1.5 命中 [1.0, 2.0]，不命中 0.9 / 5.0。"""
    db = str(tmp_path / "t.db")
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5, "ret_20d": 0.05},
        {"event_date": "2020-02-01", "symbol": "XLE", "rs_momentum": 1.0, "ret_20d": 0.05},
        {"event_date": "2020-03-01", "symbol": "XLE", "rs_momentum": 2.0, "ret_20d": 0.05},
        {"event_date": "2020-04-01", "symbol": "XLE", "rs_momentum": 0.9, "ret_20d": 0.05},  # 边界外
        {"event_date": "2020-05-01", "symbol": "XLE", "rs_momentum": 5.0, "ret_20d": 0.05},  # 远离
    ])
    r = sector_stats.query_probability("XLE", 1.5, db_path=db)
    assert r["sample_size"] == 3


def test_query_probability_none_momentum_uses_all(tmp_path):
    """rs_momentum=None 时查该 symbol 全部事件做概率。"""
    db = str(tmp_path / "t.db")
    _seed_history(db, [
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.5, "ret_20d": 0.05},
        {"event_date": "2020-02-01", "symbol": "XLE", "rs_momentum": 5.0, "ret_20d": 0.08},
    ])
    r1 = sector_stats.query_probability("XLE", 1.5, db_path=db)
    assert r1["sample_size"] == 1   # 1.5±0.5 只命中 1.5
    r2 = sector_stats.query_probability("XLE", None, db_path=db)
    assert r2["sample_size"] == 2


# ===================== wilson_ci =====================

def test_wilson_ci_known_values():
    """Wilson 区间手算验证: p=0.5, n=10 -> 约 (0.237, 0.763)。"""
    lo, hi = sector_stats.wilson_ci(0.5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3)
    assert hi == pytest.approx(0.7634, abs=1e-3)


def test_wilson_ci_bounds():
    """p=0 或 p=1、n=0 的边界不越界。"""
    lo, hi = sector_stats.wilson_ci(0.0, 5)
    assert lo == 0.0 and hi < 0.6
    lo, hi = sector_stats.wilson_ci(1.0, 5)
    assert hi == 1.0 and lo > 0.4
    lo, hi = sector_stats.wilson_ci(0.5, 0)
    assert (lo, hi) == (0.0, 1.0)
