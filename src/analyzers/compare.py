"""对比模块：计算当前与历史快照的变化。"""

from typing import Dict, Any


def compare(today: Dict[str, Any], past: Dict[str, Any], label: str) -> Dict[str, Any]:
    """
    对比两个快照的变化。

    Args:
        today: 当前快照
        past: 历史快照
        label: 对比标签，如 "2日对比" / "周对比"

    Returns:
        {symbol, label, price_chg_pct, volume_chg_pct, cmf_delta}
    """
    symbol = today.get("symbol", "")

    # 价格变化百分比
    price_chg_pct = None
    if today.get("close") is not None and past.get("close") is not None and past["close"] != 0:
        price_chg_pct = (today["close"] - past["close"]) / past["close"]

    # 成交量变化百分比
    volume_chg_pct = None
    if today.get("volume") is not None and past.get("volume") is not None and past["volume"] != 0:
        volume_chg_pct = (today["volume"] - past["volume"]) / past["volume"]

    # CMF 变化量
    cmf_delta = None
    if today.get("cmf") is not None and past.get("cmf") is not None:
        cmf_delta = today["cmf"] - past["cmf"]

    return {
        "symbol": symbol,
        "label": label,
        "price_chg_pct": float(price_chg_pct) if price_chg_pct is not None else None,
        "volume_chg_pct": float(volume_chg_pct) if volume_chg_pct is not None else None,
        "cmf_delta": float(cmf_delta) if cmf_delta is not None else None,
    }
