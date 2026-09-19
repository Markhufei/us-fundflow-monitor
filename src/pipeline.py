"""主流程管道：采集 -> 分析 -> 存储 -> 通知。"""

import argparse
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List

# yaml 是可选依赖
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config/watchlist.yaml") -> Dict[str, Any]:
    """加载配置文件。"""
    if not HAS_YAML:
        raise ImportError("pyyaml 未安装，请运行: pip install pyyaml")
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_trading_day() -> bool:
    """判断是否为采集日（简化版：周一到周六）。

    周六也放行: 北京周六 06:00 = 美股周五 18:00 EDT，周五收盘数据已完整；
    旧逻辑 weekday < 5 导致美股周五收盘系统性延迟到周一才入库。
    """
    weekday = datetime.now().weekday()  # 0=周一, 5=周六, 6=周日
    return weekday < 6


def _hist_to_df(hist: List[Dict[str, Any]]) -> pd.DataFrame:
    """将历史数据列表转为 DataFrame。"""
    df = pd.DataFrame(hist)
    # 确保有 OHLC
    if "close" not in df.columns and len(df) > 0:
        return pd.DataFrame()
    if "high" not in df.columns:
        df["high"] = df["close"] * 1.01
    if "low" not in df.columns:
        df["low"] = df["close"] * 0.99
    return df


def _is_partial_day(hist: List[Dict[str, Any]]) -> bool:
    """最新 bar 成交量 < 前 20 根均量 30% 判为盘中不完整数据（防半日快照污染入库）。"""
    if len(hist) < 6:
        return False
    vol = hist[-1].get("volume")
    if not vol:
        return False
    prior = [h.get("volume") or 0 for h in hist[-21:-1]]
    mean = sum(prior) / len(prior)
    return bool(mean > 0 and vol < mean * 0.3)


def _drop_partial_tail(hist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """丢弃尾部疑似盘中不完整 bar（美东盘中手动刷新拉到的半日量），返回裁剪后的列表。

    该日快照由盘后 cron/launchd 全量数据 UPSERT 补上（幂等）。
    """
    while hist and _is_partial_day(hist):
        dropped = hist.pop()
        logger.warning(f"丢弃疑似盘中不完整 bar {dropped.get('symbol', '?')} "
                       f"{dropped['date']} volume={dropped.get('volume')}")
    return hist


def _trading_days_between(start_date: str, end_date: str) -> int:
    """两日期间的交易日数（周一~周五口径，含 end 不含 start）；解析失败返回 -1。"""
    try:
        start = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
        end = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return -1
    n = 0
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def _expected_data_date() -> str:
    """预期最新数据日（美东视角）：北京时间 -12h 的日期，周末回退至周五。"""
    d = (datetime.now() - timedelta(hours=12)).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _check_freshness(db_path: str, config: Dict[str, Any]) -> None:
    """数据新鲜度告警：快照 MAX(date) 落后预期交易日 >1 个工作日则发通知（调度可能停摆）。"""
    import sqlite3

    from src.alerts import notify

    d = datetime.strptime(_expected_data_date(), "%Y-%m-%d").date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    threshold = d.isoformat()

    for table in ("daily_snapshot", "sector_snapshot"):
        conn = sqlite3.connect(db_path)
        row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
        conn.close()
        max_date = row[0] if row and row[0] else ""
        if max_date < threshold:
            notify.send({
                "date": _expected_data_date(), "symbol": "SYSTEM", "rule": "data_stale",
                "severity": "HIGH",
                "message": (f"数据 stale: {table} 最新 {max_date or '空'} "
                            f"落后预期 {_expected_data_date()} 超 1 个交易日，调度可能停摆"),
            }, config)
            logger.warning(f"freshness: {table} MAX(date)={max_date} < threshold {threshold}")


_LOG_ROTATE_FILES = ("cron_daily.log", "cron_sector.log", "cron_compare2d.log",
                     "cron_weekly.log", "fundflow.log")
_MAX_LOG_BYTES = 5 * 1024 * 1024


def _rotate_logs(max_bytes: int = _MAX_LOG_BYTES) -> None:
    """日志超 5MB 轮转一次（保留 .old 一份）。streamlit.log 由 launchd 持有 fd，不轮转。"""
    import os

    for name in _LOG_ROTATE_FILES:
        path = os.path.join("logs", name)
        try:
            if os.path.exists(path) and os.path.getsize(path) > max_bytes:
                os.replace(path, path + ".old")
                logger.info(f"日志轮转: {path} -> {path}.old")
        except OSError as e:
            logger.warning(f"日志轮转失败 {path}: {e}")


def run(config_path: str = "config/watchlist.yaml", mode: str = "daily") -> None:
    """
    运行主流程。

    Args:
        config_path: 配置文件路径
        mode: 运行模式: daily / compare2d / weekly / sector
    """
    logger.info(f"启动 pipeline，模式={mode}")

    # 0. 日志轮转（超 5MB 归档为 .old）
    _rotate_logs()

    # 1. 读配置
    config = load_config(config_path)
    db_path = "data/fundflow.db"

    # 2. 初始化数据库
    from src.storage import store
    store.init_db(db_path)

    # 3. 非交易日跳过采集（含 sector：周末白跑烧 API 配额）
    if not is_trading_day():
        logger.info("非交易日，跳过数据采集")
        return

    # 版块轮动模式：独立流程（不复用 watchlist 单标的逻辑）
    if mode == "sector":
        ok, skipped = _run_sector(config, db_path)
        logger.info(f"sector 完成 ok={ok} skipped={skipped}")
        _check_freshness(db_path, config)
        return

    # 4. 遍历 watchlist
    watchlist = config.get("watchlist", {})
    stocks = watchlist.get("stocks", [])
    etfs = watchlist.get("etfs", [])
    all_symbols = stocks + etfs

    thresholds = config.get("thresholds", {})
    periods = config.get("periods", {})

    # 导入 collectors
    from src.collectors import price_volume, sec_edgar, finra_short

    for item in all_symbols:
        symbol = item["symbol"]
        fallback_symbol = item.get("fallback_symbol")
        logger.info(f"处理 {symbol}...")

        # per-symbol 隔离：单个标的异常（如 NaN 转换、网络抖动）不拖垮整轮采集
        try:
            # ========== 采集阶段 ==========
            # 采集价格/成交量历史
            pv_history = price_volume.collect(symbol, days=60, fallback_symbol=fallback_symbol)

            # 盘中不完整数据守卫：丢弃尾部半日 bar（盘后 cron 全量数据 UPSERT 补上）
            pv_history = _drop_partial_tail(pv_history)
            if not pv_history:
                logger.warning(f"{symbol} 无价格数据，跳过")
                continue

            # 最新快照
            latest_pv = pv_history[-1]
            snapshot = {
                "date": latest_pv["date"],
                "symbol": symbol,
                "close": latest_pv.get("close"),
                "volume": latest_pv.get("volume"),
                "vwap": latest_pv.get("vwap"),
            }

            # 采集其他数据（13F 机构持仓由 sec_edgar.collect_inst 处理，SEC 13F 数据集）
            snapshot.update(sec_edgar.collect_inst(symbol))
            snapshot.update(sec_edgar.collect_insider(symbol))
            snapshot.update(finra_short.collect(symbol))

            # ========== 计算指标阶段 ==========
            from src.analyzers import flow_indicators

            # 转为 DataFrame 用于计算指标
            hist_df = pd.DataFrame(pv_history)
            # 确保有 high/low
            if "high" not in hist_df.columns:
                hist_df["high"] = hist_df["close"] * 1.01
            if "low" not in hist_df.columns:
                hist_df["low"] = hist_df["close"] * 0.99

            # 计算指标
            indicators = flow_indicators.calc(hist_df, periods)
            snapshot.update(indicators)

            # ========== 存储阶段 ==========
            store.store_snapshot(snapshot, db_path)
            logger.info(f"已存储 {symbol} snapshot")

            # ========== 异动检测 ==========
            from src.analyzers import anomaly_detector
            alerts = anomaly_detector.detect(snapshot, thresholds)

            # ========== 存储 alerts，仅对新插入的告警发通知（compare2d/节假日重跑不重复通知）==========
            from src.alerts import notify

            created_at = datetime.now().isoformat()
            alerts_to_store = []
            for alert in alerts:
                alert_with_ts = alert.copy()
                alert_with_ts["created_at"] = created_at
                alerts_to_store.append(alert_with_ts)

            new_alerts = store.store_alerts(alerts_to_store, db_path)
            for alert in new_alerts:
                notify.send(alert, config)
                logger.info(f"alert: {alert['symbol']} {alert['rule']} - {alert['message']}")

            # ========== 对比模式（compare2d / weekly）==========
            if mode in ["compare2d", "weekly"]:
                from src.analyzers import compare
                compare_days = periods.get("compare_short_days", 2) if mode == "compare2d" \
                    else periods.get("compare_week_days", 5)
                # 加载历史快照
                hist_list = store.load_history(symbol, days=compare_days + 5, db_path=db_path)
                if len(hist_list) > compare_days:
                    hist_snap = hist_list[-compare_days - 1]
                    label = "2日对比" if mode == "compare2d" else "周对比"
                    # 缺口校验：快照条数≠交易日数，缺口过大时"2日对比"实际跨多天
                    gap = _trading_days_between(hist_snap["date"], snapshot["date"])
                    if gap < 0:
                        logger.warning(f"{symbol} {label}跳过: 日期无法解析 "
                                       f"{hist_snap['date']} -> {snapshot['date']}")
                    elif gap > compare_days + 2:
                        logger.warning(f"{symbol} {label}跳过: 基准 {hist_snap['date']} 与当前 "
                                       f"{snapshot['date']} 实际间隔 {gap} 个交易日（预期约 {compare_days}），数据缺口过大")
                    else:
                        if gap != compare_days:
                            label = f"{label}(实际间隔{gap}个交易日)"
                        comp = compare.compare(snapshot, hist_snap, label)
                        logger.info(f"{symbol} {label}: {comp}")
        except Exception as e:
            logger.warning(f"{symbol} 处理失败，跳过: {e}", exc_info=True)
            continue

    _check_freshness(db_path, config)
    logger.info("pipeline 完成")


def _run_sector(config: Dict[str, Any], db_path: str) -> "tuple[bool, List[str]]":
    """版块轮动扫描: 拉 11 SPDR ETF + SPY -> 算 RS 指标 -> 存快照 -> 检测轮动 -> 告警。

    独立于单标的 daily 流程，复用 price_volume 采集 + sector_rotation 指标 +
    sector_detector 轮动检测 + notify 通知。

    Returns:
        (ok, skipped): ok=基准 SPY 是否成功；skipped=无数据/数据不足被跳过的 symbol 清单
    """
    from src.collectors import price_volume
    from src.analyzers import sector_rotation, sector_detector
    from src.alerts import notify
    from src.storage import store

    sector_config = config.get("sector_etfs", {})
    benchmark = sector_config.get("benchmark", "SPY")
    etfs = sector_config.get("etfs", [])
    thresholds = config.get("sector_thresholds", {})
    periods = config.get("sector_periods", {})
    hist_days = periods.get("sector_hist_days", 60)

    # 拉基准 SPY
    spy_hist = _drop_partial_tail(price_volume.collect(benchmark, days=hist_days))
    if not spy_hist:
        logger.warning(f"{benchmark} 数据获取失败，跳过版块扫描")
        return False, []

    # 存 SPY 快照（基准，RS 指标为 None）
    store.store_sector_snapshot({
        "date": spy_hist[-1]["date"], "symbol": benchmark, "sector": None,
        "close": spy_hist[-1].get("close"), "volume": spy_hist[-1].get("volume"),
    }, db_path)

    # 拉各版块 ETF + 算指标 + 存快照
    skipped: List[str] = []
    snapshots = []
    for etf in etfs:
        sym = etf["symbol"]
        sector_name = etf["sector"]
        hist = _drop_partial_tail(price_volume.collect(sym, days=hist_days))
        if not hist:
            logger.warning(f"{sym} 无数据，跳过")
            skipped.append(sym)
            continue
        indicators = sector_rotation.calc_sector(hist, spy_hist, periods)
        snap = {
            "date": hist[-1]["date"], "symbol": sym, "sector": sector_name,
            "close": hist[-1].get("close"), "volume": hist[-1].get("volume"),
            **indicators,
        }
        store.store_sector_snapshot(snap, db_path)
        snapshots.append(snap)
        logger.info(f"已存储 {sym} snapshot rs_ratio={snap.get('rs_ratio')}")

    # 个股轮动监控（RS vs SPY 同基准，与版块同公式；不参与版块轮动检测）
    for stk in config.get("sector_stocks", []):
        sym = stk["symbol"]
        hist = _drop_partial_tail(price_volume.collect(sym, days=hist_days))
        if not hist:
            logger.warning(f"个股 {sym} 无数据，跳过")
            skipped.append(sym)
            continue
        indicators = sector_rotation.calc_sector(hist, spy_hist, periods)
        snap = {
            "date": hist[-1]["date"], "symbol": sym,
            "name": stk.get("name"), "sector_symbol": stk.get("sector_symbol"),
            "close": hist[-1].get("close"), "volume": hist[-1].get("volume"),
            **indicators,
        }
        store.store_stock_snapshot(snap, db_path)
        logger.info(f"已存储个股 {sym} snapshot rs_ratio={snap.get('rs_ratio')}")

    # 检测轮动：需前一交易日快照（首次运行无 prev，不触发）
    # 交易日历用 SPY 快照日期序列近似；prev 与当日非相邻交易日（有缺口）时对比失真，跳过
    spy_dates = [s["date"] for s in store.load_sector_history(benchmark, days=hist_days, db_path=db_path)]
    prev_snapshots = []
    for snap in snapshots:
        hist = store.load_sector_history(snap["symbol"], days=3, db_path=db_path)
        if len(hist) < 2:
            continue
        prev = hist[-2]  # 前一交易日
        try:
            idx = spy_dates.index(snap["date"])
        except ValueError:
            logger.warning(f"{snap['symbol']} 轮动检测跳过: 当日 {snap['date']} 不在 {benchmark} 交易日历中")
            continue
        if idx == 0 or spy_dates[idx - 1] != prev["date"]:
            logger.warning(f"{snap['symbol']} 轮动检测跳过: prev {prev['date']} 与当日 "
                           f"{snap['date']} 非相邻交易日，存在数据缺口")
            continue
        prev_snapshots.append(prev)

    alerts = sector_detector.detect_rotation(snapshots, prev_snapshots, db_path, thresholds)

    if alerts:
        created_at = datetime.now().isoformat()
        for a in alerts:
            a["created_at"] = created_at
        # 仅对新插入的轮动事件发通知（手动刷新重跑不重复打扰）
        existing = {(e["date"], e["from_sector"], e["to_sector"])
                    for e in store.get_rotation_events(days=7, db_path=db_path)}
        new_events = [a for a in alerts
                      if (a["date"], a["from_sector"], a["to_sector"]) not in existing]
        store.store_rotation_events(alerts, db_path)
        for a in new_events:
            # 构造 notify 兼容的 alert dict（复用现有通知通道）
            notify.send({
                "date": a["date"], "symbol": a["to_symbol"],
                "rule": "sector_rotation", "severity": a["severity"],
                "message": a["message"], "value": a.get("probability"),
                "detail": (f"from={a['from_sector']} to={a['to_sector']} "
                           f"duration={a.get('avg_duration')} amplitude={a.get('avg_amplitude')} "
                           f"sample={a.get('sample_size')} confidence={a.get('confidence')}"),
            }, config)
            logger.info(f"sector alert: {a['message']}")
        if not new_events:
            logger.info(f"sector alert: {len(alerts)} 条均为已存在事件，未重复通知")
    else:
        logger.info("版块轮动: 无信号")

    return True, skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="美股资金流动监控")
    parser.add_argument("--config", default="config/watchlist.yaml", help="配置文件路径")
    parser.add_argument("--mode", default="daily", choices=["daily", "compare2d", "weekly", "sector"], help="运行模式")
    args = parser.parse_args()
    run(args.config, args.mode)
