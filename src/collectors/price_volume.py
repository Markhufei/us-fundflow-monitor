"""
价格成交量数据采集器
优先 Twelve Data（需 TWELVEDATA_API_KEY，免费 800 req/天），fallback yfinance（Yahoo IP 级限流风险）
"""
import time
import pandas as pd
import yfinance as yf
from typing import List, Dict, Optional


def collect(symbol: str, days: int = 30, fallback_symbol: Optional[str] = None) -> List[Dict]:
    """
    获取指定标的的价格和成交量数据，优先 FMP，fallback yfinance。

    Args:
        symbol: 标的代码
        days: 获取的天数
        fallback_symbol: 当主标的数据不可用时的备选标的

    Returns:
        包含日期、标的代码、收盘价、成交量、VWAP 的字典列表
    """
    # 优先 Twelve Data（需 TWELVEDATA_API_KEY），失败 fallback yfinance
    from src.collectors import twelvedata
    if twelvedata.has_key():
        data = twelvedata.collect_price_volume(symbol, days, fallback_symbol)
        if data:
            return data

    def _fetch_data(s: str) -> List[Dict]:
        # yfinance 偶发限流（YFRateLimitError），重试 2 次，间隔 30s
        hist = None
        for attempt in range(3):
            try:
                hist = yf.Ticker(s).history(period=f"{days}d")
                break
            except Exception as e:
                if attempt < 2 and "RateLimit" in type(e).__name__:
                    time.sleep(30)
                    continue
                return []
        if hist is None or hist.empty:
            return []

        result = []
        for date, row in hist.iterrows():
            # 计算 VWAP (yfinance 日线不含 VWAP，用典型价近似)
            vwap = row.get("VWAP")
            if vwap is None:
                typical_price = (row["High"] + row["Low"] + row["Close"]) / 3
                vwap = typical_price

            # NaN 判空：pandas 缺失值是 NaN 不是 None（int(NaN) 会抛 ValueError）
            close = None if pd.isna(row["Close"]) else float(row["Close"])
            high = None if pd.isna(row["High"]) else float(row["High"])
            low = None if pd.isna(row["Low"]) else float(row["Low"])
            volume = None if pd.isna(row["Volume"]) else int(row["Volume"])

            result.append({
                "date": date.strftime("%Y-%m-%d"),
                "symbol": symbol,
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
                "vwap": float(vwap)
            })

        return result

    # 尝试获取主标的数据
    data = _fetch_data(symbol)

    # 如果数据稀少且有备选标的，尝试获取备选数据
    if len(data) < days // 2 and fallback_symbol:
        time.sleep(1)  # 请求间隔
        fallback_data = _fetch_data(fallback_symbol)
        # 用备选标的的数据补充，但保持原始 symbol
        if fallback_data:
            for item in fallback_data:
                item["symbol"] = symbol
            return fallback_data

    return data
