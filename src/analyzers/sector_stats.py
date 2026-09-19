"""版块轮动历史事件库 + 概率模型（Phase 3 核心，2026-09-01 方法学重做）。

逻辑:
  1. identify_events: 在 RS 时间序列中识别"版块转强"事件
     - momentum_cross: RS-Momentum 确认上穿 0（同向连续 confirm_days 天且窗口前一日反向）
       + 量能确认（vol_zscore > 阈值）
     - rs_cross: RS-Ratio 上穿 100 + RS-Momentum 同向连续 confirm_days 天
  2. compute_outcomes: 对每个事件算后续 5/10/20/60 天相对 SPY 的超额收益 + 持续天数。
     基准日 = 事件次日收盘（T+1，与实盘可执行时点一致；旧版用事件日收盘偏乐观）
  3. build_event_library: 全量回测（25 年 × 12 版块）产事件库
  4. query_probability: 新事件触发时查历史相似事件（同版块+同触发类型+RS-Momentum
     绝对容差），算"持续概率"

"持续"定义（2026-09-01 重估，数据依据见 README）:
  ret_20d >= sustained_amplitude（默认 2%）
  旧定义（duration>=10 且 ret_10d>=3%）基准率仅 4.8%，概率输出被锁死在低区间；
  新定义单阈值基准率 22.8%（全体）/ 24.1%（momentum_cross），保留区分度。

概率 = 相似事件中"持续"的比例；置信度按样本量分级（高/中/低）。
所有数值基于真实历史回测，非主观打分。
"""
import math
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

from src.storage import store


def identify_events(df: pd.DataFrame,
                    vol_confirm: float = 1.5,
                    confirm_days: int = 2,
                    mom_cross_level: float = 0.0,
                    rs_cross_level: float = 100.0) -> List[Dict[str, Any]]:
    """在 RS 时间序列中识别版块转强事件（带去抖确认）。

    去抖语义（与 backtest_apple_storage_rotation.py 一致）:
      momentum_cross 在"确认日"触发：RS-Momentum 连续 confirm_days 天 > mom_cross_level，
      且窗口前一日 <= mom_cross_level（新鲜交叉，长正段中段不重复触发）。
      rs_cross 在 RS-Ratio 上穿日触发：当日 RS-Ratio 上穿 rs_cross_level 且
      RS-Momentum 已连续 confirm_days 天为正（不要求新鲜）。

    Args:
        df: 含列 date, rs_ratio, rs_momentum, vol_zscore，按 date 升序
        vol_confirm: 量能确认阈值（vol_zscore > 此值才算 momentum_cross）
        confirm_days: 去抖确认天数（1 = 无去抖）
        mom_cross_level: RS-Momentum 交叉电平（默认 0）
        rs_cross_level: RS-Ratio 交叉电平（默认 100）

    Returns:
        事件列表 [{event_date, trigger_type, rs_momentum, vol_zscore}]
        同日触发两类时只记一条（优先 momentum_cross）
    """
    events = []
    if df.empty or confirm_days < 1:
        return events

    mom = df["rs_momentum"].astype(float)
    pos = mom > mom_cross_level  # NaN -> False
    # 同向连续 confirm_days 天
    steady_pos = pos & (pos.rolling(confirm_days, min_periods=confirm_days).sum() == confirm_days)
    # 新鲜交叉：confirm_days 天窗口之前一日不为正
    fresh = ~pos.shift(confirm_days, fill_value=False)
    confirmed_up = steady_pos.fillna(False)
    fresh_up = (steady_pos & fresh).fillna(False)

    prev_rs = None
    for i in range(len(df)):
        rs = df["rs_ratio"].iloc[i]
        mom_i = mom.iloc[i]
        vz = df["vol_zscore"].iloc[i] if "vol_zscore" in df.columns else None
        date = df["date"].iloc[i]

        if pd.isna(mom_i) or pd.isna(rs):
            prev_rs = rs
            continue

        fired_this_day = False
        # A. RS-Momentum 确认上穿 + 量能确认
        if fresh_up.iloc[i] and vz is not None and not pd.isna(vz) and vz > vol_confirm:
            events.append({
                "event_date": date, "trigger_type": "momentum_cross",
                "rs_momentum": float(mom_i), "vol_zscore": float(vz),
            })
            fired_this_day = True

        # B. RS-Ratio 上穿 + RS-Momentum 连续 confirm_days 天为正（同日未触发 A 才记）
        if not fired_this_day and confirmed_up.iloc[i] \
                and prev_rs is not None and not pd.isna(prev_rs) \
                and prev_rs <= rs_cross_level and rs > rs_cross_level:
            events.append({
                "event_date": date, "trigger_type": "rs_cross",
                "rs_momentum": float(mom_i),
                "vol_zscore": float(vz) if vz is not None and not pd.isna(vz) else None,
            })

        prev_rs = rs

    return events


def compute_outcomes(event_idx: int,
                     sector_close: List[float],
                     spy_close: List[float],
                     horizons: Optional[List[int]] = None) -> Dict[str, Optional[float]]:
    """算事件后各 horizon 的超额收益 + 持续天数（T+1 收盘入场基准）。

    基准 = close[event_idx + 1]（事件日收盘出信号，次日收盘可执行；
    旧版用事件日收盘，隐含当日收盘成交，系统性偏乐观）。

    Args:
        event_idx: 事件日在序列中的索引
        sector_close: 版块收盘价序列（升序）
        spy_close: SPY 收盘价序列（升序，等长）
        horizons: 收益计算窗口（自入场日起），默认 [5, 10, 20, 60]

    Returns:
        {ret_5d, ret_10d, ret_20d, ret_60d, duration_days}
        超额收益 = sector_return - spy_return；持续天数 = 入场次日起连续超额累计>0 的天数
    """
    if horizons is None:
        horizons = [5, 10, 20, 60]

    result: Dict[str, Optional[float]] = {}
    n = min(len(sector_close), len(spy_close))
    entry = event_idx + 1
    if entry >= n:
        for h in horizons:
            result[f"ret_{h}d"] = None
        result["duration_days"] = 0
        return result

    base_sec = sector_close[entry]
    base_spy = spy_close[entry]

    for h in horizons:
        end = entry + h
        if end < n and base_sec > 0 and base_spy > 0:
            sec_ret = sector_close[end] / base_sec - 1
            spy_ret = spy_close[end] / base_spy - 1
            result[f"ret_{h}d"] = sec_ret - spy_ret
        else:
            result[f"ret_{h}d"] = None

    # 持续天数：自入场次日起，连续"累计超额收益 > 0"的天数（最多看 60 天）
    duration = 0
    for i in range(entry + 1, min(entry + 61, n)):
        if base_sec <= 0 or base_spy <= 0:
            break
        sec_ret = sector_close[i] / base_sec - 1
        spy_ret = spy_close[i] / base_spy - 1
        if sec_ret > spy_ret:
            duration += 1
        else:
            break
    result["duration_days"] = duration

    return result


def query_probability(symbol: str,
                      rs_momentum: Optional[float],
                      db_path: str = "data/fundflow.db",
                      thresholds: Optional[Dict[str, Any]] = None,
                      trigger_type: Optional[str] = None,
                      spy_above_ma200: Optional[int] = None) -> Dict[str, Any]:
    """查历史相似事件，算大资金移仓的持续概率。

    Args:
        symbol: 版块 ETF 代号（如 XLE）
        rs_momentum: 当前事件的 RS-Momentum（相似度匹配用，None 则查该 symbol 全部）
        db_path: 数据库路径
        thresholds: 含 sustained_amplitude, momentum_tol_abs, prob_min_sample, prob_high_sample
        trigger_type: 触发类型过滤（momentum_cross / rs_cross）。
            生产必须传，防止跨类型污染——库内 84% 为 rs_cross，混入会使
            "量能确认的 momentum cross"概率被无量能确认事件稀释（2026-09-01 审查实证）。
        spy_above_ma200: 当前 SPY regime（1=收盘>MA200，0=下方，None=未知）。
            2026-09-20 实测（n=1298）：MA200 下方 rs_cross 持续率 33% vs 上方 24%，
            是唯一有增量的市场状态变量。regime 内样本 < prob_min_sample 时回退全样本。

    Returns:
        {probability, avg_duration, avg_amplitude, sample_size, confidence, regime_used}
        probability: 0-1，相似事件中 ret_20d >= sustained_amplitude 的比例；样本为 0 时 None
        avg_duration: 平均持续天数
        avg_amplitude: 平均 20 日超额收益（%，转百分号便于展示）
        confidence: 高/中/低（按样本量）
        regime_used: 是否按 regime 分桶匹配（False = 样本不足回退全样本，或未传 regime）
    """
    if thresholds is None:
        thresholds = {}

    sustained_amp = thresholds.get("sustained_amplitude", 0.02)
    tol_abs = thresholds.get("momentum_tol_abs", 0.5)
    min_sample = thresholds.get("prob_min_sample", 10)
    high_sample = thresholds.get("prob_high_sample", 30)

    # momentum_cross 的动量容差匹配无信息（分位无单调性，n≈41/格），退化为
    # 同 symbol + 同触发类型基准率；rs_cross 保留动量窗口
    match_momentum = rs_momentum if trigger_type != "momentum_cross" else None

    regime_used = False
    similar: list = []
    if spy_above_ma200 is not None:
        similar = store.query_history_events(symbol, match_momentum, momentum_tol=tol_abs,
                                             db_path=db_path, trigger_type=trigger_type,
                                             spy_above_ma200=spy_above_ma200)
        regime_used = len(similar) >= min_sample
    if not regime_used:
        similar = store.query_history_events(symbol, match_momentum, momentum_tol=tol_abs,
                                             db_path=db_path, trigger_type=trigger_type)
        regime_used = False
    sample = len(similar)

    if sample == 0:
        return {"probability": None, "avg_duration": None, "avg_amplitude": None,
                "sample_size": 0, "confidence": "低", "regime_used": False}

    # "持续"判定：事件后 20 交易日超额收益 >= sustained_amplitude（单阈值，基准率 22.8%）
    sustained = [
        e for e in similar
        if e.get("ret_20d") is not None
        and e["ret_20d"] >= sustained_amp
    ]
    probability = len(sustained) / sample

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


def wilson_ci(p: float, n: int, z: float = 1.96) -> Tuple[float, float]:
    """二项比例的 Wilson 95% 置信区间（样本小时比正态近似稳健）。

    Returns:
        (lo, hi) 截断到 [0, 1]
    """
    if n <= 0:
        return 0.0, 1.0
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)
