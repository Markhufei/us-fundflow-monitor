"""指数观察信号：特征构建 + 事件识别 + 结果回测 + 条件概率（镜像 sector_stats 模式）。

双轨框架:
  - GSPC（标普500）: 纯回撤框架——指数对 SPY 的 RS 无意义（SPY 即其代理），
    rs_ratio/rs_momentum 恒为 NULL，触发条件不含 RS 项
  - IXIC/DJI: RS 框架，复用 sector_rotation.calc_rs_ratio/calc_rs_momentum（vs SPY）

资金流指标（cmf/mfi/vol_zscore）走代理 ETF（指数本体育量为空/失真）:
  GSPC->SPY、IXIC->QQQ、DJI->DIA。CMF/MFI 序列公式与 flow_indicators.calc 逐行
  同源（flow_indicators 仅提供最新值接口，序列版在本地实现，勿单边改动公式）。

事件结果口径为**绝对收益**（T+1 收盘入场，非 sector 的超额收益）+ max_dd_20d
（pullback_start 预警命中率用）。建库与实时检测共用 identify_index_events，
杜绝双实现漂移。
"""
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from src.storage import store
from src.analyzers import flow_indicators, sector_rotation

# duration 方向按类型: 看多型算入场后连涨天数，其余（回调/撤出型）算连跌天数
BULLISH_TRIGGER_TYPES = {"pullback_end_rebound", "bottom_fishing", "sustained_uptrend"}


def calc_drawdown(close: pd.Series, window: int = 20) -> pd.Series:
    """滚动回撤: close / rolling(window).max(close) - 1（前 window-1 位为 NaN）。"""
    return close / close.rolling(window, min_periods=window).max() - 1


def _cmf_series(close: pd.Series, high: pd.Series, low: pd.Series,
                volume: pd.Series, period: int = 20) -> pd.Series:
    """CMF 序列（公式与 flow_indicators.calc 同源，含 high==low 守卫与 min_periods=1 降级）。"""
    tp = (high + low + close) / 3
    mult = ((close - low) - (high - close)) / (high - low)
    mult = mult.where(high != low, 0)
    mf = mult * volume
    return (mf.rolling(period, min_periods=1).sum()
            / volume.rolling(period, min_periods=1).sum())


def _mfi_series(close: pd.Series, high: pd.Series, low: pd.Series,
                volume: pd.Series, period: int = 14) -> pd.Series:
    """MFI 序列（公式与 flow_indicators.calc 同源，neg_sum=0 时 MFI=100 守卫一致）。"""
    tp = (high + low + close) / 3
    raw_mf = tp * volume
    tp_chg = tp.diff()
    pos = np.where(tp_chg > 0, raw_mf, 0)
    neg = np.where(tp_chg < 0, raw_mf, 0)
    pos_sum = pd.Series(pos, index=close.index).rolling(period, min_periods=1).sum()
    neg_sum = pd.Series(neg, index=close.index).rolling(period, min_periods=1).sum()
    neg_sum = neg_sum.replace(0, np.nan)
    mfr = pos_sum / neg_sum
    mfi = 100 - (100 / (1 + mfr))
    mfi = mfi.where(~(neg_sum.isna() & (pos_sum > 0)), 100)
    return mfi


def build_features(index_df: List[Dict[str, Any]],
                   proxy_df: List[Dict[str, Any]],
                   spy_df: Optional[List[Dict[str, Any]]],
                   benchmark: Optional[str],
                   periods: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """构建指数特征序列（升序 DataFrame，建库与日常采集共用）。

    Args:
        index_df: 指数本体 OHLCV [{date, close, high, low, volume}]，升序
        proxy_df: 代理 ETF OHLCV（cmf/mfi/vol_zscore 来源），升序
        spy_df: SPY OHLCV（RS 基准），升序；benchmark 为 None 时可不传
        benchmark: RS 基准符号（"SPY"）；None = 纯回撤框架（GSPC），rs 字段全 NULL
        periods: {rs_ratio_period, rs_momentum_period, cmf_period, mfi_period, vol_avg_days}

    Returns:
        DataFrame（date/close/.../ret_1d/dd20/dd50/ma20/ma50/above_ma200/
        rs_ratio/rs_momentum/cmf/mfi/vol_zscore），指数与代理无共同日期时返回空表
    """
    if periods is None:
        periods = {"rs_ratio_period": 30, "rs_momentum_period": 30,
                   "cmf_period": 20, "mfi_period": 14, "vol_avg_days": 20}
    if index_df is None or len(index_df) == 0:
        return pd.DataFrame()

    idx = pd.DataFrame(index_df).sort_values("date").reset_index(drop=True)
    cols = ["date", "close", "high", "low", "volume"]
    m = idx[cols].copy()
    if proxy_df is not None and len(proxy_df) > 0:
        proxy = pd.DataFrame(proxy_df).sort_values("date").reset_index(drop=True)
        m = pd.merge(m, proxy[cols].rename(columns={
            "close": "proxy_close", "high": "proxy_high",
            "low": "proxy_low", "volume": "proxy_volume"}),
            on="date", how="inner")
    if benchmark and spy_df is not None and len(spy_df) > 0:
        spy = pd.DataFrame(spy_df).sort_values("date").reset_index(drop=True)
        m = pd.merge(m, spy[["date", "close"]].rename(columns={"close": "spy_close"}),
                     on="date", how="inner")
    if m.empty:
        return m

    # 价量结构特征（基于指数本体）
    m["ret_1d"] = m["close"].pct_change()
    m["ma20"] = m["close"].rolling(20).mean()
    m["ma50"] = m["close"].rolling(50).mean()
    m["ma200"] = m["close"].rolling(200).mean()
    m["above_ma200"] = (m["close"] > m["ma200"]).astype(float).where(m["ma200"].notna())
    m["dd20"] = calc_drawdown(m["close"], 20)
    m["dd50"] = calc_drawdown(m["close"], 50)

    # RS 框架（IXIC/DJI；GSPC 纯回撤框架 rs 字段 NULL）
    if benchmark and "spy_close" in m.columns:
        m["rs_ratio"] = sector_rotation.calc_rs_ratio(
            m["close"], m["spy_close"], period=periods.get("rs_ratio_period", 30))
        m["rs_momentum"] = sector_rotation.calc_rs_momentum(
            m["rs_ratio"], period=periods.get("rs_momentum_period", 30))
    else:
        m["rs_ratio"] = np.nan
        m["rs_momentum"] = np.nan

    # 资金流特征（基于代理 ETF 量价；指数本体育量失真）
    if "proxy_close" in m.columns:
        m["cmf"] = _cmf_series(m["proxy_close"], m["proxy_high"], m["proxy_low"],
                               m["proxy_volume"], periods.get("cmf_period", 20))
        m["mfi"] = _mfi_series(m["proxy_close"], m["proxy_high"], m["proxy_low"],
                               m["proxy_volume"], periods.get("mfi_period", 14))
        m["vol_zscore"] = flow_indicators.vol_zscore_series(
            m["proxy_volume"], periods.get("vol_avg_days", 20))
    else:
        m["cmf"] = np.nan
        m["mfi"] = np.nan
        m["vol_zscore"] = np.nan

    return m


def identify_index_events(df: pd.DataFrame,
                          thresholds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """在指数特征序列中识别 5 类事件（建库回测与 detect 同一函数）。

    触发规则（阈值来自 config index_thresholds）:
      - pullback_start: dd20 连续 confirm_days 天 <= pullback_dd 且窗口前一日未达
        （新鲜交叉）；预警型，与其他类型语义正交，同日可独立记录
      - pullback_end_rebound: 活跃回调段（近 pullback_lookback 天 min(dd20) <= pullback_dd）
        + fresh 上穿 MA20 + (cmf > 0 或 vol_zscore > vol_confirm)
      - capital_withdrawal: cmf 连续 confirm_days 天 < cmf_withdrawal 且前一日未达（新鲜）
      - bottom_fishing: 活跃回调段 + ret_1d >= rebound_min_ret + vol_zscore >= vol_confirm
        + 前一日 ret_1d 未达阈值（防大阳线连日重复触发）
      - sustained_uptrend: fresh 上穿 MA50 + MA50 上行(ma50 > ma50.shift(2))
        + above_ma200==1 + (rs_momentum NULL 或 > 0)

    同日多型命中只记一条，优先级 pullback_end_rebound > bottom_fishing >
    sustained_uptrend > capital_withdrawal（pullback_start 除外，见上）。

    Args:
        df: 特征序列（build_features 输出或 index_snapshot 历史行），
            需含列 date/close/ma20/ma50/dd20/ret_1d，按 date 升序
        thresholds: index_thresholds 配置

    Returns:
        事件列表 [{event_date, trigger_type, rs_momentum, vol_zscore, cmf, dd20}]
    """
    events: List[Dict[str, Any]] = []
    if df.empty:
        return events
    if thresholds is None:
        thresholds = {}
    pullback_dd = thresholds.get("pullback_dd", -0.05)
    confirm_days = max(int(thresholds.get("confirm_days", 2)), 1)
    lookback = int(thresholds.get("pullback_lookback", 15))
    vol_confirm = thresholds.get("vol_confirm", 1.5)
    cmf_withdrawal = thresholds.get("cmf_withdrawal", -0.10)
    rebound_min_ret = thresholds.get("rebound_min_ret", 0.015)

    def _col(name: str) -> pd.Series:
        return df[name].astype(float) if name in df.columns \
            else pd.Series(np.nan, index=df.index, dtype=float)

    close = _col("close")
    ma20 = _col("ma20")
    ma50 = _col("ma50")
    dd20 = _col("dd20")
    ret_1d = _col("ret_1d")
    cmf = _col("cmf")
    vz = _col("vol_zscore")
    rs_mom = _col("rs_momentum")
    above200 = _col("above_ma200")

    # NaN 参与比较为 False，天然门控未热期，无需额外过滤
    below = dd20 <= pullback_dd
    steady_below = below & (below.rolling(confirm_days, min_periods=confirm_days).sum()
                            == confirm_days)
    fresh_below = ~below.shift(confirm_days, fill_value=False)
    pullback_start = (steady_below & fresh_below).fillna(False)

    active_pullback = dd20.rolling(lookback, min_periods=1).min() <= pullback_dd

    cross_ma20 = (close > ma20) & (close.shift(1) <= ma20.shift(1))
    rebound = (active_pullback & cross_ma20
               & ((cmf > 0) | (vz > vol_confirm))).fillna(False)

    big_up = ret_1d >= rebound_min_ret
    fishing = (active_pullback & big_up & (vz >= vol_confirm)
               & ~big_up.shift(1, fill_value=False)).fillna(False)

    cross_ma50 = (close > ma50) & (close.shift(1) <= ma50.shift(1))
    ma50_rising = ma50 > ma50.shift(2)
    uptrend = (cross_ma50 & ma50_rising & (above200 == 1)
               & (rs_mom.isna() | (rs_mom > 0))).fillna(False)

    cw_out = cmf < cmf_withdrawal
    steady_cw = cw_out & (cw_out.rolling(confirm_days, min_periods=confirm_days).sum()
                          == confirm_days)
    fresh_cw = ~cw_out.shift(confirm_days, fill_value=False)
    withdrawal = (steady_cw & fresh_cw).fillna(False)

    def _f(v) -> Optional[float]:
        return None if pd.isna(v) else float(v)

    for i in range(len(df)):
        fired = []
        if pullback_start.iloc[i]:
            fired.append("pullback_start")
        # 4 型互斥只记一条（优先级即 elif 顺序）
        if rebound.iloc[i]:
            fired.append("pullback_end_rebound")
        elif fishing.iloc[i]:
            fired.append("bottom_fishing")
        elif uptrend.iloc[i]:
            fired.append("sustained_uptrend")
        elif withdrawal.iloc[i]:
            fired.append("capital_withdrawal")
        for tt in fired:
            events.append({
                "event_date": df["date"].iloc[i],
                "trigger_type": tt,
                "rs_momentum": _f(rs_mom.iloc[i]),
                "vol_zscore": _f(vz.iloc[i]),
                "cmf": _f(cmf.iloc[i]),
                "dd20": _f(dd20.iloc[i]),
            })
    return events


def compute_index_outcomes(event_idx: int,
                           close: List[float],
                           horizons: Optional[List[int]] = None,
                           trigger_type: Optional[str] = None) -> Dict[str, Any]:
    """算事件后各 horizon 的绝对收益 + 20 日内最大回撤 + 持续天数（T+1 收盘入场）。

    与 sector_stats.compute_outcomes 的差异: 绝对收益口径（非超额）+ 新增
    max_dd_20d（pullback_start 预警命中率: 事件后 20 日内是否真的发生 >= 阈值回撤）
    + duration 方向按类型（看多型算连涨、回调/撤出型算连跌）。

    Args:
        event_idx: 事件日在序列中的索引
        close: 收盘价序列（升序）
        horizons: 收益计算窗口（自入场日起），默认 [5, 10, 20, 60]
        trigger_type: 触发类型（duration 方向用；None 按看多处理）

    Returns:
        {ret_5d, ret_10d, ret_20d, ret_60d, max_dd_20d, duration_days}
    """
    if horizons is None:
        horizons = [5, 10, 20, 60]

    result: Dict[str, Any] = {}
    n = len(close)
    entry = event_idx + 1
    if entry >= n:
        for h in horizons:
            result[f"ret_{h}d"] = None
        result["max_dd_20d"] = None
        result["duration_days"] = 0
        return result

    base = close[entry]
    if base is None or base <= 0:
        for h in horizons:
            result[f"ret_{h}d"] = None
        result["max_dd_20d"] = None
        result["duration_days"] = 0
        return result

    for h in horizons:
        end = entry + h
        result[f"ret_{h}d"] = (close[end] / base - 1) if end < n and close[end] is not None else None

    # max_dd_20d: 入场日起 20 个交易日内相对窗口内运行峰值的最大回撤（<=0）
    window = close[entry:entry + 20]
    peak = base
    max_dd = 0.0
    for c in window:
        if c is None:
            break
        peak = max(peak, c)
        max_dd = min(max_dd, c / peak - 1)
    result["max_dd_20d"] = max_dd

    # 持续天数: 入场次日起，方向一致（连涨/连跌）的累计天数（最多看 60 天）
    bullish = trigger_type in BULLISH_TRIGGER_TYPES if trigger_type else True
    duration = 0
    for i in range(entry + 1, min(entry + 61, n)):
        c = close[i]
        if c is None:
            break
        r = c / base - 1
        if (bullish and r > 0) or (not bullish and r < 0):
            duration += 1
        else:
            break
    result["duration_days"] = duration

    return result


def query_index_probability(symbol: str,
                            trigger_type: str,
                            db_path: str = "data/fundflow.db",
                            thresholds: Optional[Dict[str, Any]] = None,
                            rs_momentum: Optional[float] = None,
                            spy_above_ma200: Optional[int] = None) -> Dict[str, Any]:
    """查历史相似指数事件，算条件概率（镜像 sector_stats.query_probability）。

    结果口径按类型:
      pullback_start     -> 命中率 = max_dd_20d <= pullback_dd 的比例
                            （预警命中率，页面标注"非可交易优势"）
      capital_withdrawal -> 下跌延续 = ret_20d <= -sustained_amplitude 的比例
      其余（看多型）      -> 延续 = ret_20d >= sustained_amplitude 的比例

    相似度匹配: 同 symbol + 同 trigger_type 必须匹配；rs_momentum 容差仅
    sustained_uptrend 生效（其规则含 RS 特征，±momentum_tol_abs 绝对容差），
    其余类型退化为基准率。spy_above_ma200 regime 分桶，桶内样本 <
    prob_min_sample 时回退全样本。

    Returns:
        {probability, avg_duration, avg_amplitude, sample_size, confidence, regime_used}
        probability: 0-1；样本为 0 时 None。avg_amplitude 为平均 20 日绝对收益（%）
    """
    if thresholds is None:
        thresholds = {}
    sustained_amp = thresholds.get("sustained_amplitude", 0.02)
    pullback_dd = thresholds.get("pullback_dd", -0.05)
    tol_abs = thresholds.get("momentum_tol_abs", 0.5)
    min_sample = thresholds.get("prob_min_sample", 10)
    high_sample = thresholds.get("prob_high_sample", 30)

    match_momentum = rs_momentum if trigger_type == "sustained_uptrend" else None

    regime_used = False
    similar: list = []
    if spy_above_ma200 is not None:
        similar = store.query_index_events(symbol, trigger_type, rs_momentum=match_momentum,
                                           momentum_tol=tol_abs,
                                           spy_above_ma200=spy_above_ma200, db_path=db_path)
        regime_used = len(similar) >= min_sample
    if not regime_used:
        similar = store.query_index_events(symbol, trigger_type, rs_momentum=match_momentum,
                                           momentum_tol=tol_abs, db_path=db_path)
        regime_used = False
    sample = len(similar)

    if sample == 0:
        return {"probability": None, "avg_duration": None, "avg_amplitude": None,
                "sample_size": 0, "confidence": "低", "regime_used": False}

    def _hit(e: Dict[str, Any]) -> bool:
        if trigger_type == "pullback_start":
            return e.get("max_dd_20d") is not None and e["max_dd_20d"] <= pullback_dd
        if trigger_type == "capital_withdrawal":
            return e.get("ret_20d") is not None and e["ret_20d"] <= -sustained_amp
        return e.get("ret_20d") is not None and e["ret_20d"] >= sustained_amp

    probability = sum(1 for e in similar if _hit(e)) / sample

    durations = [e["duration_days"] for e in similar if e.get("duration_days") is not None]
    rets = [e["ret_20d"] for e in similar if e.get("ret_20d") is not None]
    avg_duration = sum(durations) / len(durations) if durations else None
    avg_amplitude = (sum(rets) / len(rets) * 100) if rets else None  # 转百分号

    if sample >= high_sample:
        confidence = "高"
    elif sample >= min_sample:
        confidence = "中"
    else:
        confidence = "低"

    return {
        "probability": probability,
        "avg_duration": avg_duration,
        "avg_amplitude": avg_amplitude,
        "sample_size": sample,
        "confidence": confidence,
        "regime_used": regime_used,
    }
