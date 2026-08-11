"""版块轮动历史事件库 + 概率模型（Phase 3 核心）。

逻辑:
  1. identify_events: 在 RS 时间序列中识别"版块转强"事件
     - momentum_cross: RS-Momentum 上穿 0 + 量能确认（vol_zscore > 阈值）
     - rs_cross: RS-Ratio 上穿 100 + RS-Momentum > 0
  2. compute_outcomes: 对每个事件算后续 5/10/20/60 天相对 SPY 的超额收益 + 持续天数
  3. build_event_library: 全量回测（25 年 × 11 版块）产事件库
  4. query_probability: 新事件触发时查历史相似事件，算"持续概率"

"持续"定义（可在 thresholds 配置）:
  duration_days >= sustained_days 且 ret_10d >= sustained_amplitude

概率 = 相似事件中"持续"的比例；置信度按样本量分级（高/中/低）。
所有数值基于真实历史回测，非主观打分。
"""
import math
from typing import List, Dict, Any, Optional

import pandas as pd

from src.storage import store


def identify_events(df: pd.DataFrame,
                    vol_confirm: float = 1.5) -> List[Dict[str, Any]]:
    """在 RS 时间序列中识别版块转强事件。

    Args:
        df: 含列 date, rs_ratio, rs_momentum, vol_zscore，按 date 升序
        vol_confirm: 量能确认阈值（vol_zscore > 此值才算 momentum_cross）

    Returns:
        事件列表 [{event_date, trigger_type, rs_momentum, vol_zscore}]
        同日触发两类时只记一条（优先 momentum_cross）
    """
    events = []
    prev_mom = None
    prev_rs = None

    for _, row in df.iterrows():
        mom = row.get("rs_momentum")
        rs = row.get("rs_ratio")
        vz = row.get("vol_zscore")
        date = row.get("date")

        if pd.isna(mom) or pd.isna(rs):
            prev_mom = mom
            prev_rs = rs
            continue

        fired_this_day = False
        # A. RS-Momentum 上穿 0 + 量能确认
        if prev_mom is not None and not pd.isna(prev_mom):
            if prev_mom <= 0 and mom > 0 and not pd.isna(vz) and vz > vol_confirm:
                events.append({
                    "event_date": date, "trigger_type": "momentum_cross",
                    "rs_momentum": float(mom), "vol_zscore": float(vz) if not pd.isna(vz) else None,
                })
                fired_this_day = True

        # B. RS-Ratio 上穿 100 + RS-Momentum > 0（同日未触发 A 才记）
        if not fired_this_day and prev_rs is not None and not pd.isna(prev_rs):
            if prev_rs <= 100 and rs > 100 and mom > 0:
                events.append({
                    "event_date": date, "trigger_type": "rs_cross",
                    "rs_momentum": float(mom), "vol_zscore": float(vz) if not pd.isna(vz) else None,
                })

        prev_mom = mom
        prev_rs = rs

    return events


def compute_outcomes(event_idx: int,
                     sector_close: List[float],
                     spy_close: List[float],
                     horizons: Optional[List[int]] = None) -> Dict[str, Optional[float]]:
    """算事件后各 horizon 的超额收益 + 持续天数。

    Args:
        event_idx: 事件日在序列中的索引
        sector_close: 版块收盘价序列（升序）
        spy_close: SPY 收盘价序列（升序，等长）
        horizons: 收益计算窗口，默认 [5, 10, 20, 60]

    Returns:
        {ret_5d, ret_10d, ret_20d, ret_60d, duration_days}
        超额收益 = sector_return - spy_return；持续天数 = 连续超额累计>0 的天数
    """
    if horizons is None:
        horizons = [5, 10, 20, 60]

    result: Dict[str, Optional[float]] = {}
    n = min(len(sector_close), len(spy_close))
    base_sec = sector_close[event_idx]
    base_spy = spy_close[event_idx]

    for h in horizons:
        end = event_idx + h
        if end < n and base_sec > 0 and base_spy > 0:
            sec_ret = sector_close[end] / base_sec - 1
            spy_ret = spy_close[end] / base_spy - 1
            result[f"ret_{h}d"] = sec_ret - spy_ret
        else:
            result[f"ret_{h}d"] = None

    # 持续天数：从事件次日起，连续"累计超额收益 > 0"的天数（最多看 60 天）
    duration = 0
    for i in range(event_idx + 1, min(event_idx + 61, n)):
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
                      thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """查历史相似事件，算大资金移仓的持续概率。

    Args:
        symbol: 版块 ETF 代号（如 XLE）
        rs_momentum: 当前事件的 RS-Momentum（相似度匹配用，None 则查该 symbol 全部）
        db_path: 数据库路径
        thresholds: 含 sustained_days, sustained_amplitude, prob_min_sample, prob_high_sample

    Returns:
        {probability, avg_duration, avg_amplitude, sample_size, confidence}
        probability: 0-1，相似事件中"持续"的比例；样本为 0 时 None
        avg_duration: 平均持续天数
        avg_amplitude: 平均 10 日超额收益（%，转百分号便于展示）
        confidence: 高/中/低（按样本量）
    """
    if thresholds is None:
        thresholds = {}

    sustained_days = thresholds.get("sustained_days", 10)
    sustained_amp = thresholds.get("sustained_amplitude", 0.03)
    min_sample = thresholds.get("prob_min_sample", 10)
    high_sample = thresholds.get("prob_high_sample", 30)

    similar = store.query_history_events(symbol, rs_momentum, db_path=db_path)
    sample = len(similar)

    if sample == 0:
        return {"probability": None, "avg_duration": None, "avg_amplitude": None,
                "sample_size": 0, "confidence": "低"}

    # "持续"判定：duration_days >= sustained_days 且 ret_10d >= sustained_amplitude
    sustained = [
        e for e in similar
        if e.get("duration_days") is not None
        and e["duration_days"] >= sustained_days
        and e.get("ret_10d") is not None
        and e["ret_10d"] >= sustained_amp
    ]
    probability = len(sustained) / sample

    durations = [e["duration_days"] for e in similar if e.get("duration_days") is not None]
    rets = [e["ret_10d"] for e in similar if e.get("ret_10d") is not None]
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
    }
