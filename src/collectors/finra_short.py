"""FINRA 短量数据采集器（近似实现）。

数据源限制（已实测）：
- FINRA 官方 API (api.finra.org) 返回 307 重定向，公开端点不稳定。
- 改用 yfinance 的 shortPercentOfFloat 作为做空水平近似（空头占流通股比例）。

注：yfinance 的 shortPercentOfFloat 是静态快照（非日级 Reg-SHO 短量），
    仅作为做空压力参考。如需精确日级短量，需接入 FINRA 数据订阅。
"""
import time
import yfinance as yf
from typing import Dict, Optional


def collect(symbol: str) -> Dict[str, Optional[float]]:
    """获取做空占比近似值。

    Returns:
        {"short_ratio": float|None} - 做空占流通股比例（0-1），None=不可得
    """
    try:
        time.sleep(0.3)
        info = yf.Ticker(symbol).info
        sp = info.get("shortPercentOfFloat")
        if sp is not None:
            return {"short_ratio": float(sp)}
        return {"short_ratio": None}
    except Exception:
        return {"short_ratio": None}
