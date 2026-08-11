"""版块相对强度指标测试（Phase 2）。

用合成数据验证 RS-Ratio / RS-Momentum 公式 + calc_sector 整合。
所有断言基于手算的已知输入->已知输出，非主观判断。
"""
import pandas as pd
import pytest

from src.analyzers import sector_rotation


def test_calc_rs_ratio_basic():
    """RS-Ratio 公式: 100 * ratio / rolling_mean(ratio, period)。"""
    # sector 上涨 spy 平 -> ratio 上升
    sector = pd.Series([100, 110, 120, 130, 140], dtype=float)
    spy = pd.Series([100, 100, 100, 100, 100], dtype=float)
    rs = sector_rotation.calc_rs_ratio(sector, spy, period=3)
    # ratio = [1.0, 1.1, 1.2, 1.3, 1.4]
    # period=3 rolling mean at idx4 = (1.2+1.3+1.4)/3 = 1.3
    # rs[4] = 100 * 1.4 / 1.3 ≈ 107.69
    assert rs.iloc[4] == pytest.approx(100 * 1.4 / 1.3, rel=1e-4)
    # 前 period-1 个为 NaN（min_periods=period）
    assert pd.isna(rs.iloc[0])
    assert pd.isna(rs.iloc[1])


def test_calc_rs_ratio_outperform_gt_100():
    """sector 持续跑赢 spy -> RS-Ratio > 100。"""
    sector = pd.Series([100, 102, 104, 106, 108], dtype=float)
    spy = pd.Series([100, 100, 100, 100, 100], dtype=float)
    rs = sector_rotation.calc_rs_ratio(sector, spy, period=3)
    assert rs.iloc[-1] > 100


def test_calc_rs_ratio_underperform_lt_100():
    """sector 持续跑输 spy -> RS-Ratio < 100。"""
    sector = pd.Series([100, 98, 96, 94, 92], dtype=float)
    spy = pd.Series([100, 100, 100, 100, 100], dtype=float)
    rs = sector_rotation.calc_rs_ratio(sector, spy, period=3)
    assert rs.iloc[-1] < 100


def test_calc_rs_ratio_length_mismatch():
    """长度不一致应报错（防静默错位）。"""
    with pytest.raises(ValueError):
        sector_rotation.calc_rs_ratio(pd.Series([1, 2, 3]), pd.Series([1, 2]), period=2)


def test_calc_rs_momentum_basic():
    """RS-Momentum = 100 * (RS/RS.shift(period) - 1)。"""
    rs = pd.Series([100, 105, 110, 115, 120], dtype=float)
    mom = sector_rotation.calc_rs_momentum(rs, period=2)
    # mom[2] = 100*(110/100 - 1) = 10
    # mom[4] = 100*(120/110 - 1) ≈ 9.0909
    assert mom.iloc[2] == pytest.approx(10.0, rel=1e-4)
    assert mom.iloc[4] == pytest.approx(100 * (120 / 110 - 1), rel=1e-4)
    assert pd.isna(mom.iloc[0])
    assert pd.isna(mom.iloc[1])


def test_calc_rs_momentum_negative_when_declining():
    """RS-Ratio 下降 -> RS-Momentum 为负。"""
    rs = pd.Series([120, 115, 110, 105, 100], dtype=float)
    mom = sector_rotation.calc_rs_momentum(rs, period=2)
    assert mom.iloc[-1] < 0


def test_calc_sector_returns_all_fields():
    """calc_sector 返回 6 个字段，sector 跑赢时 rs_ratio > 100。"""
    dates = pd.date_range("2026-07-01", periods=30).strftime("%Y-%m-%d").tolist()
    sec = [{"date": d, "close": 100 + i * 0.5, "high": 101 + i * 0.5,
            "low": 99 + i * 0.5, "volume": 1_000_000 + i * 10_000}
           for i, d in enumerate(dates)]
    spy = [{"date": d, "close": 400 + i * 0.1, "high": 401, "low": 399,
            "volume": 50_000_000} for i, d in enumerate(dates)]
    result = sector_rotation.calc_sector(sec, spy, {
        "rs_ratio_period": 10, "rs_momentum_period": 10,
        "mfi_period": 14, "cmf_period": 20, "vol_avg_days": 20,
    })
    assert set(result.keys()) == {"rs_ratio", "rs_momentum", "mfi", "cmf", "vol_zscore", "vol_mean"}
    # sector 涨得比 spy 快 -> rs_ratio > 100
    assert result["rs_ratio"] is not None
    assert result["rs_ratio"] > 100


def test_calc_sector_empty_history():
    """空历史返回全 None。"""
    result = sector_rotation.calc_sector([], [])
    assert result == {"rs_ratio": None, "rs_momentum": None, "mfi": None,
                      "cmf": None, "vol_zscore": None, "vol_mean": None}


def test_calc_sector_insufficient_data():
    """数据不足 rs_ratio_period 时 RS 为 None（不报错）。"""
    dates = ["2026-08-01", "2026-08-02"]
    sec = [{"date": d, "close": 100, "volume": 1000} for d in dates]
    spy = [{"date": d, "close": 400, "volume": 1000} for d in dates]
    result = sector_rotation.calc_sector(sec, spy, {"rs_ratio_period": 10})
    assert result["rs_ratio"] is None


def test_calc_sector_aligns_mismatched_dates():
    """sector 与 spy 日期不完全一致时按 inner join 对齐。"""
    sec = [{"date": "2026-08-01", "close": 100, "volume": 1000},
           {"date": "2026-08-02", "close": 101, "volume": 1000},
           {"date": "2026-08-03", "close": 102, "volume": 1000}]
    # spy 缺 08-02
    spy = [{"date": "2026-08-01", "close": 400, "volume": 1000},
           {"date": "2026-08-03", "close": 400, "volume": 1000}]
    # 数据不足 period=10 -> None，但不应崩溃
    result = sector_rotation.calc_sector(sec, spy, {"rs_ratio_period": 10})
    assert result["rs_ratio"] is None
