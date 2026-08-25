# 报警数据契约 v1

## 1. 设计目标

外部文件字段可能因厂商而异，系统通过“导入字段映射 → 统一内部模型”隔离差异。v1 仅处理离线文件；不定义 OPC 或控制指令。

时间统一按审核环境时区 `Asia/Shanghai` 解释无时区输入，进入系统后保存为带时区时间。原始单元格文本和原始行号保留用于追溯，业务统计使用规范化字段。

## 2. 导入文件

支持：

- `.csv`：带表头，自动识别 UTF-8（含 BOM）；演示包还应提供 GB18030 样例验证中文导入。
- `.txt`：带表头，字段以制表符分隔。
- `.xlsx`：读取第一个可见工作表，第一行为表头，不执行公式和宏。

导入采用两阶段流程：先预览、映射和全文件校验，再确认落库。文件存在阻断错误时整批不落业务记录；警告可由用户确认后继续。不得悄悄丢行或部分成功。

## 3. 规范化报警记录

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `record_id` | UUID | 系统生成 | 规范化记录标识 |
| `batch_id` | UUID | 系统生成 | 所属导入批次 |
| `source_row` | 正整数 | 是 | 含表头时首条数据通常为 2 |
| `event_time` | timestamptz | 是 | 报警发生时间 |
| `return_time` | timestamptz | 否 | 恢复时间，不得早于发生时间 |
| `ack_time` | timestamptz | 否 | 确认时间，不得早于发生时间 |
| `site` | 字符串(100) | 是 | 厂区或演示地点 |
| `area` | 字符串(100) | 是 | 装置/区域 |
| `unit` | 字符串(100) | 否 | 工艺单元；缺失时显式为空 |
| `tag` | 字符串(120) | 是 | 报警位号；去除首尾空白但保留大小写 |
| `description` | 字符串(500) | 是 | 报警描述 |
| `priority` | 枚举 | 是 | `P1`、`P2`、`P3`、`P4`，P1 最紧急 |
| `state` | 枚举 | 是 | `ACTIVE`、`RETURNED`、`ACKNOWLEDGED` |
| `value` | decimal | 否 | 当时值；无法安全转数字时报错而非置零 |
| `threshold` | decimal | 否 | 报警阈值 |
| `engineering_unit` | 字符串(40) | 否 | 工程单位 |
| `source_system` | 字符串(100) | 是 | 合成样例固定为 `SYNTHETIC_DCS` |
| `operator` | 字符串(100) | 否 | 文件给出的操作员标识，不等同登录用户 |
| `raw_payload` | JSON | 是 | 原始列名到原始文本的映射 |

默认字段别名只服务于样例和常见中文表头；未知列由用户映射，不在代码中猜测厂商语义。空字符串按缺失处理。每个阻断错误至少返回 `source_row`、目标字段、错误码和中文消息。

推荐稳定错误码：`MISSING_HEADER`、`REQUIRED_VALUE_MISSING`、`INVALID_TIME`、`INVALID_ENUM`、`INVALID_NUMBER`、`TIME_ORDER_INVALID`、`DUPLICATE_SOURCE_ROW`。

## 4. 批次与处理状态

导入批次状态：

```text
UPLOADED -> VALIDATING -> READY -> IMPORTED -> ANALYZING -> COMPLETED
                    \-> REJECTED               \-> FAILED
```

- 只有 `READY` 可被确认导入。
- 只有 `IMPORTED` 可开始分析。
- `FAILED` 保留失败原因，可从明确步骤重试；不得显示为完成。
- 数据复位是演示维护动作，不使用业务状态伪装删除。

## 5. Java 与 Python 的算法接口

接口采用版本化 HTTP JSON，v1 最小端点：

- `GET /health`：进程存活和算法版本。
- `POST /api/v1/analyze`：接收单批规范化记录和规则参数，返回逐记录标签、事件链和运行摘要。

请求必须包含 `analysis_run_id`、`contract_version: "v1"`、`algorithm_version`、`records` 和显式规则参数。响应必须回传相同运行标识，并包含：

- `record_results[]`：`record_id`、`noise_type`、`alarm_class`、`cause_category`、`score`、`evidence[]`；
- `event_chains[]`：链标识、成员记录、起止时间、关联规则和说明；
- `summary`：输入数、成功数、失败数、各类计数；
- `errors[]`：记录标识、稳定错误码和中文消息。

`noise_type` v1 允许值为 `NORMAL`、`DUPLICATE`、`CHATTER`、`SHORT_LIVED`、`PERSISTENT`；一条记录只保存一个主要类型，其他命中放入 `evidence`。`cause_category` v1 使用 `PROCESS_DISTURBANCE`、`EQUIPMENT_FAULT`、`INSTRUMENT_ISSUE`、`MAINTENANCE_TEST`、`UNKNOWN`。`UNKNOWN` 是有效结果，禁止为提高符合率强行分类。

Java 对数据库和业务状态拥有最终写权限。Python 不连接 PostgreSQL，不改变处置状态，不生成审计主体。超时、非 2xx、响应版本不符或记录数不一致时，Java 将本次分析标为失败并展示可重试原因，不伪造兜底成功。

## 6. 事件链与处置

事件链至少记录：规则版本、时间窗口、成员及顺序、作为起点的记录、关联说明。界面和报告统一称“关联事件链”，不得显示“已确认根因”。

处置状态固定为：

```text
OPEN -> IN_PROGRESS -> CLOSED
```

允许 `IN_PROGRESS -> OPEN` 退回；关闭必须填写处置说明。每次流转保留操作者、时间、原状态、新状态和备注。

## 7. 合成数据集

所有样例包含清晰的 `SYNTHETIC` 标识且不使用真实厂名、人名或生产位号。

| 数据集 | 规模 | 形式 | 用途 |
|---|---:|---|---|
| `smoke` | 每种格式 300 行 | CSV/TXT/XLSX 表达同一逻辑数据 | 格式等价、日常冒烟 |
| `demo` | 20,000 行 | CSV 为主，可由固定种子重建其他格式 | 正式演示、看板和报告 |
| `invalid` | 30–50 行 | 按错误场景分文件 | 校验和错误提示 |
| `generated` | 参数化 100,000/1,000,000 行 | 运行时生成，不提交大文件 | 非承诺性容量观察 |

固定随机种子和生成器版本共同决定数据。场景至少覆盖正常报警洪泛、重复记录、抖动、短时恢复、长期未恢复、仪表漂移、设备跳停序列、工艺扰动级联和维护测试。预期结果由场景声明独立生成或人工复核，测试不得用待测算法本身生成“正确答案”。

合成场景符合率计算为“符合场景声明的已检查项 / 场景声明检查项总数”。该指标仅验证实现与样例设计一致，不代表真实工业准确率。
