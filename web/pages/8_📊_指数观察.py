"""指数观察：三大指数回撤框架 + 5 类触发信号 + 条件概率查询 + 历史事件统计。"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from web import common
from src.schema import INDEX_TRIGGER_LABELS

common.init()
st.title("📊 指数观察")
st.caption("三大指数（GSPC/IXIC/DJI）回撤 + 资金流代理（SPY/QQQ/DIA）观察模块。"
           "主源腾讯财经真指数 OHLCV，事件库深度约 2008→今；"
           "概率为历史事件统计参考非预测；TQQQ/SQQQ 跟踪 NDX 而非 IXIC 综合指数（口径差异）；"
           "pullback_start 为预警命中率非可交易优势。")

snaps = common.load_index_snapshots()
if not snaps:
    st.warning("暂无指数快照。请运行: PYTHONPATH=. .venv/bin/python src/pipeline.py --mode index")
    st.stop()

# ---------- 当前阶段判定 ----------
from src.analyzers.index_stage import STAGE_EMOJI, STAGE_LABELS  # noqa: E402

st.subheader("📍 当前阶段判定")
st.caption("阶段=量化规则判定（close/MA/dd20，可回测复核）；历史同类阶段统计为描述性类比，"
           "上涨占比是历史频率非概率预测。")

with st.spinner("阶段判定计算中（首次约 1-2 分钟，结果缓存 30/60 分钟）"):
    for sym in common.INDEXES:
        try:
            concl = common.stage_conclusion_cached(sym)
        except Exception:
            concl = {"status": "insufficient"}
        if concl.get("status") != "ok" or not concl.get("stage"):
            st.write(f"**{sym}**: 阶段判定暂不可用（历史数据积累中）")
            continue
        stage = concl["stage"]
        st.markdown(
            f"{STAGE_EMOJI[stage]} **{sym} {common.INDEXES[sym]} — {STAGE_LABELS[stage]}**"
            f"（{concl['as_of']}）")
        st.caption(" ｜ ".join(concl["evidence"]))
        with st.expander(f"{sym} 历史同类阶段统计（类比参考非预测）"):
            frame = common.stage_stats_frame(sym, current_stage=stage)
            if frame is not None:
                st.dataframe(frame, width="stretch", hide_index=True)
            else:
                st.write("历史统计计算中或数据不足")

# ---------- 最新快照表 ----------
st.subheader(f"最新指标（截至 {common.latest_snapshot_date(snaps)}）")
idx_df = pd.DataFrame([s for s in snaps if s.get("kind") == "index"])
if not idx_df.empty:
    idx_df["名称"] = idx_df["symbol"].map(common.INDEXES).fillna(idx_df["symbol"])
    show = ["symbol", "名称", "date", "close", "ret_1d", "dd20", "dd50",
            "ma20", "ma50", "above_ma200", "rs_ratio", "rs_momentum",
            "cmf", "mfi", "vol_zscore", "data_source"]
    st.dataframe(idx_df[[c for c in show if c in idx_df.columns]].round(4),
                 width="stretch", hide_index=True)

other_df = pd.DataFrame([s for s in snaps if s.get("kind") != "index"])
if not other_df.empty:
    with st.expander("代理 / 杠杆 ETF 行情（资金流指标取自代理行）"):
        other_df["名称"] = other_df["name"].fillna(other_df["symbol"])
        st.dataframe(other_df[["symbol", "名称", "kind", "date", "close", "volume"]]
                     .rename(columns={"kind": "类型"}), width="stretch", hide_index=True)

st.markdown("---")

# ---------- 回撤时间线 ----------
st.subheader("回撤时间线")
sym = st.selectbox("选择指数", list(common.INDEXES.keys()),
                   format_func=lambda s: f"{s} ({common.INDEXES[s]})")
hist = common.load_index_history(sym, days=250)
if hist:
    hdf = pd.DataFrame(hist)
    events = common.load_index_events(symbol=sym)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.65, 0.35],
                        vertical_spacing=0.03, subplot_titles=("收盘 + MA20/MA50", "20日回撤 dd20"))
    fig.add_trace(go.Scatter(x=hdf["date"], y=hdf["close"], name="收盘",
                             line=dict(color="#1f77b4", width=1.5)), row=1, col=1)
    if "ma20" in hdf.columns:
        fig.add_trace(go.Scatter(x=hdf["date"], y=hdf["ma20"], name="MA20",
                                 line=dict(color="#ff7f0e", width=1)), row=1, col=1)
    if "ma50" in hdf.columns:
        fig.add_trace(go.Scatter(x=hdf["date"], y=hdf["ma50"], name="MA50",
                                 line=dict(color="#2ca02c", width=1)), row=1, col=1)
    dd = pd.to_numeric(hdf.get("dd20"), errors="coerce")
    fig.add_trace(go.Scatter(x=hdf["date"], y=dd, name="dd20", fill="tozeroy",
                             line=dict(color="#d62728", width=1)), row=2, col=1)
    if not events.empty:
        ev_plot = events.copy()
        ev_plot["y"] = ev_plot["event_date"].map(
            dict(zip(hdf["date"], dd)))
        for tt, grp in ev_plot.groupby("trigger_type"):
            fig.add_trace(go.Scatter(
                x=grp["event_date"], y=grp["y"], mode="markers",
                name=INDEX_TRIGGER_LABELS.get(tt, tt),
                marker=dict(size=9, symbol="triangle-down"),
                customdata=grp["trigger_type"],
                hovertemplate="%{x}<br>" + tt + "<extra></extra>"), row=2, col=1)
    fig.add_hline(y=-0.05, line_dash="dash", line_color="#d62728",
                  annotation_text="回调阈值 -5%", row=2, col=1)
    fig.update_layout(height=560, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08))
    fig.update_yaxes(tickformat=".1%", row=2, col=1)
    st.plotly_chart(fig, width="stretch")
    st.caption(f"数据截至: {hist[-1].get('date', '未知')}；散点为该指数历史事件（建库回测产出）")
else:
    st.info(f"无 {sym} 历史快照")

st.markdown("---")

# ---------- 近 30 日信号 ----------
st.subheader("近 30 日信号")
alerts = common.load_index_alerts(days=30)
if alerts:
    adf = pd.DataFrame(alerts)
    adf["指数"] = adf["symbol"].map(common.INDEXES).fillna(adf["symbol"])
    adf["触发类型"] = adf["rule"].str.replace("index_", "", regex=False)\
        .map(INDEX_TRIGGER_LABELS).fillna(adf["rule"])
    st.dataframe(adf[["date", "指数", "触发类型", "severity", "message", "value"]]
                 .rename(columns={"severity": "级别", "value": "概率", "message": "明细"}),
                 width="stretch", hide_index=True)
else:
    st.info("近 30 日无指数信号（index_hist_days=250 历史未积累前无信号属设计内行为）")

st.markdown("---")

# ---------- 条件概率查询 ----------
st.subheader("条件概率查询")
st.caption("查历史事件库中同指数+同触发类型的条件概率（Wilson 95% CI 小样本必看；"
           "pullback_start 的概率为预警命中率）。低样本时结果仅作参考。")
c1, c2, c3 = st.columns(3)
q_sym = c1.selectbox("指数", list(common.INDEXES.keys()),
                     format_func=lambda s: common.INDEXES[s], key="prob_sym")
q_tt = c2.selectbox("触发类型", list(INDEX_TRIGGER_LABELS.keys()),
                    format_func=lambda t: INDEX_TRIGGER_LABELS[t], key="prob_tt")
q_regime = c3.selectbox("SPY 状态（regime 分桶）",
                        [(None, "不分桶"), (1, "MA200 上方"), (0, "MA200 下方")],
                        format_func=lambda x: x[1], key="prob_regime")
regime_val = q_regime[0]

prob = common.query_index_probability(q_sym, q_tt, spy_above_ma200=regime_val)
p = prob.get("probability")
if p is None:
    st.warning("无历史样本（事件库未建或该类型 0 事件）。请先运行建库脚本: "
               "PYTHONPATH=. .venv/bin/python scripts/build_index_event_library.py")
else:
    from src.analyzers.sector_stats import wilson_ci
    lo, hi = wilson_ci(p, prob["sample_size"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("概率", f"{p*100:.0f}%",
              help="历史相似事件中结果成立的比例（口径按触发类型，见页面顶部说明）")
    m2.metric("Wilson 95% CI", f"{lo*100:.0f}%~{hi*100:.0f}%",
              help="小样本下点估计精度虚高，置信区间必看")
    m3.metric("样本量", prob["sample_size"])
    m4.metric("平均持续", f"{prob['avg_duration']:.0f}天" if prob.get("avg_duration") is not None else "N/A")
    if prob.get("avg_amplitude") is not None:
        st.caption(f"平均 20 日收益: {prob['avg_amplitude']:+.2f}%；"
                   f"置信度: {prob['confidence']}"
                   f"{'；regime 分桶匹配' if prob.get('regime_used') else '（全样本，regime 内样本不足或未启用）'}")
    if prob.get("confidence") == "低":
        st.warning("⚠️ 样本不足（低于最小样本阈值），以上概率仅作参考，非可交易优势。")

st.markdown("---")

# ---------- 历史事件统计表 ----------
st.subheader("历史事件统计")
n_events = common.count_index_events()
if n_events == 0:
    st.info("事件库为空。先运行: PYTHONPATH=. .venv/bin/python scripts/build_index_event_library.py")
else:
    stats = common.index_event_stats()
    st.dataframe(stats, width="stretch", hide_index=True)
    st.caption(f"共 {n_events} 条事件。命中率口径按触发类型不同（pullback_start=预警命中率，"
               "capital_withdrawal=下跌延续，其余=反弹/上涨延续），见 query_index_probability。")
