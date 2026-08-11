"""版块轮动检测器：识别版块间资金转移，查持续概率，生成轮动告警。

逻辑:
  1. 比较各版块当日 vs 前一日快照，识别"转强"/"转弱"版块
     - 转强: RS-Momentum 上穿 0（+量能确认）或 RS-Ratio 上穿 100
     - 转弱: RS-Momentum 下穿 0 或 RS-Ratio 下穿 100
  2. 转强版块配对当日最弱版块（RS-Momentum 最低）作为 from -> 表达资金转移方向
  3. 对每个转强版块查历史相似事件 -> 算持续概率 + 平均持续天数/波动幅度
  4. 生成轮动告警（含概率、历史参考、样本量、置信度）

置信度"低"时 message 显式标注（反幻觉：样本不足时警告用户）。
"""
from typing import List, Dict, Any, Optional

from src.analyzers import sector_stats
from src.schema import SEVERITY_HIGH, SEVERITY_MEDIUM


def detect_rotation(cur_snapshots: List[Dict[str, Any]],
                    prev_snapshots: List[Dict[str, Any]],
                    db_path: str,
                    thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    """检测版块轮动信号。

    Args:
        cur_snapshots: 当日各版块快照 [{symbol, sector, rs_ratio, rs_momentum, vol_zscore, date}]
        prev_snapshots: 前一交易日各版块快照（用于判断上穿/下穿）
        db_path: 数据库路径（查历史事件库算概率）
        thresholds: sector_thresholds 配置

    Returns:
        轮动事件 list [{date, from_sector, to_sector, to_symbol, probability,
                        avg_duration, avg_amplitude, sample_size, confidence,
                        severity, message}]
    """
    if not cur_snapshots:
        return []

    date = cur_snapshots[0].get("date", "")
    prev_map = {s["symbol"]: s for s in prev_snapshots}
    vol_confirm = thresholds.get("vol_confirm_zscore", 1.5)

    strong: List[Dict[str, Any]] = []
    weak: List[Dict[str, Any]] = []

    for cur in cur_snapshots:
        sym = cur.get("symbol")
        if sym == "SPY" or not sym:
            continue
        prev = prev_map.get(sym)
        if not prev:
            continue

        cur_mom = cur.get("rs_momentum")
        prev_mom = prev.get("rs_momentum")
        cur_rs = cur.get("rs_ratio")
        prev_rs = prev.get("rs_ratio")
        cur_vz = cur.get("vol_zscore")
        if any(v is None for v in (cur_mom, prev_mom, cur_rs, prev_rs)):
            continue

        # 转强: RS-Momentum 上穿 0 + 量能确认，或 RS-Ratio 上穿 100 + 动量正
        momentum_cross_up = (prev_mom <= 0 and cur_mom > 0
                             and cur_vz is not None and cur_vz > vol_confirm)
        rs_cross_up = (prev_rs <= 100 and cur_rs > 100 and cur_mom > 0)
        if momentum_cross_up or rs_cross_up:
            strong.append(cur)

        # 转弱: RS-Momentum 下穿 0，或 RS-Ratio 下穿 100
        momentum_cross_down = prev_mom >= 0 and cur_mom < 0
        rs_cross_down = prev_rs >= 100 and cur_rs < 100
        if momentum_cross_down or rs_cross_down:
            weak.append(cur)

    if not strong:
        return []

    # from_sector: 当日最弱版块（RS-Momentum 最低），表达资金流出方向
    from_sec = min(weak, key=lambda s: s.get("rs_momentum") if s.get("rs_momentum") is not None else 0) if weak else None

    alerts = []
    for to_sec in strong:
        prob = sector_stats.query_probability(
            to_sec["symbol"], to_sec.get("rs_momentum"), db_path, thresholds)

        # 严重度: 有明确 from + 概率 > 0.6 -> HIGH，否则 MEDIUM
        probability = prob.get("probability")
        severity = SEVERITY_HIGH if (from_sec and probability is not None and probability > 0.6) else SEVERITY_MEDIUM

        from_name = from_sec["sector"] if from_sec else "其他"
        message = _format_message(date, from_name, to_sec["sector"], prob)

        alerts.append({
            "date": date,
            "from_sector": from_name,
            "to_sector": to_sec["sector"],
            "to_symbol": to_sec["symbol"],
            "probability": probability,
            "avg_duration": prob.get("avg_duration"),
            "avg_amplitude": prob.get("avg_amplitude"),
            "sample_size": prob.get("sample_size"),
            "confidence": prob.get("confidence"),
            "severity": severity,
            "message": message,
        })

    return alerts


def _format_message(date: str, from_name: str, to_name: str,
                    prob: Dict[str, Any]) -> str:
    """格式化轮动告警 message。

    格式: [MM-DD] 版块轮动: 科技->能源 持续概率72% 历史均持续18天 +8.2% 样本42 ⚠️低置信度
    """
    sd = date[5:] if date and len(date) >= 10 else date  # MM-DD
    p = prob.get("probability")
    p_str = f"{p*100:.0f}%" if p is not None else "N/A"
    dur = prob.get("avg_duration")
    dur_str = f"{dur:.0f}天" if dur is not None else "N/A"
    amp = prob.get("avg_amplitude")
    amp_str = f"{amp:+.1f}%" if amp is not None else "N/A"
    conf = prob.get("confidence", "低")
    sample = prob.get("sample_size", 0)
    low_conf = " ⚠️低置信度" if conf == "低" else ""
    return (f"[{sd}] 版块轮动: {from_name}->{to_name} "
            f"持续概率{p_str} 历史均持续{dur_str} {amp_str} 样本{sample}{low_conf}")
