# M5 报告、审计和演示复位实施约束

本文件冻结 M5 的最小实现边界，供主智能体、后端组、前端组和测试组共享。需求依据为 PRD-001、PRD-014、PRD-016、PRD-018 及 G5；若实现与本文冲突，以产品需求、数据契约和业务审计策略为先。

## 1. 交付闭环

审核员应能在当前单页界面完成：人工修订一个分析结论、查询关键动作审计、下载整次已完成分析的 PDF/XLSX 报告、明确确认后复位本项目演示数据。开发脚本另提供一次 PostgreSQL 备份入口。

不实现筛选报告、报告历史库、异步任务、权限系统、消息队列、通用工作流、审计修改/删除或选择性复位。

## 2. 数据与事务

- Flyway V5 只新增 `analysis_result_override` 和 `audit_event`。原始 `analysis_result` 不可变；看板、列表、详情和报告以 `COALESCE(override, algorithm result)` 作为当前有效结论。
- override 保存 `noise_type`、`alarm_class`、`cause_category`、操作者、理由和更新时间；历史原值/新值写入统一审计，不再建重复历史表。
- audit 保存事件 UUID、类型、时间、操作者、目标类型/标识、结果、`trace_id` 和 JSON 详情。业务变更与成功审计在同一 Java 事务内提交。
- 事件类型固定为 `IMPORT_CREATED`、`IMPORT_REJECTED`、`IMPORT_CONFIRMED`、`ANALYSIS_STARTED`、`ANALYSIS_COMPLETED`、`ANALYSIS_FAILED`、`RESULT_OVERRIDDEN`、`DISPOSITION_CHANGED`、`REPORT_EXPORTED`。
- 自动动作使用页面明确展示的本地演示身份 `demo-reviewer`；人工修订、处置和报告使用请求中的非空操作者。
- 复位在一个事务内显式清空本项目业务表，不使用 `CASCADE` 或动态发现，不触碰 `app_metadata`、`flyway_schema_history`、项目文件和其他数据库表。存在 `ANALYZING` 运行时拒绝复位。复位后的业务表与审计表为空。

## 3. HTTP 契约

人工分类修订：

```http
PATCH /api/v1/analyses/{runId}/alarms/{recordId}/classification
Content-Type: application/json

{"noise_type":"CHATTER","alarm_class":"NUISANCE","cause_category":"INSTRUMENT_ISSUE","operator":"审核员A","reason":"根据事件序列复核"}
```

三个分类字段、操作者和理由均必填；枚举与 v1 算法契约一致；无实际变化返回 409。详情同时返回算法原值、当前有效值及 override 元数据，事件链仍只是关联建议。

报告：

```http
POST /api/v1/analyses/{runId}/reports/pdf
POST /api/v1/analyses/{runId}/reports/xlsx
Content-Type: application/json

{"operator":"审核员A"}
```

只接受 `COMPLETED` 运行并导出整次分析。成功响应为附件；PDF 包含重建 Demo/合成数据标识、运行与版本、总量及全部看板统计和事件链摘要；XLSX 包含概要、报警明细、事件链、处置历史，明细保留算法原值和当前有效值。完整生成成功后才写 `REPORT_EXPORTED`。

审计查询：

```http
GET /api/v1/audit-events?page=0&size=50&event_type=&target_type=&target_id=
```

按时间倒序返回固定字段分页结果；API 和页面只读。复位：

```http
POST /api/v1/demo/reset
Content-Type: application/json

{"operator":"demo-reviewer","confirmation":"RESET_DEMO"}
```

成功响应包含完成时间、各业务表删除数量和 `business_state: "EMPTY"`；确认值错误、分析进行中或事务失败不得清空数据或显示成功。

## 4. 用户界面

保持 Vue 单页和现有组件边界。增加报告下载、分类修订、审计分页/类型筛选和危险操作区；不增加路由、状态库、图表库或下载库。下载使用浏览器 Blob，复位成功统一清除旧批次、运行、看板、列表和详情状态。报告、审计、复位各自显示独立的加载、空状态和可行动中文失败信息。

页面持续显示“2026 年灾后重建 Demo”“仅使用合成数据”和“本地演示身份 demo-reviewer”，不得出现合规认证、真实准确率或已确认根因措辞。

## 5. G5 验收

- 后端测试用 PDFBox/POI 重新打开文件并逐项对账看板或固定 SQL；中文标识可提取，XLSX 明细首尾行、override 和处置历史正确。
- 真实 Chromium 完成 300 行导入、分析、修订、处置、两种下载和审计查询；再复位并完整重跑两次，去除 UUID/时间后的摘要一致。
- 固定种子 20,000 行至少生成一次两类报告，记录耗时、文件大小和可打开结果，不预设工业性能承诺。
- 强制报告/复位失败时不假成功；复位前后的外部数据库哨兵和项目外文件不变。
- `scripts/dev/test.sh` 与 `scripts/dev/m5-report-audit-reset-smoke.sh` 是正式阶段命令；固定候选还需通过差异白名单、独立审查和远端 CI。

## 6. 目录所有权

- 后端组独占 `src/backend/**`：V5、审计接入、override、报告、复位及 PostgreSQL 测试。
- 前端组独占 `src/frontend/**`：修订、下载、审计和复位用户路径及组件测试。
- 测试/工程组独占 `tests/e2e/**`、`scripts/dev/**` 和应用 CI：真实浏览器、备份、双复位和 20,000 行报告门槛。
- 主智能体独占公共规划、自动化状态、集成提交、推送和阶段证据；公共契约冻结后不得由专业组自行改义。
