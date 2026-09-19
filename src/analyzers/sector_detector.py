"""版块轮动检测器：识别版块间资金转移，查持续概率，生成轮动告警。

逻辑（2026-09-01 方法学重做，落地 08-14 回测的改进方向）:
  1. 比较各版块当日 vs 前一日快照，识别"转强"版块（带去抖确认）
     - momentum_cross: RS-Momentum 同向连续 confirm_days 天 > 电平，且窗口前一日反向
       （新鲜交叉，不重复告警）+ 量能确认
     - rs_cross: RS-Ratio 上穿 100 + RS-Momentum 已连续 confirm_days 天为正
  2. from_sector 取当日 RS-Momentum 最低的其他版块（旧版要求同日下穿，实测
     5 条告警 4 条退化为"其他"）
  3. 对每个转强版块查历史相似事件（同触发类型）-> 算持续概率 + 平均持续天数/波动幅度
  4. 生成轮动告警（含概率、Wilson 95% CI、样本量、置信度）
     severity 由 confidence + probability 联合判定（旧版 probability>0.6 即 HIGH，
     样本 3 全持续 p=1.0 会误发 HIGH）

去抖依据（2026-08-31 回测, 2025-10-01~2026-08-31 扣 0.1%/笔）:
  period=10 无去抖切换 37-55 次/年且 4/5 对净收益最差；period=30 + 去抖 2 天
  切换降至 9-17 次，版块对（XLK/XLE/XLF vs SPY）平均净收益最优。
  周期参数在 sector_periods（rs_*_period），去抖在 sector_thresholds
  （momentum_confirm_days），与事件库建库脚本共用同一配置。

置信度"低"时 message 显式标注（反幻觉：样本不足时警告用户）。
注意: 部署切换期（约 2×period 个交易日）内，DB 历史快照混有旧 period 计算的
RS 值，去抖判定在该期间语义有偏，属已知过渡态。
"""
from typing import List, Dict, Any, Optional

from src.analyzers import sector_stats
from src.storage import store
from src.schema import SEVERITY_HIGH, SEVERITY_MEDIUM


def detect_rotation(cur_snapshots: List[Dict[str, Any]],
                    prev_snapshots: List[Dict[str, Any]],
                    db_path: str,
                    thresholds: Dict[str, Any],
                    spy_above_ma200: Optional[int] = None) -> List[Dict[str, Any]]:
    """检测版块轮动信号。

    Args:
        cur_snapshots: 当日各版块快照 [{symbol, sector, rs_ratio, rs_momentum, vol_zscore, date}]
        prev_snapshots: 前一交易日各版块快照（DB 历史缺失时的兜底数据源）
        db_path: 数据库路径（读历史快照做去抖 + 查历史事件库算概率）
        thresholds: sector_thresholds 配置
        spy_above_ma200: 当前 SPY regime（1/0/None），透传概率查询做分桶匹配

    Returns:
        轮动事件 list [{date, from_sector, to_sector, to_symbol, trigger_type,
                        probability, avg_duration, avg_amplitude, sample_size,
                        confidence, severity, message}]
    """
    if not cur_snapshots:
        return []

    date = cur_snapshots[0].get("date", "")
    prev_map = {s["symbol"]: s for s in prev_snapshots}
    vol_confirm = thresholds.get("vol_confirm_zscore", 1.5)
    confirm_days = thresholds.get("momentum_confirm_days", 2)
    mom_level = thresholds.get("rs_momentum_cross", 0.0)
    rs_level = thresholds.get("rs_ratio_cross", 100.0)
    high_sample = thresholds.get("prob_high_sample", 30)

    strong: List[Dict[str, Any]] = []

    for cur in cur_snapshots:
        sym = cur.get("symbol")
        if sym == "SPY" or not sym:
            continue

        rows = _history_rows(sym, cur, prev_map.get(sym), confirm_days + 1, db_path)
        if len(rows) < confirm_days + 1:
            continue  # 历史不足以做去抖确认，不发信号

        moms = [r.get("rs_momentum") for r in rows]
        rss = [r.get("rs_ratio") for r in rows]
        if any(v is None for v in moms) or any(v is None for v in rss):
            continue

        cur_mom = moms[-1]
        cur_rs = rss[-1]
        cur_vz = cur.get("vol_zscore")

        # 去抖确认: 最近 confirm_days 天 mom 全部 > 电平，且窗口前一日 <= 电平（新鲜）
        window = moms[-(confirm_days + 1):]
        confirmed_up = all(m > mom_level for m in window[1:]) and window[0] <= mom_level

        momentum_cross_up = (confirmed_up
                             and cur_vz is not None and cur_vz > vol_confirm)
        rs_cross_up = (rss[-2] <= rs_level and cur_rs > rs_level and cur_mom > mom_level
                       and _steady_positive(moms, confirm_days, mom_level))
        if momentum_cross_up or rs_cross_up:
            sig = dict(cur)
            sig["_trigger"] = "momentum_cross" if momentum_cross_up else "rs_cross"
            strong.append(sig)

    if not strong:
        return []

    alerts = []
    for to_sec in strong:
        prob = sector_stats.query_probability(
            to_sec["symbol"], to_sec.get("rs_momentum"), db_path, thresholds,
            trigger_type=to_sec["_trigger"], spy_above_ma200=spy_above_ma200)

        # 严重度: 概率 > 0.6 + 高置信 -> HIGH（低样本高概率不误发）
        probability = prob.get("probability")
        confidence = prob.get("confidence")
        severity = SEVERITY_HIGH if (probability is not None and probability > 0.6
                                     and confidence == "高") else SEVERITY_MEDIUM

        from_sec = _weakest_sector(cur_snapshots, exclude_symbol=to_sec["symbol"])
        from_name = from_sec["sector"] if from_sec else "其他"
        message = _format_message(date, from_name, to_sec["sector"], prob, high_sample)

        alerts.append({
            "date": date,
            "from_sector": from_name,
            "to_sector": to_sec["sector"],
            "to_symbol": to_sec["symbol"],
            "trigger_type": to_sec["_trigger"],
            "probability": probability,
            "avg_duration": prob.get("avg_duration"),
            "avg_amplitude": prob.get("avg_amplitude"),
            "sample_size": prob.get("sample_size"),
            "confidence": confidence,
            "severity": severity,
            "message": message,
        })

    return alerts


def _history_rows(symbol: str, cur: Dict[str, Any],
                  prev: Optional[Dict[str, Any]],
                  n_days: int, db_path: str) -> List[Dict[str, Any]]:
    """取该 symbol 最近 n_days 天快照（升序）。

    生产路径: pipeline 先存当日快照再调本函数，DB 即含当日。
    DB 不可用/无记录时（如单测直传快照），用传入的 cur/prev 兜底合并。
    """
    rows: Dict[str, Dict[str, Any]] = {}
    if db_path:
        try:
            for r in store.load_sector_history(symbol, days=n_days, db_path=db_path):
                rows[r["date"]] = r
        except Exception:
            pass
    if prev is not None and prev.get("date"):
        rows.setdefault(prev["date"], prev)
    if cur.get("date"):
        rows.setdefault(cur["date"], cur)
    return [rows[d] for d in sorted(rows)][-n_days:]


def _steady_positive(moms: List[float], confirm_days: int, level: float) -> bool:
    """RS-Momentum 是否已连续 confirm_days 天 > 电平（不要求新鲜，rs_cross 用）。"""
    if confirm_days < 1 or len(moms) < confirm_days:
        return False
    return all(m > level for m in moms[-confirm_days:])


def _weakest_sector(cur_snapshots: List[Dict[str, Any]],
                    exclude_symbol: str) -> Optional[Dict[str, Any]]:
    """当日 RS-Momentum 最低的其他版块（表达资金流出方向）。"""
    cands = [s for s in cur_snapshots
             if s.get("symbol") and s["symbol"] not in ("SPY", exclude_symbol)
             and s.get("rs_momentum") is not None]
    if not cands:
        return None
    return min(cands, key=lambda s: s["rs_momentum"])


def _format_message(date: str, from_name: str, to_name: str,
                    prob: Dict[str, Any], high_sample: int = 30) -> str:
    """格式化轮动告警 message。

    格式: [MM-DD] 版块轮动: 科技->能源 持续概率72% CI95[58%~83%] 历史均持续18天 +8.2% 样本42 ⚠️低置信度
    样本 < 高置信阈值时附 Wilson 95% CI（点估计在小样本下精度虚高）。
    """
    sd = date[5:] if date and len(date) >= 10 else date  # MM-DD
    p = prob.get("probability")
    p_str = f"{p*100:.0f}%" if p is not None else "N/A"
    sample = prob.get("sample_size", 0)
    ci_str = ""
    if p is not None and sample < high_sample:
        lo, hi = sector_stats.wilson_ci(p, sample)
        ci_str = f" CI95[{lo*100:.0f}%~{hi*100:.0f}%]"
    dur = prob.get("avg_duration")
    dur_str = f"{dur:.0f}天" if dur is not None else "N/A"
    amp = prob.get("avg_amplitude")
    amp_str = f"{amp:+.1f}%" if amp is not None else "N/A"
    conf = prob.get("confidence", "低")
    low_conf = " ⚠️低置信度" if conf == "低" else ""
    return (f"[{sd}] 版块轮动: {from_name}->{to_name} "
            f"持续概率{p_str}{ci_str} 历史均持续{dur_str} {amp_str} 样本{sample}{low_conf}")
