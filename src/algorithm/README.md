# Python 算法服务

该组件是报警管理系统的纯计算服务。版本化的 `POST /api/v2/analyze` 使用 `hybrid-v2` 对规范化报警执行数学化专家时序规则、一阶 Markov 关联和可弃权原因分类，返回逐记录标签、解释及关联事件链。

服务不连接 PostgreSQL、不保留跨请求状态、不改变业务数据，也不把统计关联描述为已确认根因。完整数学定义和限制见 `docs/algorithm/model-v2.md`。

## 环境

- Python 3.12
- 默认监听 `127.0.0.1:8001`
- 健康端点：`GET /health`
- 分析端点：`POST /api/v2/analyze`

依赖版本以 `requirements.lock` 为准。

## 安装与测试

Windows：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pytest
```

WSL/Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
```

## 启动

```powershell
.venv\Scripts\python.exe -m algorithm_service
```

```bash
.venv/bin/python -m algorithm_service
```

健康响应固定为：

```json
{
  "status": "UP",
  "service": "algorithm-service",
  "version": "0.2.0",
  "contract_version": "v2"
}
```

## v2 请求契约

请求顶层字段固定为 `analysis_run_id`、`contract_version`、`algorithm_version`、`parameters` 和 `records`：

- `contract_version` 必须为 `v2`；
- `algorithm_version` 必须为 `0.2.0`；
- 一次请求只能包含一个批次，记录 ID 不得重复；
- 所有时间必须携带时区；
- 未知字段、缺失参数和越界参数返回 HTTP 422。

完整参数为：

```json
{
  "duplicate_window_seconds": 30,
  "chatter_window_seconds": 60,
  "chatter_min_count": 4,
  "chatter_min_transition_ratio": 0.8,
  "short_lived_seconds": 10,
  "persistent_requires_ack": true,
  "episode_gap_seconds": 60,
  "chain_window_seconds": 60,
  "chain_min_steps": 5,
  "min_episode_support": 3,
  "min_transition_probability": 0.6,
  "min_lift": 2.0,
  "expert_min_score": 0.35,
  "expert_min_margin": 0.1
}
```

Python 会校验参数并在响应中原样回传。参数由调用方显式提供，没有隐藏默认值。

## 时序规则和规则强度

记录先按时间、源行和 UUID 稳定排序，业务分组使用 `site/area/unit/tag`。

- 重复：同组且核心字段完全相同的相邻非同时记录在窗口内命中，`score = exp(-时间差/重复窗口)`。
- 抖动：将 `RETURNED` 映射为 0，将 `ACTIVE` 和 `ACKNOWLEDGED` 映射为 1；滑动窗口中记录数和状态转换比均达到门槛时命中，`score = 状态转换次数/(记录数-1)`。
- 短时恢复：存在真实恢复时间且持续时间不超过阈值时命中，`score = exp(-持续时间/短时阈值)`。
- 持续：P1、ACTIVE、未恢复且满足确认策略时命中，规则强度为 1。
- 正常：未命中上述规则时规则强度为 1，含义是通过当前规则集，不是正常状态概率。

主要类型优先级保持：

```text
DUPLICATE > CHATTER > SHORT_LIVED > PERSISTENT > NORMAL
```

其他同时命中的规则及其强度保留在 `evidence`。`score` 不是概率、准确率或安全风险值。

## 一阶 Markov 关联

模型按 `site/area/unit` 和 `episode_gap_seconds` 划分 episode，折叠连续相同 tag 后，仅统计相邻 tag 转移。对边 `u -> v`：

```text
P(v|u) = C_uv / C_u
P(v) = C_.v / C
lift(u,v) = P(v|u) / P(v)
```

只有不同 episode 支持数、`P(v|u)`、lift 和中位延迟同时达到显式门槛时，边才可参与链提取。同一关系范围和实际 episode 内至少达到 `chain_min_steps` 个成员才输出事件链。

解释包含逐边 `C_uv`、episode 支持、`P(v|u)`、`P(v)`、lift、中位延迟和门槛。事件链只表示重复片段中的统计关联；链首是候选起始报警，不是已确认根因。

## 可弃权原因分类

原因分类使用冻结的非负专家特征和余弦分数：

```text
S_c(x) = (w_c dot x) / (||w_c|| * ||x||)
```

只有最高分达到 `expert_min_score`，且与次高分的差达到 `expert_min_margin` 时才输出原因类别；否则返回 `UNKNOWN`。维护特征会否决其他原因类别，避免将明确维护活动误写为设备或工艺故障。

每条原因解释包含各类别分数、最高分、次高分、margin 和主要贡献特征。当前响应中的 `score` 仍是主要时序规则强度，原因分类分数只写入解释，二者不得混用。

## 确定性与复杂度

- 请求数组乱序、UUID 重映射和整体时间平移不改变规范化业务结果；
- 算法不读取样例预期、场景名称、固定源行或数据库；
- 分组排序为 `O(n log n)`，窗口扫描和相邻转移为 `O(n)`，专家分类为有界特征下的 `O(nF)`；
- 不构造所有记录对或所有 tag 对的二次矩阵。

## 配置

| 环境变量 | 默认值 | 约束 |
| --- | --- | --- |
| `ALGORITHM_HOST` | `127.0.0.1` | 非空；容器运行时显式设置为监听地址 |
| `ALGORITHM_PORT` | `8001` | 1–65535 的整数 |

配置在启动 Uvicorn 前校验。错误配置会向标准错误输出中文原因并以退出码 2 结束。

## 限制

- 当前没有独立真实工业标注集，不声明分类准确率、行业合规性或自动根因能力。
- 罕见 tag 或支持不足的真实事件会保守地不生成关联链。
- 缺少 P&ID、过程连续变量和资产拓扑时，Markov 先后关系不能解释为物理因果。
- 原因专家特征未覆盖的新词汇或冲突证据会返回 `UNKNOWN`。
- 服务只做同步纯计算；业务状态、结果持久化、人工复核和审计由 Java 主系统负责。
