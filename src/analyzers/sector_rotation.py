"""版块相对强度指标：RS-Ratio / RS-Momentum（RRG 标准）。

参考: AdroitAnandAI/RRG-Sector-Rotation-India 的 JdK 标准公式（调研中算法文档最详尽项目）。

RS-Ratio = 100 * (sector_close / spy_close) / rolling_mean(sector_close / spy_close, period)
  >100 表示版块跑赢大盘
RS-Momentum = 100 * (RS-Ratio / RS-Ratio.shift(period) - 1)
  正值表示强度上升（资金偏好转入该版块）

RRG 四象限:
  领先  (RS-Ratio>100, RS-Momentum>0)   改善  (RS-Ratio<100, RS-Momentum>0)
  恶化  (RS-Ratio>100, RS-Momentum<0)   落后  (RS-Ratio<100, RS-Momentum<0)

资金流辅助指标（MFI/CMF/vol_zscore）复用 flow_indicators，基于版块 ETF 自身量价。
"""
import pandas as pd
from typing import List, Dict, Any, Optional

from src.analyzers import flow_indicators


def calc_rs_ratio(sector_close: pd.Series, spy_close: pd.Series, period: int = 10) -> pd.Series:
    """计算 RS-Ratio（版块相对 SPY 强度）。

    Args:
        sector_close: 版块 ETF 收盘价序列
        spy_close: SPY 收盘价序列（需与 sector_close 等长对齐）
        period: 滚动均值周期

    Returns:
        RS-Ratio 序列（前 period-1 个为 NaN）；>100 跑赢大盘
    """
    if len(sector_close) != len(spy_close):
        raise ValueError(f"长度不一致: sector={len(sector_close)} spy={len(spy_close)}")
    ratio = sector_close / spy_close
    return 100 * ratio / ratio.rolling(window=period, min_periods=period).mean()


def calc_rs_momentum(rs_ratio: pd.Series, period: int = 10) -> pd.Series:
    """计算 RS-Momentum（RS-Ratio 的变化率）。

    Args:
        rs_ratio: RS-Ratio 序列
        period: ROC 周期

    Returns:
        RS-Momentum 序列（前 period 个为 NaN）；正值=强度上升
    """
    return 100 * (rs_ratio / rs_ratio.shift(period) - 1)


def calc_sector(sector_history: List[Dict[str, Any]],
                spy_history: List[Dict[str, Any]],
                periods: Optional[Dict[str, int]] = None) -> Dict[str, Optional[float]]:
    """计算版块全部指标（RS + 资金流），返回最新日值。

    Args:
        sector_history: 版块 ETF 历史快照 [{date, close, high, low, volume, vwap}]，升序
        spy_history: SPY 历史快照，升序
        periods: {rs_ratio_period, rs_momentum_period, mfi_period, cmf_period, vol_avg_days}

    Returns:
        {rs_ratio, rs_momentum, mfi, cmf, vol_zscore, vol_mean}；数据不足对应字段为 None
    """
    if periods is None:
        periods = {"rs_ratio_period": 10, "rs_momentum_period": 10,
                   "mfi_period": 14, "cmf_period": 20, "vol_avg_days": 20}

    if not sector_history or not spy_history:
        return _empty_sector()

    sec_df = pd.DataFrame(sector_history).sort_values("date").reset_index(drop=True)
    spy_df = pd.DataFrame(spy_history).sort_values("date").reset_index(drop=True)

    # 缺 high/low 时用 close 近似（与 pipeline.py _hist_to_df 一致）
    for df in (sec_df, spy_df):
        if "high" not in df.columns:
            df["high"] = df["close"] * 1.01
        if "low" not in df.columns:
            df["low"] = df["close"] * 0.99

    # 按日期对齐（inner join，避免长度不一致或停牌错位）
    merged = pd.merge(
        sec_df[["date", "close", "high", "low", "volume"]].rename(
            columns={"close": "sec_close", "high": "sec_high",
                     "low": "sec_low", "volume": "sec_volume"}),
        spy_df[["date", "close"]].rename(columns={"close": "spy_close"}),
        on="date", how="inner",
    )
    if len(merged) < periods.get("rs_ratio_period", 10):
        return _empty_sector()

    # RS 指标
    rs_ratio = calc_rs_ratio(merged["sec_close"], merged["spy_close"],
                             period=periods.get("rs_ratio_period", 10))
    rs_momentum = calc_rs_momentum(rs_ratio, period=periods.get("rs_momentum_period", 10))

    # 资金流指标（基于版块自身量价，复用 flow_indicators）
    sec_ohlc = merged[["date", "sec_close", "sec_high", "sec_low", "sec_volume"]].rename(
        columns={"sec_close": "close", "sec_high": "high",
                 "sec_low": "low", "sec_volume": "volume"})
    fi = flow_indicators.calc(sec_ohlc, {
        "cmf_period": periods.get("cmf_period", 20),
        "mfi_period": periods.get("mfi_period", 14),
        "vol_avg_days": periods.get("vol_avg_days", 20),
    })

    def _last(s: pd.Series) -> Optional[float]:
        v = s.iloc[-1] if len(s) > 0 else None
        return float(v) if pd.notna(v) else None

    return {
        "rs_ratio": _last(rs_ratio),
        "rs_momentum": _last(rs_momentum),
        "mfi": fi.get("mfi"),
        "cmf": fi.get("cmf"),
        "vol_zscore": fi.get("vol_zscore"),
        "vol_mean": fi.get("vol_mean"),
    }


def _empty_sector() -> Dict[str, None]:
    return {"rs_ratio": None, "rs_momentum": None, "mfi": None,
            "cmf": None, "vol_zscore": None, "vol_mean": None}
