"""个股轮动监控：SKHY / MU / SNDK / SPCX + 美股七姐妹 的 RS vs SPY + 资金流指标。"""
import streamlit as st
import pandas as pd

from web import common
from web.rrg_chart import render_rrg_trail, render_flow_indicators, quadrant

common.init()
st.title("🎯 个股监控")
st.caption("RS-Ratio 基准为 SPY（与版块同公式同基准，可直接对比轮动位置）。"
           "注意：持续概率库为版块级 20 年回测，个股历史不足（SKHY/SNDK 上市不满 2 年），不提供个股概率。")

snaps = common.load_stock_snapshots()
if not snaps:
    st.warning("暂无个股快照。请到总览页点「🔄 立即扫描版块」或等每日 06:15 cron 扫描。")
    st.stop()

# 最新个股快照表（各标的最新日可能不齐，标题取全表 max，行内 date 列供对照）
st.subheader(f"最新指标（截至 {common.latest_snapshot_date(snaps)}）")
sdf = pd.DataFrame(snaps)
sdf["名称"] = sdf["symbol"].map(common.STOCKS)
sdf["象限"] = [quadrant(r, m) for r, m in zip(sdf["rs_ratio"], sdf["rs_momentum"])]
show = ["symbol", "名称", "date", "close", "rs_ratio", "rs_momentum", "象限",
        "mfi", "cmf", "vol_zscore"]
st.dataframe(sdf[[c for c in show if c in sdf.columns]].round(2),
             width="stretch", hide_index=True)

st.markdown("---")

# 个股 RRG 轨迹图（与版块同基准）
st.subheader("个股 RRG 轨迹图")
trail_days = st.slider("轨迹天数", min_value=5, max_value=60, value=20, step=5)
stock_hist = common.load_all_stock_histories(days=60)
valid, dropped = common.filter_valid_histories(stock_hist)
if len(valid) >= 2:
    fig, _ = render_rrg_trail(stock_hist, trail_days=trail_days)
    st.plotly_chart(fig, width="stretch")
    dropped_msg = common.format_dropped(dropped)
    if dropped_msg:
        st.caption(dropped_msg)
else:
    st.info(f"轨迹图需每只个股 ≥2 天有效 RS 数据（当前有效 {len(valid)} 只），请等扫描积累")

st.markdown("---")

# 单个股 RS 走势 + 资金流
sym = st.selectbox("选择个股查看走势", list(common.STOCKS.keys()),
                   format_func=lambda s: f"{s} ({common.STOCKS[s]})")
hist = common.load_stock_history(sym, days=120)
if hist:
    hdf = pd.DataFrame(hist)
    st.subheader(f"{sym} RS 指标走势（近120天）")
    st.plotly_chart(common.render_rs_chart(hdf), width="stretch")
    st.caption(f"数据截至: {hist[-1].get('date', '未知')}")

    st.subheader(f"{sym} 资金流指标（近120天）")
    flow_fig = render_flow_indicators(hdf)
    if flow_fig is not None:
        st.plotly_chart(flow_fig, width="stretch")
    else:
        st.info("该股无资金流指标数据（MFI/CMF/vol_zscore 均为空）")
else:
    st.info(f"无 {sym} 历史快照")
