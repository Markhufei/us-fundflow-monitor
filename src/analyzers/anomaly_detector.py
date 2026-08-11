"""异动检测器：基于阈值检测异常信号。"""

from typing import List, Dict, Any, Optional
from src.schema import SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW


def detect(snapshot: Dict[str, Any], thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    检测异动信号。

    Args:
        snapshot: daily_snapshot 单行数据，含所有指标
        thresholds: 阈值配置字典

    Returns:
        Alert 列表，每项: {date, symbol, rule, severity, message, value, detail}
        message 含交易时间(MM-DD) + 核心数值；detail 含逐笔明细（内部人交易等）
    """
    alerts = []
    symbol = snapshot.get("symbol", "")
    date = snapshot.get("date", "")
    sd = _short_date(date)

    # 1. 成交量异常放大 (vol_zscore > threshold 或 volume_spike)
    vol_zscore = snapshot.get("vol_zscore")
    if vol_zscore is not None:
        vol_detail = _vol_detail(snapshot)
        if vol_zscore > 2.0:
            alerts.append(_make_alert(
                date, symbol, "volume_spike", SEVERITY_HIGH,
                f"[{sd}] 成交量异常放大 Z={vol_zscore:.2f}", vol_zscore, vol_detail
            ))
        elif vol_zscore > 1.5:
            alerts.append(_make_alert(
                date, symbol, "volume_spike", SEVERITY_MEDIUM,
                f"[{sd}] 成交量放大 Z={vol_zscore:.2f}", vol_zscore, vol_detail
            ))

    # 2. 价格异动
    close = snapshot.get("close")
    vwap = snapshot.get("vwap")
    if close is not None and vwap is not None and vwap > 0:
        price_move = (close - vwap) / vwap
        price_thresh = thresholds.get("price_move_pct", 0.05)
        if abs(price_move) > price_thresh:
            severity = SEVERITY_HIGH if abs(price_move) > price_thresh * 1.5 else SEVERITY_MEDIUM
            alerts.append(_make_alert(
                date, symbol, "price_move", severity,
                f"[{sd}] 价格偏离VWAP {price_move*100:.1f}% | 收盘${close:,.2f} vs VWAP${vwap:,.2f}",
                price_move
            ))

    # 3. CMF 反转
    cmf = snapshot.get("cmf")
    cmf_thresh = thresholds.get("cmf_reversal_abs", 0.3)
    if cmf is not None:
        if abs(cmf) > cmf_thresh:
            severity = SEVERITY_HIGH if abs(cmf) > cmf_thresh * 1.5 else SEVERITY_MEDIUM
            direction = "流入" if cmf > 0 else "流出"
            alerts.append(_make_alert(
                date, symbol, "cmf_reversal", severity,
                f"[{sd}] 资金{direction}强烈 CMF={cmf:.3f}", cmf
            ))

    # 4. 机构减持
    inst_change = snapshot.get("inst_change")
    inst_thresh = thresholds.get("inst_reduce_pct", 0.10)
    if inst_change is not None and inst_change < -inst_thresh:
        alerts.append(_make_alert(
            date, symbol, "inst_reduce", SEVERITY_HIGH,
            f"[{sd}] 机构季度减持 {abs(inst_change)*100:.1f}%", inst_change
        ))

    # 5. 内部人大额卖出
    insider_net = snapshot.get("insider_net")
    insider_thresh = thresholds.get("insider_sell_usd", 1_000_000)
    if insider_net is not None and insider_net < -insider_thresh:
        txns = snapshot.get("insider_transactions") or []
        # top seller：卖出额最大的内部人（写入 message，通知中心一眼可见谁在卖）
        seller_amt = {}
        for t in txns:
            if t.get("code") != "A":
                name = t.get("insider", "未知")
                seller_amt[name] = seller_amt.get(name, 0) + t.get("amount", 0)
        top = max(seller_amt.items(), key=lambda x: x[1]) if seller_amt else None
        top_str = f", top {top[0]} ${top[1]:,.0f}" if top else ""
        alerts.append(_make_alert(
            date, symbol, "insider_sell", SEVERITY_HIGH,
            f"[{sd}] 内部人净卖出 ${abs(insider_net):,.0f} ({len(txns)}笔{top_str})",
            insider_net, _insider_detail(txns)
        ))

    # 6. 短量占比异常
    short_ratio = snapshot.get("short_ratio")
    short_high = thresholds.get("short_ratio_high", 0.40)
    if short_ratio is not None:
        if short_ratio > short_high:
            alerts.append(_make_alert(
                date, symbol, "short_spike", SEVERITY_HIGH,
                f"[{sd}] 短量占比达 {short_ratio*100:.1f}%", short_ratio
            ))

    return alerts


def _short_date(date_str: str) -> str:
    """2026-07-24 -> 07-24（交易时间短格式）。"""
    return date_str[5:] if date_str and len(date_str) >= 10 else date_str


def _vol_detail(snapshot: Dict[str, Any]) -> Optional[str]:
    """成交量明细：当日量 vs 均值，放大倍数。"""
    vol = snapshot.get("volume")
    mean = snapshot.get("vol_mean")
    if vol is None or mean is None or mean == 0:
        return None
    mult = vol / mean
    return f"  当日 {int(vol):,} vs 均值 {int(mean):,} ({mult:.1f}x)"


_TCODE_DESC = {
    "P": "公开买入", "S": "公开卖出", "A": "归属RSU", "M": "期权行权",
    "F": "税收代扣", "G": "赠与", "D": "其他处置",
}


def _insider_detail(txns: List[Dict]) -> Optional[str]:
    """内部人交易逐笔明细，按内部人分组，多行。

    每组: 【姓名 (职位)】N笔 净卖出/买入 $X；每笔: MM-DD 买/卖 N股 @$P = $A [类型码]
    非 S/P 的交易类型（如 A 归属、F 税收代扣、G 赠与）标注 transactionCode。
    """
    if not txns:
        return None
    groups = {}  # (insider, title) -> txns，dict 保持插入顺序（Py3.7+）
    for t in txns:
        key = (t.get("insider", "未知"), t.get("title", ""))
        groups.setdefault(key, []).append(t)
    lines = []
    for (insider, title), grp in groups.items():
        sell = sum(t["amount"] for t in grp if t.get("code") != "A")
        buy = sum(t["amount"] for t in grp if t.get("code") == "A")
        who = insider + (f" ({title})" if title else "")
        net = f"净卖出 ${sell:,.0f}" if sell >= buy else f"净买入 ${buy:,.0f}"
        lines.append(f"【{who}】{len(grp)}笔 {net}")
        for t in grp:
            sd = _short_date(t.get("date", ""))
            action = "买" if t.get("code") == "A" else "卖"
            tcode = t.get("tcode", "")
            tag = f" [{tcode}:{_TCODE_DESC.get(tcode, '?')}]" if tcode and tcode not in ("S", "P") else ""
            lines.append(f"  {sd} {action} {t['shares']:,.0f}股 @${t['price']:,.2f} = ${t['amount']:,.0f}{tag}")
    return "\n".join(lines)


def _make_alert(date: str, symbol: str, rule: str, severity: str, message: str,
                value: float | None, detail: Optional[str] = None) -> Dict[str, Any]:
    """构造 Alert 对象。"""
    return {
        "date": date,
        "symbol": symbol,
        "rule": rule,
        "severity": severity,
        "message": message,
        "value": float(value) if value is not None else None,
        "detail": detail,
    }
