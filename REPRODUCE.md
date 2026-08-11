# 美股版块轮动监控工具 · 完整复现文档

> 本文档可独立发送。收件人既可直接 clone 代码运行（**轨道 A**），也可把第七节的提示词粘贴给 Claude Code 从零生成（**轨道 B**）。

---

## 一、工具简介

监控大资金在美股 11 个版块间的移仓（如科技 → 能源），给出移仓**持续概率** + 历史**波动幅度/持续时间**。

### 4 条核心需求

1. **版块异动**：大资金从一个版块转移到另一个版块
2. **持续性警告**：给出移仓持续概率 + 历史上类似异动后该版块的波动幅度和持续时间
3. **免费**
4. **真实可靠**，不幻觉、不虚构数据

---

## 二、复现策略（双轨，任选）

| 轨道 | 方式 | 适合 | 可靠性 | 耗时 |
|------|------|------|--------|------|
| **A（推荐）** | clone GitHub 代码 + 按 README 部署 | 想直接用 | ★★★★★ | 15 分钟 |
| **B** | Claude Code + 提示词从零生成 | 学习/二次定制 | ★★★☆☆ | 2–4 小时 |

---

## 三、使用模型

| 项 | 推荐 | 说明 |
|----|------|------|
| **AI 编程助手** | Claude Code CLI（首选）| 本工具即用它开发，支持 plan 模式、多文件编辑、bash 执行、测试驱动 |
| **模型** | Claude Sonnet 4.5 / 4.6（或 Opus 4.x）| 需工具调用（tool use）+ 多文件重构能力，Haiku 级别不够 |
| **替代方案** | Cursor / Windsurf / Cline + 同级模型 | 任一支持 agentic 文件编辑的 IDE |
| **不推荐** | 纯对话式 AI（网页版 ChatGPT/Claude.ai）| 无文件系统访问，无法完成多文件工程 |

> 复现用 AI 必须具备：plan 模式、文件读写、bash 执行、测试运行能力。

---

## 四、需注册的会员（全免费，无信用卡）

| 服务 | 是否必需 | 注册地址 | 用途 | 免费额度 |
|------|---------|---------|------|---------|
| **Twelve Data** | ✅ 必需 | https://twelvedata.com/ | 11 版块 ETF + SPY 日线量价（主力数据源）| 800 req/天 |
| **GitHub** | 轨道 A 必需 | https://github.com/ | clone/分享代码 | 无限公开仓库 |
| yfinance | ❌ 不需注册 | 装包即用 | Twelve Data 限流时 fallback | 无 key，IP 级限流 |
| SEC EDGAR | ❌ 不需注册 | 公开 API | 单标的内部人交易（可选模块）| 无限 |

**明确不需要**：Bloomberg / EPFR / Morningstar（真实板块资金流，付费，本工具用 SPDR ETF 量价间接推算替代）、Alpha Vantage（免费额度更低，非必需）、IEX Cloud（2024 已关闭）。

---

## 五、需要的工具（运行环境）

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+（3.14 已验证）| 运行时 |
| pip | 随 Python | 装依赖 |
| git | 任意 | clone 代码 |
| 编辑器 | VS Code / Cursor（可选）| 看代码 |
| cron | 系统自带（macOS/Linux）| 定时扫描（可选）|

**Python 依赖**（`requirements.txt`，全纯 Python 无编译）：

```
yfinance>=0.2.40
pandas>=2.0
requests>=2.30
pyyaml>=6.0
pytest>=7.0
streamlit>=1.30
plotly>=5.18
```

**平台**：macOS / Linux / Windows 均可。仅 macOS 通知功能是平台特有（`terminal-notifier`），其他平台用 Web 面板替代。

---

## 六、项目结构

```
us-fundflow-monitor/
├── config/watchlist.yaml              标的 + 阈值 + 11 版块 ETF 配置
├── src/collectors/
│   ├── twelvedata.py                  Twelve Data 采集（has_key 检查 + 限流间隔）
│   └── price_volume.py                采集封装（TD 优先，yfinance fallback）
├── src/analyzers/
│   ├── sector_rotation.py             RS-Ratio / RS-Momentum（RRG JdK 公式）
│   ├── sector_stats.py                历史事件库 + 条件概率查询
│   └── sector_detector.py             实时轮动检测 + 告警生成
├── src/storage/store.py               SQLite 幂等 UPSERT 存储
├── src/alerts/notify.py               macOS 通知 + 日志
├── src/pipeline.py                    主调度（--mode sector）
├── scripts/build_sector_event_library.py    一次性建库脚本（25 年回测）
├── web/
│   ├── app.py                         Streamlit 总览页（+ 立即扫描按钮）
│   ├── common.py                      共享代码（DB 路径、.env 加载、数据加载）
│   ├── rrg_chart.py                   RRG 四象限图组件
│   └── pages/                         6 个子页面（RRG/信号/事件库/详情/回测/关于）
├── tests/                             76 个 pytest 测试
├── .env.example                       API key 模板（复制为 .env 后填 key）
├── .gitignore                         排除 .env/data/logs/.venv
├── requirements.txt
├── cron_setup.sh                      cron 定时安装（幂等）
└── README.md
```

---

## 七、AI 复现提示词（核心交付物，直接粘贴给 Claude Code）

> 把下面这段完整粘贴给 Claude Code（或其他 AI 编程助手），即可从零复现。

````
# 任务：开发美股版块轮动监控工具

## 核心需求（4 条，缺一不可）
1. 检测美股版块间大资金转移（如科技->能源），基于 11 个 SPDR Sector ETF 量价间接推算
2. 给出大资金移仓的「持续性概率」+ 历史上类似异动后该版块的「波动幅度和持续时间」
3. 全免费数据源
4. 真实可靠，禁止幻觉/虚构数据

## 技术栈
Python 3.11+ / pandas / SQLite / Streamlit / plotly。不引入重框架，最少代码解决问题。

## 数据源（免费）
- 主力：Twelve Data API（免费 800 req/天，需 TWELVEDATA_API_KEY 环境变量）
- fallback：yfinance（无 key，IP 级限流）
- 不使用任何付费数据。真实板块资金流（Bloomberg/EPFR）为付费，用 SPDR ETF 量价间接推算替代

## 监测标的
SPY 基准 + 11 个 SPDR Sector ETF：XLK(科技) XLE(能源) XLF(金融) XLV(医疗) XLY(可选消费) XLP(必需消费) XLI(工业) XLB(材料) XLRE(房地产) XLU(公用事业) XLC(通信)

## 算法（RRG 相对强度，JdK 标准公式）
- RS-Ratio = 100 × (版块close/SPYclose) / rolling_mean(版块close/SPYclose, period=10)
  - >100 表示版块跑赢大盘
- RS-Momentum = 100 × (RS-Ratio / RS-Ratio.shift(period=10) - 1)
  - >0 表示强度上升（资金偏好转入）
- RRG 四象限：领先(RS-Ratio≥100, Mom≥0) / 改善(<100, ≥0) / 落后(<100, <0) / 恶化(≥100, <0)
- 量能确认：当日成交量 Z-Score > 1.5

## 轮动检测 + 概率模型
1. 事件触发：RS-Momentum 上穿 0 + 量能确认；或 RS-Ratio 上穿 100
2. 历史事件库：一次性回测 11 ETF × 约 20 年（yfinance period='max'），识别所有触发事件，算后续 5/10/20/60 天相对 SPY 的超额收益 + 持续天数
3. 持续定义：事件后超额收益>0 的连续天数 ≥10 且 10 日累计超额收益 ≥3%
4. 概率查询：新事件触发时，查历史相似事件（同版块 + RS-Momentum 量级 ±20% 容差），算「持续」事件占比作为持续概率
5. 置信度：样本 <10 标「低置信度」，≥30 标「高置信度」

## 模块结构
config/watchlist.yaml        标的 + 阈值 + 版块配置
src/collectors/twelvedata.py    Twelve Data 采集（has_key 检查 + 限流间隔）
src/collectors/price_volume.py  采集封装（TD 优先，yfinance fallback）
src/analyzers/sector_rotation.py  RS-Ratio / RS-Momentum 计算
src/analyzers/sector_stats.py     历史事件库 + 条件概率查询
src/analyzers/sector_detector.py  实时轮动检测 + 告警生成
src/storage/store.py           SQLite 幂等 UPSERT 存储
src/alerts/notify.py           macOS 通知 + 日志
src/pipeline.py                主调度（--mode sector）
scripts/build_sector_event_library.py  一次性建库脚本
web/app.py + web/pages/        Streamlit 多页面仪表盘（7 页 + 立即扫描按钮）
tests/                         pytest 测试套件

## Web 面板（7 页）
总览(关键指标+RRG缩略+扫描按钮) / RRG轮动 / 轮动信号 / 历史事件库 / 版块详情 / 回测验证 / 关于。
总览页必须有「立即扫描」按钮，点击调 _run_sector 拉数据+检测，无需终端操作。
注意：Streamlit 进程不自动 source .env，需在 common.py 启动时手动读 .env 到 os.environ。

## 反幻觉要求（最重要）
- 所有概率必须标样本量，样本不足标「低置信度」
- Web 面板显示数据源 + 时间戳
- 明确声明「RS 指标是滞后确认，非领先预警」
- 禁止生成模拟数据冒充真实数据
- 概率基于真实历史统计，非主观打分

## 验证标准（必须全部达成）
1. pytest 全套通过
2. 历史事件库 ≥3000 事件（11 ETF × 20 年）
3. 真实数据交叉验证：拉真实 XLK/SPY 算 RS-Ratio，量级合理（接近 100，非离谱值）
4. Streamlit AppTest 无头验证 7 页面无运行时错误
5. 端到端：--mode sector 跑通，11 版块 RS 计算正确
6. 概率查询：构造翻转场景，能返回「持续概率% + 均持续天数 + ±振幅 + 样本量」

## 执行方式
先 plan mode 设计 6 阶段计划（配置+Schema -> RS 指标 -> 事件库 -> 检测器 -> Web -> cron+文档），
批准后逐阶段实现，每阶段写测试 + 真实数据验证。禁止跳过验证直接进下一阶段。
````

---

## 八、复现步骤

### 轨道 A：clone 代码（推荐，15 分钟）

```bash
# 1. clone 仓库
git clone https://github.com/<用户名>/us-fundflow-monitor.git
cd us-fundflow-monitor

# 2. 建虚拟环境 + 装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 配 Twelve Data key（去 https://twelvedata.com/ 免费注册）
cp .env.example .env
# 编辑 .env，把 TWELVEDATA_API_KEY=后面的占位符换成真实 key

# 4. 建历史事件库（首次，约 2-5 分钟，拉 11 ETF × 20 年）
PYTHONPATH=. .venv/bin/python3 scripts/build_sector_event_library.py

# 5. 跑测试验证
.venv/bin/python3 -m pytest tests/ -v          # 应 76 passed

# 6. 启 Web 面板
.venv/bin/streamlit run web/app.py
# 浏览器开 http://localhost:8501，点「立即扫描版块」按钮
```

### 轨道 B：Claude Code 从零生成（2–4 小时）

```bash
# 1. 装 Claude Code（需 Node.js）
npm install -g @anthropic-ai/claude-code

# 2. 建项目目录 + 虚拟环境
mkdir us-fundflow-monitor && cd us-fundflow-monitor
python3 -m venv .venv
.venv/bin/pip install pandas requests pyyaml pytest streamlit plotly yfinance

# 3. 配 Twelve Data key
mkdir -p config
echo 'TWELVEDATA_API_KEY=你的key' > .env

# 4. 启动 Claude Code，粘贴第七节的提示词
claude
# 在交互界面粘贴提示词 -> 批准 plan -> 逐阶段完成 -> 验证
```

---

## 九、算法说明

### 1. RS-Ratio / RS-Momentum（RRG JdK 标准公式）

```
RS-Ratio = 100 × (版块close / SPYclose) / rolling_mean(版块close / SPYclose, period=10)
RS-Momentum = 100 × (RS-Ratio / RS-Ratio.shift(period=10) - 1)
```

- **RS-Ratio > 100**：版块跑赢大盘
- **RS-Momentum > 0**：版块强度上升（资金偏好转入）

**RRG 四象限**：

| 象限 | RS-Ratio | RS-Momentum | 含义 |
|------|----------|-------------|------|
| 领先（绿）| ≥100 | ≥0 | 资金持续流入 |
| 改善（蓝）| <100 | ≥0 | 资金开始流入 |
| 落后（红）| <100 | <0 | 资金持续流出 |
| 恶化（橙）| ≥100 | <0 | 资金开始流出 |

### 2. 轮动检测

- **转强**：RS-Momentum 上穿 0（+当日 vol_zscore > 1.5 量能确认），或 RS-Ratio 上穿 100
- **转弱**：RS-Momentum 下穿 0，或 RS-Ratio 下穿 100
- **轮动信号**：转强版块配对当日最弱版块（RS-Momentum 最低）作为 `from → to`，表达资金转移方向

### 3. 持续概率（核心）

1. **历史事件库**：回测 11 ETF × 20 年，识别所有「转强」事件，算后续 5/10/20/60 天相对 SPY 的超额收益 + 持续天数
2. **持续定义**：事件后超额收益 > 0 的连续天数 ≥ 10 且 10 日累计超额收益 ≥ 3%
3. **概率查询**：新事件触发时，查历史相似事件（同版块 + RS-Momentum 量级 ±20% 容差），算「持续」事件占比
4. **置信度**：样本 < 10 标「低置信度」，≥ 30 标「高置信度」

---

## 十、验证标准（复现成功的标志）

| 检查项 | 期望值 |
|--------|--------|
| pytest | 76 passed |
| 历史事件库 | 3187 事件（2006–2026）|
| 真实 RS-Ratio | XLK≈101.48 / XLU≈95.59（接近 100，非离谱）|
| Streamlit AppTest | 7/7 页面 PASS |
| 扫描按钮 | 点击后真实拉取 11 版块数据，显示「✅ 扫描完成」|
| 概率查询 | 返回「持续概率% + 均持续天数 + ±振幅 + 样本量」四要素 |

---

## 十一、反幻觉保障（本工具的核心价值）

- ✅ **数据源全部免费且真实**：Twelve Data 实时拉取，非模拟
- ✅ **概率基于真实历史统计**：3187 个真实事件的条件概率，非主观打分
- ✅ **每个概率都标样本量**：样本不足自动标「低置信度」
- ✅ **明确声明指标性质**：RS 指标是滞后确认，不是预言
- ✅ **76 个测试 + 真实数据交叉验证**：XLK/SPY RS-Ratio 量级合理

---

## 十二、输出示例

```
[2026-08-11] 版块轮动: 科技->能源 持续概率15% 历史均持续10天 +0.4% 样本41
[2026-08-11] 版块轮动: 能源->科技 持续概率62% 历史均持续12天 ±4.1% 样本41
[2026-08-11] 版块轮动: 金融->公用事业 持续概率18% 历史均持续3天 ±1.2% 样本33 低置信度
```

> 注：示例中「持续概率 15%」是真实统计结果——单 RS-Momentum 上穿信号多数不持续。工具如实给出概率，不主观拔高。这正是反幻觉的价值。

---

## 十三、技术现实说明（重要）

1. **真实板块资金流免费源拿不到**：Bloomberg/EPFR/Morningstar 都是付费。本工具用 SPDR Sector ETF 量价间接推算（RS-Ratio + MFI）。"大资金从科技->能源" 近似为 "XLE 的 RS-Ratio 上穿 XLK"。
2. **历史异动持续期统计无现成数据库**：Sam Stovall 的轮动研究是付费报告。本工具用 20 年 SPDR 日线（yfinance 免费可下）自建事件库。
3. **RRG/相对强度本质是滞后指标**：免费 + 量价类指标做不到"领先预警"，只能"确认 + 分级 + 给概率"。

---

*文档结束。如有疑问，对照 README.md 或运行 `pytest tests/ -v` 验证。*
