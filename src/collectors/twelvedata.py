"""Twelve Data 数据采集器：量价 OHLCV。

替代 yfinance（Yahoo IP 限流）和 FMP（注册难）。
免费 800 req/天，邮箱注册无需信用卡。
API key 从环境变量 TWELVEDATA_API_KEY 读取；未配置时返回空，pipeline fallback yfinance。

端点（demo 已验证 AAPL 60天可用）：
- GET https://api.twelvedata.com/time_series?symbol=&interval=1day&outputsize=&apikey=
  返回 {"meta":..., "values":[{datetime,open,high,low,close,volume}, ...], "status":"ok"}
  values 降序（最新在前），需转升序；volume 为字符串需转换；日线不含 VWAP 用典型价近似。

参考: https://twelvedata.com/docs
"""
import os
import time
import requests
from typing import List, Dict, Optional

_BASE = "https://api.twelvedata.com"
_TIMEOUT = 15
_REQUEST_DELAY = 0.3  # 免费 800/day，保守间隔


def has_key() -> bool:
    """是否配置了 TWELVEDATA_API_KEY。"""
    return bool(os.environ.get("TWELVEDATA_API_KEY"))


def _get(params: dict) -> Optional[dict]:
    """带 key 的 GET 请求，返回 JSON 或 None（无 key/失败）。"""
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        return None
    params = {**params, "apikey": key}
    try:
        time.sleep(_REQUEST_DELAY)
        r = requests.get(_BASE + "/time_series", params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(v):
    # Twelve Data volume 是字符串，可能含小数
    try:
        return int(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


def collect_price_volume(symbol: str, days: int = 60,
                         fallback_symbol: Optional[str] = None) -> List[Dict]:
    """Twelve Data 历史价格 OHLCV，返回与 price_volume.collect 兼容格式。

    Returns:
        List[{date, symbol, close, high, low, volume, vwap}]，升序（旧->新）。
        VWAP 用典型价 (H+L+C)/3 近似（Twelve Data 日线不含 VWAP）。
        无 key 或请求失败返回 []。
    """
    def _fetch(s: str) -> List[Dict]:
        data = _get({"symbol": s, "interval": "1day", "outputsize": days})
        if not data or data.get("status") != "ok" or "values" not in data:
            return []
        result = []
        for row in data["values"]:
            high = _to_float(row.get("high"))
            low = _to_float(row.get("low"))
            close = _to_float(row.get("close"))
            vwap = None
            if high is not None and low is not None and close is not None:
                vwap = (high + low + close) / 3
            result.append({
                "date": row.get("datetime"),
                "symbol": symbol,
                "close": close,
                "high": high,
                "low": low,
                "volume": _to_int(row.get("volume")),
                "vwap": vwap,
            })
        result.reverse()  # Twelve Data 降序（最新在前），转升序
        return result

    data = _fetch(symbol)
    # 主标的数据稀少且有备选标的，尝试 fallback
    if len(data) < days // 2 and fallback_symbol:
        fb = _fetch(fallback_symbol)
        if fb:
            for item in fb:
                item["symbol"] = symbol
            return fb
    return data
