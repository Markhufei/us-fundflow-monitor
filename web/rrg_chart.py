"""RRG (Relative Rotation Graph) 四象限散点图组件 (plotly)。

X 轴 = RS-Ratio（100 为基准，>100 跑赢大盘）
Y 轴 = RS-Momentum（0 为基准，>0 强度上升）
四象限:
  领先 (RS-Ratio>=100, RS-Momentum>=0)  - 资金持续流入
  改善 (RS-Ratio<100,  RS-Momentum>=0)  - 资金开始流入
  落后 (RS-Ratio<100,  RS-Momentum<0)   - 资金持续流出
  恶化 (RS-Ratio>=100, RS-Momentum<0)   - 资金开始流出
"""
from typing import List, Dict, Any, Optional

import plotly.graph_objects as go

QUADRANT_COLORS = {
    "领先": "#2ca02c",   # 绿
    "改善": "#1f77b4",   # 蓝
    "落后": "#d62728",   # 红
    "恶化": "#ff7f0e",   # 橙
    "未知": "#999999",
}


def quadrant(rs_ratio: Optional[float], rs_momentum: Optional[float]) -> str:
    """根据 RS-Ratio / RS-Momentum 判定象限。"""
    if rs_ratio is None or rs_momentum is None:
        return "未知"
    if rs_ratio >= 100 and rs_momentum >= 0:
        return "领先"
    if rs_ratio < 100 and rs_momentum >= 0:
        return "改善"
    if rs_ratio < 100 and rs_momentum < 0:
        return "落后"
    return "恶化"


def render_rrg(snapshots: List[Dict[str, Any]]) -> go.Figure:
    """渲染 RRG 四象限散点图。

    Args:
        snapshots: 各版块最新快照 [{symbol, sector, rs_ratio, rs_momentum, ...}]

    Returns:
        plotly Figure（streamlit 用 st.plotly_chart 渲染）
    """
    fig = go.Figure()

    # 四象限背景色块（半透明）
    fig.add_hrect(y0=0, y1=10, x0=100, x1=120,
                  fillcolor=QUADRANT_COLORS["领先"], opacity=0.06, layer="below")
    fig.add_hrect(y0=0, y1=10, x0=80, x1=100,
                  fillcolor=QUADRANT_COLORS["改善"], opacity=0.06, layer="below")
    fig.add_hrect(y0=-10, y1=0, x0=80, x1=100,
                  fillcolor=QUADRANT_COLORS["落后"], opacity=0.06, layer="below")
    fig.add_hrect(y0=-10, y1=0, x0=100, x1=120,
                  fillcolor=QUADRANT_COLORS["恶化"], opacity=0.06, layer="below")

    # 基准线
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.add_vline(x=100, line_dash="dash", line_color="gray")

    # 象限标签
    fig.add_annotation(x=110, y=9, text="领先", font=dict(color=QUADRANT_COLORS["领先"], size=14),
                       showarrow=False)
    fig.add_annotation(x=90, y=9, text="改善", font=dict(color=QUADRANT_COLORS["改善"], size=14),
                       showarrow=False)
    fig.add_annotation(x=90, y=-9, text="落后", font=dict(color=QUADRANT_COLORS["落后"], size=14),
                       showarrow=False)
    fig.add_annotation(x=110, y=-9, text="恶化", font=dict(color=QUADRANT_COLORS["恶化"], size=14),
                       showarrow=False)

    # 版块散点
    for s in snapshots:
        if s.get("symbol") == "SPY":
            continue
        rs = s.get("rs_ratio")
        rm = s.get("rs_momentum")
        if rs is None or rm is None:
            continue
        q = quadrant(rs, rm)
        fig.add_trace(go.Scatter(
            x=[rs], y=[rm], mode="markers+text",
            text=s.get("symbol", ""),
            textposition="top center",
            marker=dict(size=16, color=QUADRANT_COLORS[q], line=dict(width=1, color="white")),
            name=s.get("sector", ""),
            hovertext=(f"{s.get('sector')} ({s.get('symbol')})\n"
                       f"RS-Ratio={rs:.2f}\nRS-Momentum={rm:.2f}\n"
                       f"象限={q}\nMFI={s.get('mfi')}\nCMF={s.get('cmf')}"),
            hoverinfo="text",
        ))

    fig.update_layout(
        title="版块相对强度轮动图 (RRG)",
        xaxis_title="RS-Ratio（相对 SPY 强度，>100 跑赢大盘）",
        yaxis_title="RS-Momentum（强度动量，>0 上升）",
        showlegend=False,
        height=600,
        margin=dict(l=60, r=40, t=60, b=50),
    )
    return fig
