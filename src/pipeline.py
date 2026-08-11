"""主流程管道：采集 -> 分析 -> 存储 -> 通知。"""

import argparse
import logging
import pandas as pd
import numpy as np
from datetime import datetime
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
    """判断是否为交易日（简化版：周一到周五）。"""
    weekday = datetime.now().weekday()  # 0=周一, 4=周五
    return weekday < 5


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


def run(config_path: str = "config/watchlist.yaml", mode: str = "daily") -> None:
    """
    运行主流程。

    Args:
        config_path: 配置文件路径
        mode: 运行模式: daily / compare2d / weekly
    """
    logger.info(f"启动 pipeline，模式={mode}")

    # 1. 读配置
    config = load_config(config_path)
    db_path = "data/fundflow.db"

    # 2. 初始化数据库
    from src.storage import store
    store.init_db(db_path)

    # 版块轮动模式：独立流程（不复用 watchlist 单标的逻辑）
    if mode == "sector":
        _run_sector(config, db_path)
        return

    # 3. 非交易日跳过采集
    if not is_trading_day():
        logger.info("非交易日，跳过数据采集")
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

        # ========== 采集阶段 ==========
        # 采集价格/成交量历史
        pv_history = price_volume.collect(symbol, days=60, fallback_symbol=fallback_symbol)

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

        # ========== 存储 alerts 并发送通知 ==========
        from src.alerts import notify

        created_at = datetime.now().isoformat()
        alerts_to_store = []
        for alert in alerts:
            alert_with_ts = alert.copy()
            alert_with_ts["created_at"] = created_at
            alerts_to_store.append(alert_with_ts)
            notify.send(alert, config)
            logger.info(f"alert: {alert['symbol']} {alert['rule']} - {alert['message']}")

        if alerts_to_store:
            store.store_alerts(alerts_to_store, db_path)

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
                comp = compare.compare(snapshot, hist_snap, label)
                logger.info(f"{symbol} {label}: {comp}")

    logger.info("pipeline 完成")


def _run_sector(config: Dict[str, Any], db_path: str) -> None:
    """版块轮动扫描: 拉 11 SPDR ETF + SPY -> 算 RS 指标 -> 存快照 -> 检测轮动 -> 告警。

    独立于单标的 daily 流程，复用 price_volume 采集 + sector_rotation 指标 +
    sector_detector 轮动检测 + notify 通知。
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
    spy_hist = price_volume.collect(benchmark, days=hist_days)
    if not spy_hist:
        logger.warning(f"{benchmark} 数据获取失败，跳过版块扫描")
        return False

    # 存 SPY 快照（基准，RS 指标为 None）
    store.store_sector_snapshot({
        "date": spy_hist[-1]["date"], "symbol": benchmark, "sector": None,
        "close": spy_hist[-1].get("close"), "volume": spy_hist[-1].get("volume"),
    }, db_path)

    # 拉各版块 ETF + 算指标 + 存快照
    snapshots = []
    for etf in etfs:
        sym = etf["symbol"]
        sector_name = etf["sector"]
        hist = price_volume.collect(sym, days=hist_days)
        if not hist:
            logger.warning(f"{sym} 无数据，跳过")
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

    # 检测轮动：需前一交易日快照（首次运行无 prev，不触发）
    prev_snapshots = []
    for snap in snapshots:
        hist = store.load_sector_history(snap["symbol"], days=3, db_path=db_path)
        if len(hist) >= 2:
            prev_snapshots.append(hist[-2])  # 前一交易日

    alerts = sector_detector.detect_rotation(snapshots, prev_snapshots, db_path, thresholds)

    if alerts:
        created_at = datetime.now().isoformat()
        for a in alerts:
            a["created_at"] = created_at
        store.store_rotation_events(alerts, db_path)
        for a in alerts:
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
    else:
        logger.info("版块轮动: 无信号")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="美股资金流动监控")
    parser.add_argument("--config", default="config/watchlist.yaml", help="配置文件路径")
    parser.add_argument("--mode", default="daily", choices=["daily", "compare2d", "weekly", "sector"], help="运行模式")
    args = parser.parse_args()
    run(args.config, args.mode)
