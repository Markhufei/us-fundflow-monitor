"""指数信号检测器：对 DB 历史跑同一 identify 函数，过滤当日事件，查概率生成告警。

detect = 建库回测与日常检测一套代码（identify_index_events），杜绝双实现漂移。
severity: probability > 0.6 且置信度"高" -> HIGH，其余 MEDIUM（镜像 sector_detector，
低样本高概率不误发 HIGH）。
注意: index_hist_days=250 未积累前 DB 历史不足，identify 特征未热 -> 无信号，属设计内行为。
"""
import pandas as pd

from src.analyzers import index_signals
from src.schema import INDEX_TRIGGER_LABELS, SEVERITY_HIGH, SEVERITY_MEDIUM
from src.storage import store


def detect_index_signals(cur_snapshots: list,
                         db_path: str,
                         thresholds: dict,
                         periods: dict | None = None,
                         spy_above_ma200: int | None = None) -> list:
    """检测指数当日信号。

    Args:
        cur_snapshots: 当日指数快照（kind=index 行，含 date/symbol/name）
        db_path: 数据库路径（读最近 index_hist_days 天历史跑 identify + 查事件库算概率）
        thresholds: index_thresholds 配置
        periods: index_periods 配置（读 index_hist_days）
        spy_above_ma200: 当前 SPY regime（1/0/None），透传概率查询做分桶匹配

    Returns:
        alert 列表 [{date, symbol, rule, severity, message, value}]，
        rule = "index_" + trigger_type（alerts 表 UNIQUE(date,symbol,rule) 天然幂等）
    """
    if not cur_snapshots:
        return []
    periods = periods or {}
    hist_days = int(periods.get("index_hist_days", 250))
    high_sample = thresholds.get("prob_high_sample", 30)

    alerts = []
    for snap in cur_snapshots:
        symbol = snap.get("symbol")
        date = snap.get("date")
        if not symbol or not date:
            continue
        hist = store.load_index_history(symbol, days=hist_days, db_path=db_path)
        if not hist:
            continue
        df = pd.DataFrame(hist)
        todays = [e for e in index_signals.identify_index_events(df, thresholds)
                  if e.get("event_date") == date]
        for ev in todays:
            prob = index_signals.query_index_probability(
                symbol, ev["trigger_type"], db_path=db_path, thresholds=thresholds,
                rs_momentum=ev.get("rs_momentum"),
                spy_above_ma200=spy_above_ma200)
            probability = prob.get("probability")
            severity = SEVERITY_HIGH if (probability is not None and probability > 0.6
                                         and prob.get("confidence") == "高") else SEVERITY_MEDIUM
            alerts.append({
                "date": date,
                "symbol": symbol,
                "rule": f"index_{ev['trigger_type']}",
                "trigger_type": ev["trigger_type"],
                "severity": severity,
                "message": _format_message(date, snap.get("name") or symbol,
                                           ev["trigger_type"], prob, ev, high_sample),
                "value": probability,
                "probability": probability,
                "sample_size": prob.get("sample_size"),
                "confidence": prob.get("confidence"),
                "rs_momentum": ev.get("rs_momentum"),
            })
    return alerts


def _format_message(date: str, name: str, trigger_type: str,
                    prob: dict, event: dict, high_sample: int = 30) -> str:
    """格式化指数信号 message（镜像 sector_detector 格式）。

    格式: [MM-DD] 指数信号: 标普500 回调结束反弹 延续概率72% CI95[58%~83%] 均持续18天 +5.2% 样本42 ⚠️低置信度
    概率措辞按类型: pullback_start=预警命中率 / capital_withdrawal=下跌概率 / 其余=延续概率。
    """
    label = INDEX_TRIGGER_LABELS.get(trigger_type, trigger_type)
    if trigger_type == "pullback_start":
        p_word = "预警命中率"
    elif trigger_type == "capital_withdrawal":
        p_word = "下跌概率"
    else:
        p_word = "延续概率"

    sd = date[5:] if date and len(date) >= 10 else date  # MM-DD
    p = prob.get("probability")
    p_str = f"{p*100:.0f}%" if p is not None else "N/A"
    sample = prob.get("sample_size", 0)
    ci_str = ""
    if p is not None and sample < high_sample:
        from src.analyzers import sector_stats
        lo, hi = sector_stats.wilson_ci(p, sample)
        ci_str = f" CI95[{lo*100:.0f}%~{hi*100:.0f}%]"
    dur = prob.get("avg_duration")
    dur_str = f"{dur:.0f}天" if dur is not None else "N/A"
    amp = prob.get("avg_amplitude")
    amp_str = f"{amp:+.1f}%" if amp is not None else "N/A"
    conf = prob.get("confidence", "低")
    low_conf = " ⚠️低置信度" if conf == "低" else ""
    dd = event.get("dd20")
    dd_str = f" dd20={dd*100:.1f}%" if dd is not None else ""
    return (f"[{sd}] 指数信号: {name} {label} {p_word}{p_str}{ci_str} "
            f"均持续{dur_str} {amp_str} 样本{sample}{dd_str}{low_conf}"
            f"（历史统计参考非预测）")
