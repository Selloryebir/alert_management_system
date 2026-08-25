# Python 算法服务

该组件是报警管理系统灾后重建 Demo 的纯计算服务。M3 提供版本化的 `POST /api/v1/analyze`，根据请求中的规范化报警和显式参数返回逐记录标签、原因类别建议及关联事件链。服务不连接 PostgreSQL、不改变业务状态，也不把规则建议描述为已确认根因。

## 环境

- Python 3.12
- 默认监听 `127.0.0.1:8001`
- 健康端点：`GET /health`
- 分析端点：`POST /api/v1/analyze`

依赖版本以 `requirements.lock` 为准。该锁文件同时包含最小运行依赖和健康检查测试依赖，减少阶段骨架中的依赖入口数量。

## 安装与测试

在本目录执行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pytest
```

WSL/Linux 使用后续统一提供的 Python 3.12 环境：

```bash
python3.12 -m pip install -r requirements.lock
python3.12 -m pytest
```

## 启动

```powershell
.venv\Scripts\python.exe -m algorithm_service
```

```bash
python3.12 -m algorithm_service
```

启动成功后，`GET http://127.0.0.1:8001/health` 返回：

```json
{
  "status": "UP",
  "service": "algorithm-service",
  "version": "0.1.0",
  "contract_version": "v1"
}
```

## 分析契约与规则

请求顶层字段固定为 `analysis_run_id`、`contract_version`、`algorithm_version`、`parameters` 和 `records`。`contract_version` 必须为 `v1`，算法版本为 `0.1.0`，规则版本由响应记录为 `rules-v1.0.0`。一次请求只能包含一个批次，记录 ID 不得重复，所有时间必须携带时区；未知字段和不符合契约的输入返回 HTTP 422。

M3 审核参数为：

```json
{
  "duplicate_window_seconds": 30,
  "chatter_window_seconds": 60,
  "chatter_min_count": 4,
  "short_lived_seconds": 10,
  "persistent_requires_ack": true,
  "chain_window_seconds": 60,
  "chain_min_steps": 5
}
```

规则结果是确定性建议：

- 主噪声类型优先级为 `DUPLICATE > CHATTER > SHORT_LIVED > PERSISTENT > NORMAL`；同位号的重复核心值由描述、优先级、状态、当前值和阈值组成，窗口内匹配记录的两端都标为重复；抖动要求同位号在窗口内达到频次阈值且状态连续交替。
- `DUPLICATE`、`CHATTER`、`SHORT_LIVED` 映射为 `NUISANCE`，`PERSISTENT` 映射为 `ACTIONABLE`，`NORMAL` 映射为 `STANDARD`。
- 原因类别按维护、仪表、设备、工艺的可解释文本规则以及设备跳停/工艺级联序列建议；证据不足时保留 `UNKNOWN`。
- 关联事件链只识别同类步骤 `1..5` 在时间窗内的连续序列。链 ID 由规则版本和有序成员 ID 计算，同一请求重复运行结果相同。

响应回传运行 ID、契约/算法/规则版本及参数，并返回 `record_results`、`event_chains`、`summary` 和 `errors`。`summary` 同时给出输入、成功、失败数量和完整分类计数。

## 配置

| 环境变量 | 默认值 | 约束 |
|---|---|---|
| `ALGORITHM_HOST` | `127.0.0.1` | 非空；Docker 阶段可明确设置为容器监听地址 |
| `ALGORITHM_PORT` | `8001` | 1–65535 的整数 |

配置在启动 Uvicorn 前校验。错误配置会向标准错误输出中文原因并以退出码 2 结束，不会启动一个假健康服务。

## 当前限制

- 仅负责同步纯计算，不负责数据库访问、分析任务状态、人工处置或报告。
- 未获得真实工业数据，本组件不声明准确率、性能或行业合规性。
- Windows 自包含可执行文件由 M6 工程交付阶段基于该入口构建，本阶段不提交二进制运行时。
