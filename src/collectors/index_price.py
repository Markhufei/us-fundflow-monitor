"""指数本体数据采集器（yfinance 通道）。

Twelve Data 免费档不支持指数（SPX/NDX/INX 需付费；2026-10-16 实测 IXIC/DJI/GSPC 404），
指数本体走 yfinance ^ 前缀符号。代理/杠杆 ETF（资金流指标来源）走现有
price_volume.collect（Twelve Data 优先），不经本模块。

裸符号（config 用 GSPC/IXIC/DJI）必须经 map_yf_symbol 转 ^ 前缀再调 yfinance，
直传裸符号会静默拿不到指数数据。
"""
import time
import pandas as pd
import yfinance as yf
from typing import List, Dict

# Twelve Data 风格裸符号 -> Yahoo 指数符号（^ 前缀）；ETF 符号不在表内，原样返回
TD_TO_YF = {
    "GSPC": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
    "NDX": "^NDX",
    "RUT": "^RUT",
}


def map_yf_symbol(symbol: str) -> str:
    """裸指数符号转 yfinance ^ 前缀符号；其余（ETF 等）原样返回。"""
    return TD_TO_YF.get(symbol, symbol)


def _fetch_history(yf_symbol: str, period: str):
    """yfinance 拉历史，限流重试 30s×2（与 price_volume 同模式）。失败返回 None。"""
    hist = None
    for attempt in range(3):
        try:
            hist = yf.Ticker(yf_symbol).history(period=period)
            break
        except Exception as e:
            if attempt < 2 and "RateLimit" in type(e).__name__:
                time.sleep(30)
                continue
            return None
    return hist


def collect_index(symbol: str, days: int = 60) -> List[Dict]:
    """获取指数日线量价，返回与 price_volume.collect 同构格式。

    Returns:
        List[{date, symbol, close, high, low, volume, vwap}]，升序（旧->新）。
        指数本体育 volume 为 0/None（资金流指标以代理 ETF 行为准）。
        VWAP 用典型价 (H+L+C)/3 近似。无数据返回 []。
    """
    hist = _fetch_history(map_yf_symbol(symbol), period=f"{days}d")
    if hist is None or hist.empty:
        return []

    result = []
    for date, row in hist.iterrows():
        close = None if pd.isna(row["Close"]) else float(row["Close"])
        high = None if pd.isna(row["High"]) else float(row["High"])
        low = None if pd.isna(row["Low"]) else float(row["Low"])
        volume = None if pd.isna(row["Volume"]) else int(row["Volume"])
        vwap = None
        if high is not None and low is not None and close is not None:
            vwap = (high + low + close) / 3
        result.append({
            "date": date.strftime("%Y-%m-%d"),
            "symbol": symbol,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "vwap": float(vwap) if vwap is not None else None,
        })
    return result


def fetch_full_history(symbol: str) -> pd.DataFrame:
    """拉指数全部可得历史（yfinance period="max" 一次取全史，无需分页）。

    建库用（25 年回测）。返回 DataFrame（date,close,high,low,volume 升序），
    无数据返回空 DataFrame。
    """
    hist = _fetch_history(map_yf_symbol(symbol), period="max")
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.reset_index()
    hist["date"] = hist["Date"].dt.strftime("%Y-%m-%d")
    return hist[["date", "Close", "High", "Low", "Volume"]].rename(
        columns={"Close": "close", "High": "high", "Low": "low", "Volume": "volume"}
    )
