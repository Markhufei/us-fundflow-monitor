"""技术指标计算：OBV, CMF, MFI, VWAP, 成交量 Z-Score。"""

import pandas as pd
import numpy as np


def calc(df: pd.DataFrame, periods: dict | None = None) -> dict:
    """
    计算资金流向指标。

    Args:
        df: 包含列 close, high, low, volume, date 的 DataFrame
        periods: 周期配置，默认使用 {cmf_period: 20, mfi_period: 14, vol_avg_days: 20}

    Returns:
        {obv, cmf, mfi, vwap, vol_zscore}，均为最新值
    """
    if periods is None:
        periods = {"cmf_period": 20, "mfi_period": 14, "vol_avg_days": 20}

    if df.empty or len(df) == 0:
        return {"obv": None, "cmf": None, "mfi": None, "vwap": None, "vol_zscore": None, "vol_mean": None}

    # 确保需要的列存在
    required_cols = ["close", "volume"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"DataFrame 缺少必需列: {col}")

    # 复制避免修改原数据
    df = df.copy().sort_values("date").reset_index(drop=True)

    # 计算 OBV (On-Balance Volume)
    df["price_change"] = df["close"].diff()
    df["obv_dir"] = np.where(df["price_change"] > 0, 1,
                            np.where(df["price_change"] < 0, -1, 0))
    df["obv_volume"] = df["obv_dir"] * df["volume"]
    df["obv"] = df["obv_volume"].cumsum()
    obv = df["obv"].iloc[-1] if pd.notna(df["obv"].iloc[-1]) else None

    # 计算 CMF (Chaikin Money Flow)
    cmf = None
    cmf_period = min(periods.get("cmf_period", 20), len(df))
    if cmf_period >= 1 and all(col in df.columns for col in ["high", "low"]):
        # 计算典型价格与货币流量乘数
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        # Money Flow Multiplier: [(Close - Low) - (High - Close)] / (High - Low)
        df["mf_multiplier"] = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"])
        # 处理分母为0的情况
        df.loc[df["high"] == df["low"], "mf_multiplier"] = 0
        df["money_flow"] = df["mf_multiplier"] * df["volume"]
        # 20日求和
        df["cmf"] = df["money_flow"].rolling(window=cmf_period, min_periods=1).sum() / \
                    df["volume"].rolling(window=cmf_period, min_periods=1).sum()
        cmf = df["cmf"].iloc[-1] if pd.notna(df["cmf"].iloc[-1]) else None

    # 计算 MFI (Money Flow Index)
    mfi = None
    mfi_period = min(periods.get("mfi_period", 14), len(df))
    if mfi_period >= 1 and all(col in df.columns for col in ["high", "low"]):
        if "typical_price" not in df.columns:
            df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["raw_money_flow"] = df["typical_price"] * df["volume"]
        # 区分正负货币流
        df["tp_change"] = df["typical_price"].diff()
        df["positive_mf"] = np.where(df["tp_change"] > 0, df["raw_money_flow"], 0)
        df["negative_mf"] = np.where(df["tp_change"] < 0, df["raw_money_flow"], 0)
        # 滚动求和
        pos_sum = df["positive_mf"].rolling(window=mfi_period, min_periods=1).sum()
        neg_sum = df["negative_mf"].rolling(window=mfi_period, min_periods=1).sum()
        # 避免除以0
        neg_sum = neg_sum.replace(0, np.nan)
        df["money_flow_ratio"] = pos_sum / neg_sum
        df["mfi"] = 100 - (100 / (1 + df["money_flow_ratio"]))
        # 当 neg_sum 为0时，MFI=100
        df.loc[neg_sum.isna() & (pos_sum > 0), "mfi"] = 100
        mfi = df["mfi"].iloc[-1] if pd.notna(df["mfi"].iloc[-1]) else None

    # VWAP (Volume Weighted Average Price) - 当日值或最新值
    vwap = None
    if "vwap" in df.columns and pd.notna(df["vwap"].iloc[-1]):
        vwap = df["vwap"].iloc[-1]
    elif all(col in df.columns for col in ["high", "low"]):
        # 用典型价格模拟 VWAP
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        vwap = df["typical_price"].iloc[-1] if pd.notna(df["typical_price"].iloc[-1]) else None
    else:
        vwap = df["close"].iloc[-1] if pd.notna(df["close"].iloc[-1]) else None

    # 成交量 Z-Score（基线窗口不含当日，与事件库建库共用 vol_zscore_series）
    vol_zscore = None
    vol_mean = None
    vol_avg_days = periods.get("vol_avg_days", 20)
    if len(df) > vol_avg_days:  # 需 20 天基线 + 当日
        zs = vol_zscore_series(df["volume"], vol_avg_days)
        v = zs.iloc[-1]
        if pd.notna(v):
            vol_zscore = float(v)
        m = df["volume"].rolling(vol_avg_days, min_periods=vol_avg_days).mean().shift(1).iloc[-1]
        if pd.notna(m):
            vol_mean = float(m)

    return {
        "obv": float(obv) if obv is not None and pd.notna(obv) else None,
        "cmf": float(cmf) if cmf is not None and pd.notna(cmf) else None,
        "mfi": float(mfi) if mfi is not None and pd.notna(mfi) else None,
        "vwap": float(vwap) if vwap is not None and pd.notna(vwap) else None,
        "vol_zscore": float(vol_zscore) if vol_zscore is not None and pd.notna(vol_zscore) else None,
        "vol_mean": float(vol_mean) if vol_mean is not None and pd.notna(vol_mean) else None,
    }


def vol_zscore_series(volume: pd.Series, period: int = 20) -> pd.Series:
    """成交量 Z-Score 序列：当日量相对"前 period 日"基线的 Z 分数。

    与旧版差异（2026-09-01 方法学重做）:
      - 基线 shift(1) 不含当日：放量日不再抬高自身分母（旧版 Z 被钝化）
      - min_periods=period：基线不足 period 日时为 NaN，不做降级
      - 量级守卫：当日量偏离前 period 日中位数 10 倍以上视为坏数据 -> NaN
        （实证: 2026-07-28 部分数据日 MU 量仅为正常 1/11，高量损坏方向会伪确认量能）

    Returns:
        与 volume 等长的 Z-Score 序列（前 period 位及坏数据日为 NaN）
    """
    vol_mean = volume.rolling(period, min_periods=period).mean().shift(1)
    vol_std = volume.rolling(period, min_periods=period).std().shift(1)
    vol_med = volume.rolling(period, min_periods=period).median().shift(1)
    z = (volume - vol_mean) / vol_std
    bad = (vol_med > 0) & ((volume > 10 * vol_med) | (volume < vol_med / 10))
    return z.mask(bad)
