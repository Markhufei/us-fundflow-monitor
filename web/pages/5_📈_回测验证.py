"""概率模型回测验证页。"""
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px

from web import common

common.init()
st.title("📈 回测验证（概率模型可靠性）")
st.caption("验证模型区分度：实际持续率应随 RS-Momentum 量级递增。样本<30 的结论仅供参考。")

n = common.count_events()
if n == 0:
    st.warning("事件库为空，无法回测")
    st.stop()

# 1. 各版块持续概率
st.subheader("各版块历史持续概率")
stats = common.history_stats_by_symbol()
fig1 = px.bar(stats, x="symbol", y="持续概率%", color="sector",
              text="事件数", title="各版块历史持续概率（柱上数字=样本量）")
fig1.update_layout(height=420, showlegend=False)
st.plotly_chart(fig1, width="stretch")
st.dataframe(stats, width="stretch", hide_index=True)

st.markdown("---")

# 2. RS-Momentum 量级 vs 实际持续率
st.subheader("RS-Momentum 量级 vs 实际持续率（全版块聚合）")
conn = sqlite3.connect(common.DB_PATH)
try:
    df = pd.read_sql(
        """SELECT symbol, rs_momentum, duration_days, ret_10d,
                  CASE WHEN duration_days >= 10 AND ret_10d >= 0.03 THEN 1 ELSE 0 END AS sustained
           FROM sector_history_events""",
        conn,
    )
finally:
    conn.close()

df["mom_bucket"] = pd.cut(df["rs_momentum"], bins=[-100, 0, 2, 5, 10, 100],
                          labels=["<0", "0-2", "2-5", "5-10", ">10"])
bucket = df.groupby("mom_bucket", observed=True).agg(
    样本数=("sustained", "count"),
    实际持续率=("sustained", "mean"),
    平均持续天数=("duration_days", "mean"),
    平均10日超额=("ret_10d", "mean"),
).reset_index()
bucket["实际持续率%"] = (bucket["实际持续率"] * 100).round(1)
bucket["平均10日超额%"] = (bucket["平均10日超额"] * 100).round(2)
bucket["平均持续天数"] = bucket["平均持续天数"].round(1)

fig2 = px.bar(bucket, x="mom_bucket", y="实际持续率%", text="样本数",
              title="RS-Momentum 量级 vs 实际持续率（柱上数字=样本量）")
fig2.update_layout(height=420, xaxis_title="RS-Momentum 量级", yaxis_title="实际持续率%")
st.plotly_chart(fig2, width="stretch")
st.dataframe(bucket, width="stretch", hide_index=True)

st.markdown("---")

# 3. 样本量分布
st.subheader("各版块样本量分布")
sym_count = df.groupby("symbol").size().reset_index(name="事件数")
fig3 = px.bar(sym_count, x="symbol", y="事件数", title="各版块历史事件样本量")
fig3.update_layout(height=350, showlegend=False)
st.plotly_chart(fig3, width="stretch")

st.info(
    "**解读**: 若「实际持续率」随 RS-Momentum 量级递增，说明模型有区分度。"
    "样本 <30 的版块/分桶结论仅供参考（标注低置信度）。"
)
