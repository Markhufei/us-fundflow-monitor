"""RRG 四象限轮动轨迹图详情页。"""
import streamlit as st

from web import common
from web.rrg_chart import render_rrg_trail

common.init()
st.title("🔄 版块相对强度轮动图 (RRG)")
st.caption("X 轴=RS-Ratio（>100 跑赢大盘） | Y 轴=RS-Momentum（>0 强度上升） | 每条线=版块 N 天轨迹")

# 轨迹天数选择器
trail_days = st.slider(
    "轨迹天数", min_value=5, max_value=60, value=20, step=5,
    help="每版块显示最近 N 天的 RS 点连成线。线末端=当前，空心圆=起点。"
)

# 加载所有版块历史
histories = common.load_all_sector_histories(days=trail_days)
valid_hist, dropped = common.filter_valid_histories(histories)

if not valid_hist:
    st.warning("暂无 RS 历史数据。请先运行多次 `--mode sector`（或点总览页扫描按钮）积累快照。")
    st.stop()

fig, _ = render_rrg_trail(histories, trail_days=trail_days)
st.plotly_chart(fig, width="stretch")
dropped_msg = common.format_dropped(dropped)
if dropped_msg:
    st.caption(dropped_msg)

st.caption(
    "📊 读图：线末端实心点=当前状态，空心圆=N 天前起点。"
    "顺时针轨迹（落后→改善→领先→恶化）= 标准轮动周期。"
    "线向「领先」延伸=资金持续流入；从「领先」拐头向「恶化」=资金开始撤离。"
)

st.markdown("---")
st.subheader("版块指标明细（最新一日）")

# 最新一日快照
snapshots = common.load_snapshots()
rs_list = common.render_sector_table(snapshots)
if rs_list:
    st.caption(f"数据日期: {common.latest_snapshot_date(rs_list)}")

with st.expander("📖 RRG 四象限说明"):
    st.markdown("""
- **领先** (绿): 跑赢大盘且强度上升，资金持续流入
- **改善** (蓝): 跑输但强度上升，资金开始流入
- **落后** (红): 跑输且强度下降，资金持续流出
- **恶化** (橙): 跑赢但强度下降，资金开始流出

轮动典型路径: 落后 -> 改善 -> 领先 -> 恶化 -> 落后（顺时针循环）

**轨迹图用法**: 调整上方滑块改变轨迹天数。短线（5-10 天）看近期异动拐头；
长线（30-60 天）看完整轮动周期。版块代号标在线末端（当前点）。
""")
