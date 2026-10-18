"""指数本体数据采集器（腾讯财经主源 + yfinance 备源）。

Twelve Data 免费档不支持指数（SPX/NDX/INX 需付费；2026-10-16 实测 IXIC/DJI/GSPC 404）。
Yahoo 2026-10 起对本机 IP 持续 429/403（IP 级限流），故主源改为腾讯财经真指数 OHLCV
（中国直连、无 key；2026-10-18 实测 DJI/INX/IXIC 可用，历史深度约 2008/2009 -> 今），
yfinance ^ 前缀降为备源。代理/杠杆 ETF（资金流指标来源）走现有
price_volume.collect（Twelve Data 优先），不经本模块。

腾讯 kline 端点注意（实测结论）：
- 单次 count 上限约 800，超出报 param error -> 分窗取数
- 指定 [start, end] 时返回窗口内**最近** count 根 -> 全史需从 end 向前翻页
- 连发请求会返回空体（限流），需请求间隔 + 空响应重试
- 返回列序为 date,open,close,high,low,volume（非 OHLC 惯例序），映射须按位取
"""
import datetime
import json
import time
import urllib.request
import pandas as pd
import yfinance as yf
from typing import List, Dict, Optional, Tuple

# Twelve Data 风格裸符号 -> Yahoo 指数符号（^ 前缀）；ETF 符号不在表内，原样返回
TD_TO_YF = {
    "GSPC": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
    "NDX": "^NDX",
    "RUT": "^RUT",
}

# 裸指数符号 -> 腾讯 kline 代码（2026-10-18 实测：响应键为 us.DJI / us.INX / us.IXIC）
TENCENT_MAP = {
    "GSPC": "usINX",
    "IXIC": "usIXIC",
    "DJI": "usDJI",
}

_TENCENT_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
    "?param={code},day,{start},{end},{count}"
)
_TENCENT_WINDOW = 750   # 实测上限约 800，留余量
_TENCENT_DELAY = 2.0    # 窗口间请求间隔（连发会空体限流）
_TENCENT_RETRY_DELAY = 10.0  # 空响应（限流）重试间隔，实测约 10s 自愈


def map_yf_symbol(symbol: str) -> str:
    """裸指数符号转 yfinance ^ 前缀符号；其余（ETF 等）原样返回。"""
    return TD_TO_YF.get(symbol, symbol)


def _tencent_fetch_window(code: str, start: str, end: str) -> Optional[List[List]]:
    """请求单个 kline 窗口，返回原始 day 数组；持续失败返回 None。

    空体/非 JSON 响应视为限流，重试 2 次（间隔 _TENCENT_RETRY_DELAY）。
    """
    url = _TENCENT_URL.format(code=code, start=start, end=end, count=_TENCENT_WINDOW)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                node = data.get(code.replace("us", "us."))
                if node is None:  # 部分响应键形如 us.INX
                    node = next((v for k, v in data.items() if isinstance(v, dict) and "day" in v), None)
                if node and node.get("day"):
                    return node["day"]
            return []  # 正常 JSON 但无数据（区间超出历史起点）
        except Exception:
            if attempt < 2:
                time.sleep(_TENCENT_RETRY_DELAY)
                continue
            return None


def fetch_tencent_index(symbol: str, start: Optional[str] = None,
                        end: Optional[str] = None) -> List[Dict]:
    """腾讯财经拉指数日线（真指数 OHLCV），从 end 向前翻页聚合至 start 或历史起点。

    Args:
        symbol: 裸指数符号（GSPC/IXIC/DJI，经 TENCENT_MAP 映射）
        start: 起始日期 YYYY-MM-DD（默认 1990-01-01，实际受腾讯历史深度限制）
        end: 结束日期 YYYY-MM-DD（默认今天）

    Returns:
        List[{date, open, close, high, low, volume}] 升序（旧->新）；
        列序按位映射：原始数组为 date,open,close,high,low,volume。
        持续失败返回 []。
    """
    code = TENCENT_MAP.get(symbol)
    if code is None:
        return []
    end = end or datetime.date.today().isoformat()
    start = start or "1990-01-01"

    rows: List[Dict] = []
    seen: set = set()
    cursor_end = end
    while cursor_end >= start:
        raw = _tencent_fetch_window(code, start, cursor_end)
        if raw is None:
            break  # 持续失败，返回已聚合部分
        if not raw:
            break  # 到历史起点
        batch = [
            {
                "date": r[0],
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": int(float(r[5])) if r[5] not in (None, "", "--") else None,
            }
            for r in raw if len(r) >= 6
        ]
        batch = [r for r in batch if r["date"] >= start and r["date"] <= end]
        if not batch:
            break
        new_rows = [r for r in batch if r["date"] not in seen]
        for r in new_rows:
            seen.add(r["date"])
        rows.extend(new_rows)
        first_date = min(r["date"] for r in batch)
        if first_date <= start:
            break  # 已覆盖到 start
        next_end = (datetime.date.fromisoformat(first_date)
                    - datetime.timedelta(days=1)).isoformat()
        if next_end >= cursor_end:
            break  # 游标未前进（如窗口重复返回），防死循环
        cursor_end = next_end
        time.sleep(_TENCENT_DELAY)

    rows.sort(key=lambda r: r["date"])
    return rows


def _tencent_rows_to_output(rows: List[Dict], symbol: str) -> List[Dict]:
    """腾讯行转 collect 输出结构（补 symbol/vwap，与 yfinance 通道同构）。"""
    result = []
    for r in rows:
        high, low, close = r["high"], r["low"], r["close"]
        vwap = (high + low + close) / 3 if None not in (high, low, close) else None
        result.append({
            "date": r["date"], "symbol": symbol,
            "close": close, "high": high, "low": low, "volume": r["volume"],
            "vwap": vwap,
        })
    return result


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


def collect_index_with_source(symbol: str, days: int = 60) -> Tuple[List[Dict], str]:
    """获取指数日线量价：腾讯主源 -> yfinance 备源。

    Returns:
        (rows, source): rows 与 price_volume.collect 同构
        （List[{date, symbol, close, high, low, volume, vwap}]，升序）；
        source 为 "tencent" / "yfinance" / ""（全失败，rows=[]）。
    """
    # 主源：腾讯（真指数，Yahoo 被 IP 封禁时仍可用）
    if symbol in TENCENT_MAP:
        start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        rows = fetch_tencent_index(symbol, start=start)
        if rows:
            return _tencent_rows_to_output(rows, symbol), "tencent"

    # 备源：yfinance
    hist = _fetch_history(map_yf_symbol(symbol), period=f"{days}d")
    if hist is None or hist.empty:
        return [], ""
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
    return result, "yfinance"


def collect_index(symbol: str, days: int = 60) -> List[Dict]:
    """获取指数日线量价（兼容旧签名，等价 collect_index_with_source 丢弃 source）。

    Returns:
        List[{date, symbol, close, high, low, volume, vwap}]，升序（旧->新）。
        无数据返回 []。
    """
    rows, _ = collect_index_with_source(symbol, days=days)
    return rows


def _tencent_full_history_df(symbol: str) -> Optional[pd.DataFrame]:
    """腾讯翻页拉全史转 DataFrame；持续失败返回 None（区分空数据）。"""
    rows = fetch_tencent_index(symbol)
    if not rows:
        return None
    return pd.DataFrame(rows)


def fetch_full_history_with_source(symbol: str) -> Tuple[pd.DataFrame, str]:
    """拉指数全部可得历史（建库用）：腾讯翻页全史 -> yfinance period="max"。

    Returns:
        (df, source): DataFrame（date,close,high,low,volume 升序），
        source 为 "tencent" / "yfinance" / ""（全失败，df 为空 DataFrame）。
        腾讯历史深度约 2008/2009 -> 今（实测），yfinance 全史更深但受 IP 封禁影响。
    """
    # 主源：腾讯
    if symbol in TENCENT_MAP:
        df = _tencent_full_history_df(symbol)
        if df is not None:
            return df[["date", "close", "high", "low", "volume"]], "tencent"

    # 备源：yfinance
    hist = _fetch_history(map_yf_symbol(symbol), period="max")
    if hist is None or hist.empty:
        return pd.DataFrame(), ""
    hist = hist.reset_index()
    hist["date"] = hist["Date"].dt.strftime("%Y-%m-%d")
    return (hist[["date", "Close", "High", "Low", "Volume"]]
            .rename(columns={"Close": "close", "High": "high",
                             "Low": "low", "Volume": "volume"}), "yfinance")


def fetch_full_history(symbol: str) -> pd.DataFrame:
    """拉指数全部可得历史（兼容旧签名，等价 fetch_full_history_with_source 丢弃 source）。

    建库用。返回 DataFrame（date,close,high,low,volume 升序），
    无数据返回空 DataFrame。
    """
    df, _ = fetch_full_history_with_source(symbol)
    return df
