"""RRG (Relative Rotation Graph) 版块相对强度图组件 (plotly)。

支持两种形态:
  - render_rrg: 最新散点图（单日快照）
  - render_rrg_trail: 轨迹图（最近 N 天点连成线，看版块移动趋势）

X 轴 = RS-Ratio（100 为基准，>100 跑赢大盘）
Y 轴 = RS-Momentum（0 为基准，>0 强度上升）
四象限:
  领先 (RS-Ratio>=100, RS-Momentum>=0)  - 资金持续流入
  改善 (RS-Ratio<100,  RS-Momentum>=0)  - 资金开始流入
  落后 (RS-Ratio<100,  RS-Momentum<0)   - 资金持续流出
  恶化 (RS-Ratio>=100, RS-Momentum<0)   - 资金开始流出

轮动典型路径: 落后 -> 改善 -> 领先 -> 恶化 -> 落后（顺时针循环）
"""
from typing import List, Dict, Any, Optional

import plotly.graph_objects as go
from plotly.subplots import make_subplots

QUADRANT_COLORS = {
    "领先": "#2ca02c",   # 绿
    "改善": "#1f77b4",   # 蓝
    "落后": "#d62728",   # 红
    "恶化": "#ff7f0e",   # 橙
    "未知": "#999999",
}

# 11 版块固定色（轨迹追踪用，避免每日变色难辨认）
SECTOR_COLORS = {
    "XLK": "#1f77b4", "XLE": "#ff7f0e", "XLF": "#2ca02c", "XLV": "#d62728",
    "XLY": "#9467bd", "XLP": "#8c564b", "XLI": "#e377c2", "XLB": "#7f7f7f",
    "XLRE": "#bcbd22", "XLU": "#17becf", "XLC": "#393b79", "UFO": "#ff9896",
    # 监控个股（Set2 色系，与版块 tab10 区分）
    "SKHY": "#66c2a5", "MU": "#fc8d62", "SNDK": "#8da0cb", "AAPL": "#e78ac3",
    "SPCX": "#a6d854",
    # 美股七姐妹（Dark2 色系，2026-08-29 新增）
    "MSFT": "#1b9e77", "NVDA": "#d95f02", "GOOGL": "#7570b3",
    "META": "#e7298a", "AMZN": "#66a61e", "TSLA": "#e6ab02",
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


def _add_quadrant_bg(fig: go.Figure):
    """添加四象限背景色块 + 基准线 + 象限标签（公共）。"""
    fig.add_hrect(y0=0, y1=10, x0=100, x1=120,
                  fillcolor=QUADRANT_COLORS["领先"], opacity=0.06, layer="below")
    fig.add_hrect(y0=0, y1=10, x0=80, x1=100,
                  fillcolor=QUADRANT_COLORS["改善"], opacity=0.06, layer="below")
    fig.add_hrect(y0=-10, y1=0, x0=80, x1=100,
                  fillcolor=QUADRANT_COLORS["落后"], opacity=0.06, layer="below")
    fig.add_hrect(y0=-10, y1=0, x0=100, x1=120,
                  fillcolor=QUADRANT_COLORS["恶化"], opacity=0.06, layer="below")
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.add_vline(x=100, line_dash="dash", line_color="gray")
    fig.add_annotation(x=110, y=9, text="领先", font=dict(color=QUADRANT_COLORS["领先"], size=14),
                       showarrow=False)
    fig.add_annotation(x=90, y=9, text="改善", font=dict(color=QUADRANT_COLORS["改善"], size=14),
                       showarrow=False)
    fig.add_annotation(x=90, y=-9, text="落后", font=dict(color=QUADRANT_COLORS["落后"], size=14),
                       showarrow=False)
    fig.add_annotation(x=110, y=-9, text="恶化", font=dict(color=QUADRANT_COLORS["恶化"], size=14),
                       showarrow=False)


def render_rrg(snapshots: List[Dict[str, Any]]) -> go.Figure:
    """渲染 RRG 最新散点图（单日快照）。"""
    fig = go.Figure()
    _add_quadrant_bg(fig)
    for s in snapshots:
        if s.get("symbol") == "SPY":
            continue
        rs, rm = s.get("rs_ratio"), s.get("rs_momentum")
        if rs is None or rm is None:
            continue
        q = quadrant(rs, rm)
        fig.add_trace(go.Scatter(
            x=[rs], y=[rm], mode="markers+text",
            text=s.get("symbol", ""), textposition="top center",
            marker=dict(size=16, color=QUADRANT_COLORS[q], line=dict(width=1, color="white")),
            name=s.get("sector", ""),
            hovertext=(f"{s.get('sector')} ({s.get('symbol')})\n"
                       f"RS-Ratio={rs:.2f}\nRS-Momentum={rm:.2f}\n"
                       f"象限={q}\nMFI={s.get('mfi')}\nCMF={s.get('cmf')}"),
            hoverinfo="text", showlegend=False,
        ))
    fig.update_layout(
        title="版块相对强度轮动图 (RRG)",
        xaxis_title="RS-Ratio（>100 跑赢大盘）",
        yaxis_title="RS-Momentum（>0 上升）",
        showlegend=False, height=600, margin=dict(l=60, r=40, t=60, b=50),
    )
    return fig


def render_rrg_trail(histories: Dict[str, List[Dict[str, Any]]], trail_days: int = 20):
    """渲染 RRG 轨迹图：每版块最近 N 天的 RS 点连成线。

    Args:
        histories: {symbol: [snapshot,...]} 升序（旧->新），每条含 rs_ratio/rs_momentum/date
        trail_days: 轨迹显示天数

    Returns:
        (go.Figure, dropped: List[str])；dropped = 因 RS 有效点 <2 被过滤的标的
        （多为采集失败/限流），调用方应展示该清单，避免标的"静默消失"。

    每版块一条彩色线:
      - 线: 连接 N 天的 (RS-Ratio, RS-Momentum) 点，半透明
      - 末端: 实心大圆点 + 版块代号标签（当前状态）
      - 起点: 空心小圆点（N 天前起点，显示移动方向）

    视觉读图: 顺时针轨迹 = 标准轮动（落后->改善->领先->恶化）；
    轨迹向领先象限延伸 = 资金持续流入；从领先向恶化拐头 = 资金开始撤离。
    """
    fig = go.Figure()
    _add_quadrant_bg(fig)
    dropped: List[str] = []

    for symbol, hist in histories.items():
        if symbol == "SPY":
            continue
        # 过滤无效 RS + 截取最近 trail_days 天
        valid = [h for h in hist
                 if h.get("rs_ratio") is not None and h.get("rs_momentum") is not None]
        if len(valid) < 2:
            dropped.append(symbol)
            continue
        valid = valid[-trail_days:]
        xs = [h["rs_ratio"] for h in valid]
        ys = [h["rs_momentum"] for h in valid]
        dates = [h.get("date", "") for h in valid]
        color = SECTOR_COLORS.get(symbol, "#999999")

        # 轨迹线
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            line=dict(color=color, width=2.5),
            opacity=0.6,
            name=symbol,
            hovertext=[f"{symbol} · {h.get('date', '')}<br>"
                       f"RS-Ratio={h['rs_ratio']:.2f}<br>RS-Momentum={h['rs_momentum']:.2f}<br>"
                       f"象限={quadrant(h['rs_ratio'], h['rs_momentum'])}<br>"
                       f"MFI={h.get('mfi')}<br>CMF={h.get('cmf')}<br>vol_zscore={h.get('vol_zscore')}"
                       for h in valid],
            hoverinfo="text", showlegend=False,
        ))
        # 起点空心小圆（方向起点）
        fig.add_trace(go.Scatter(
            x=[xs[0]], y=[ys[0]], mode="markers",
            marker=dict(size=9, color="white", line=dict(width=2, color=color)),
            hovertext=f"{symbol} 起点 · {dates[0]}",
            hoverinfo="text", showlegend=False,
        ))
        # 末端实心大点 + 代号
        q = quadrant(xs[-1], ys[-1])
        fig.add_trace(go.Scatter(
            x=[xs[-1]], y=[ys[-1]], mode="markers+text",
            text=[symbol], textposition="top center",
            marker=dict(size=16, color=color, line=dict(width=2, color="white")),
            hovertext=(f"{symbol} 当前 · {dates[-1]}<br>"
                       f"RS-Ratio={xs[-1]:.2f}<br>RS-Momentum={ys[-1]:.2f}<br>象限={q}<br>"
                       f"MFI={valid[-1].get('mfi')}<br>CMF={valid[-1].get('cmf')}<br>"
                       f"vol_zscore={valid[-1].get('vol_zscore')}"),
            hoverinfo="text", showlegend=False,
        ))

    fig.update_layout(
        title=f"版块相对强度轮动轨迹图（最近 {trail_days} 天）",
        xaxis_title="RS-Ratio（>100 跑赢大盘）",
        yaxis_title="RS-Momentum（>0 强度上升）",
        showlegend=False, height=650, margin=dict(l=60, r=40, t=60, b=50),
        hovermode="closest",
    )
    return fig, dropped


def render_flow_indicators(hdf) -> Optional[go.Figure]:
    """渲染资金流指标子图：MFI 曲线 / CMF 柱状 / vol_zscore 柱状（三行共享 x 轴）。

    Args:
        hdf: DataFrame，需含 date 列 + mfi/cmf/vol_zscore 列

    Returns:
        Figure；指标列缺失或全空时返回 None（调用方自行提示）
    """
    flow_cols = ["mfi", "cmf", "vol_zscore"]
    if not all(c in hdf.columns for c in flow_cols):
        return None
    if not hdf[flow_cols].notna().any().any():
        return None
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.10,
        row_heights=[0.4, 0.3, 0.3],
        subplot_titles=("MFI（>80 超买 / <20 超卖）",
                        "CMF（>0 资金流入 / <0 流出）",
                        "vol_zscore（|z|≥2 异常放量）"),
    )
    fig.add_trace(go.Scatter(x=hdf["date"], y=hdf["mfi"], name="MFI",
                             line=dict(color="#1f77b4")), row=1, col=1)
    fig.add_hline(y=80, line_dash="dot", line_color="#d62728", row=1, col=1)
    fig.add_hline(y=20, line_dash="dot", line_color="#2ca02c", row=1, col=1)
    cmf_colors = ["#2ca02c" if v >= 0 else "#d62728" for v in hdf["cmf"].fillna(0)]
    fig.add_trace(go.Bar(x=hdf["date"], y=hdf["cmf"], name="CMF",
                         marker_color=cmf_colors), row=2, col=1)
    fig.add_hline(y=0, line_color="gray", row=2, col=1)
    vz_colors = ["#ff7f0e" if abs(v) >= 2 else "#7f7f7f" for v in hdf["vol_zscore"].fillna(0)]
    fig.add_trace(go.Bar(x=hdf["date"], y=hdf["vol_zscore"], name="vol_zscore",
                         marker_color=vz_colors), row=3, col=1)
    fig.add_hline(y=2, line_dash="dot", line_color="#d62728", row=3, col=1)
    fig.add_hline(y=-2, line_dash="dot", line_color="#d62728", row=3, col=1)
    fig.update_layout(height=560, showlegend=False, hovermode="x unified")
    return fig
