"""vol_zscore_series 测试（2026-09-01 方法学重做：基线不含当日 + 量级守卫）。"""
import pandas as pd
import pytest

from src.analyzers.flow_indicators import vol_zscore_series


def test_zscore_baseline_excludes_today():
    """基线窗口不含当日：当日量不进入自己的均值/标准差分母。"""
    prior = [100.0 + i for i in range(20)]        # 前 20 日
    vol = pd.Series(prior + [500.0])              # 当日放量到 500
    z = vol_zscore_series(vol, period=20)
    prior_s = pd.Series(prior)
    expected = (500.0 - prior_s.mean()) / prior_s.std()
    assert z.iloc[-1] == pytest.approx(expected, rel=1e-6)
    # 旧实现（含当日）的均值会被 500 抬高，此处显式不同
    naive = (500.0 - vol.tail(20).mean()) / vol.tail(20).std()
    assert z.iloc[-1] != pytest.approx(naive, rel=1e-3)


def test_zscore_volume_spike_guard_high():
    """当日量为前 20 日中位数 10 倍以上 -> 坏数据守卫置 NaN。"""
    vol = pd.Series([1_000_000.0] * 20 + [20_000_000.0])
    z = vol_zscore_series(vol, period=20)
    assert pd.isna(z.iloc[-1])


def test_zscore_volume_guard_low():
    """当日量不足前 20 日中位数 1/10 -> 坏数据守卫置 NaN（07-28 型部分数据日）。"""
    vol = pd.Series([60_000_000.0] * 20 + [5_000_000.0])  # ~1/12
    z = vol_zscore_series(vol, period=20)
    assert pd.isna(z.iloc[-1])


def test_zscore_insufficient_history_nan():
    """历史不足 period 日 -> NaN（min_periods=period，不降级）。"""
    vol = pd.Series([100.0] * 15)
    z = vol_zscore_series(vol, period=20)
    assert z.isna().all()


def test_zscore_normal_day_finite():
    """量能正常波动日 -> 有限 Z 值。"""
    vol = pd.Series([1_000_000 + (i % 5) * 50_000 for i in range(20)] + [1_100_000.0])
    z = vol_zscore_series(vol, period=20)
    assert pd.notna(z.iloc[-1])
    assert abs(z.iloc[-1]) < 10
