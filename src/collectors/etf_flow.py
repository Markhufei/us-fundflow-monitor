"""ETF 资金流采集器。

数据源限制（已实测）：
- iShares 官方 JSON 端点返回 HTML（非 JSON），不可用。
- yfinance 不直接提供 ETF 资金净流入（净申购/赎回）数据。
- ETF 资金流需专业数据源（Bloomberg/Morningstar），免费源不可得。

当前方案：返回 None。ETF（SOXX/SMH）的资金异动通过 price_volume 的量价指标
+ anomaly_detector 的成交量异常/价格异动规则间接监控（已覆盖）。

TODO: 接入 Morningstar 等专业数据源后启用真实资金流。
"""
from typing import Dict, Optional


def collect(symbol: str) -> Dict[str, Optional[float]]:
    """ETF 资金净流入（当前免费源不可得，返回 None）。

    ETF 监控依赖 price_volume + anomaly_detector 的量价异动间接反映。
    """
    return {"flow": None}
