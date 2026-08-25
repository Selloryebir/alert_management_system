# 项目化业务契约（M10）

本文件冻结 M10 的共享数据与 HTTP 契约，落实 PRD-032。Java/PostgreSQL 是项目事实源；Vue 只通过这些接口完成业务操作。既有 `/api/v1` 路径保持兼容，但浏览器必须显式传递当前项目。

## 项目模型

项目字段为 `project_id`、`code`、`name`、`client_name`、`site`、`unit_name`、`status`、`report_title`、`report_fields`、`created_at`、`updated_at`。`status` 仅为 `ACTIVE`（使用中）或 `ARCHIVED`（已归档）。项目编号全局唯一；名称、编号、客户、厂区、装置均为业务文本，不接受空白名称/编号。

迁移为已有数据建立只读含义明确的“默认演示项目”，并把既有批次关联到它。新数据必须有 `project_id`；批次、报警、分析、处置和报告通过批次关系继承项目作用域，不复制第二套租户字段。

项目接口：

- `GET /api/v1/projects?q=&include_archived=`：检索项目；默认只返回使用中项目。
- `POST /api/v1/projects`：创建项目。
- `GET /api/v1/projects/{projectId}`：读取项目和真实统计。
- `PATCH /api/v1/projects/{projectId}`：修改基础资料、报告抬头和有限报告字段。
- `POST /api/v1/projects/{projectId}/archive`、`.../restore`：归档和恢复；归档项目只读，不能新增导入或启动分析。
- `GET /api/v1/projects/{projectId}/export`：导出 UTF-8 JSON 项目清单与统计，不冒充完整数据库备份。
- `DELETE /api/v1/projects/{projectId}`：仅允许删除已归档且从未产生业务数据的项目；有数据时明确拒绝，避免事实被物理抹除。
- `GET /api/v1/projects/{projectId}/overview`：返回批次数、报警数、有效/作废数、待处置数及最近任务。

项目写操作均记录统一审计事件。M11 再增加登录主体和授权，不在 M10 伪造权限。

## 项目作用域接口

- `POST /api/v1/imports/preview` multipart 增加必填 `project_id`；响应增加 `project_id`。
- `GET /api/v1/imports` 增加必填 `project_id`，只返回该项目批次。
- `GET/POST /api/v1/imports/{batchId}/...` 通过批次确定项目；不存在跨项目枚举入口。
- 报告从分析运行反查项目，使用项目报告抬头和 `report_fields`。允许字段固定为 `summary`、`priority`、`area`、`unit`、`noise`、`cause`、`disposition`、`chains`；不提供任意模板语言。

字段映射继续使用稳定的“目标字段到源表头”对象传输，但页面用中文字段和下拉框生成，业务人员不接触 JSON。首次预览即使因缺少映射失败，也必须返回源表头、已识别映射和逐行中文错误，供页面重新映射后再次预览。

项目级校验规则仅支持业务可解释且当前导入器可执行的两类：附加必填字段，以及 `value`/`threshold` 的最小值和最大值。规则在预览阶段执行并返回规则名称；不建设表达式平台。

## 报警补录、修订与任务

- `POST /api/v1/projects/{projectId}/manual-alarms`：按规范化字段补录单条报警，建立来源为 `MANUAL_ENTRY` 的已导入批次，保留原值并可直接发起分析。
- `PATCH /api/v1/projects/{projectId}/manual-alarms/{recordId}`：只允许修订人工补录且尚未分析的记录；保存修订前后值和理由。
- `POST /api/v1/projects/{projectId}/manual-alarms/{recordId}/invalidate`：受控作废，要求操作者和理由，不物理删除；已作废记录不进入新分析。
- 报警责任人保存在处置记录中；列表支持 `assignee` 筛选。未分配、处理中和待关闭报警构成项目待办，不引入通用工作流引擎或外部通知。

所有状态、枚举和错误在 API 中保持稳定机器值，在页面以中文词典显示。专有名词 PostgreSQL、hybrid-v2、PDF、XLSX 可保留原文。

## 首次使用路径

页面固定呈现六步引导：创建或选择项目 → 选择样例文件 → 图形化映射并预览 → 确认导入 → 启动分析并查看看板 → 处置报警并下载报告。每步依据真实项目/批次/运行状态自动标记完成，不使用写死的演示结果。

M10 验收至少证明两个项目的数据与报告互不可见、归档项目拒绝写入、映射页面没有 JSON 编辑框、机器枚举均有中文显示，并由首次使用引导完成一次真实样例闭环。
