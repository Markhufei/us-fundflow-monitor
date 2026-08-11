"""RRG 四象限轮动图详情页。"""
import streamlit as st
import pandas as pd

from web import common
from web.rrg_chart import render_rrg, quadrant

common.init()
st.title("🔄 版块相对强度轮动图 (RRG)")
st.caption("X 轴=RS-Ratio（>100 跑赢大盘） | Y 轴=RS-Momentum（>0 强度上升）")

snapshots = common.load_snapshots()
has_rs = [s for s in snapshots if s.get("rs_ratio") is not None and s.get("symbol") != "SPY"]

if not has_rs:
    st.warning("暂无 RS 数据。请先运行: `PYTHONPATH=. .venv/bin/python src/pipeline.py --mode sector`")
    st.stop()

fig = render_rrg(has_rs)
st.plotly_chart(fig, width="stretch")

st.subheader("版块指标明细")
df = pd.DataFrame(has_rs)
df["象限"] = df.apply(lambda r: quadrant(r.get("rs_ratio"), r.get("rs_momentum")), axis=1)
show = ["date", "symbol", "sector", "象限", "close", "volume",
        "rs_ratio", "rs_momentum", "mfi", "cmf", "vol_zscore"]
st.dataframe(df[[c for c in show if c in df.columns]], width="stretch", hide_index=True)
st.caption(f"数据日期: {has_rs[0].get('date')}")

with st.expander("📖 RRG 四象限说明"):
    st.markdown("""
- **领先** (绿): 跑赢大盘且强度上升，资金持续流入
- **改善** (蓝): 跑输但强度上升，资金开始流入
- **落后** (红): 跑输且强度下降，资金持续流出
- **恶化** (橙): 跑赢但强度下降，资金开始流出

轮动典型路径: 落后 -> 改善 -> 领先 -> 恶化 -> 落后（顺时针循环）
""")
