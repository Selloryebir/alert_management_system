# Java 后端

本模块是报警管理系统灾后重建 Demo 的 Java 主系统，使用 Java 21、Spring Boot 3.5.16、PostgreSQL JDBC 和 Flyway。当前提供聚合健康、两阶段报警文件导入，以及调用 Python 服务的同步分析编排。

## 构建与测试

仓库自带固定 Maven 3.9.16 的 Wrapper，不依赖系统安装 Maven。在仓库根目录执行：

```powershell
.\mvnw.cmd -f src/backend/pom.xml test
.\mvnw.cmd -f src/backend/pom.xml package
```

Linux 或 WSL 使用 `./mvnw`。构建时如果 `src/frontend/dist/` 存在，其内容会复制到 JAR 的 `static/`；该生成目录不提交 Git。目录不存在时仍可独立构建后端。

## 运行

运行前需要可连接的 PostgreSQL 17 实例和目标数据库。默认连接为 `127.0.0.1:5432/alert_management`，首次启动由 Flyway 创建迁移验证表和导入业务表。

```powershell
.\mvnw.cmd -f src/backend/pom.xml spring-boot:run
```

默认监听 `http://127.0.0.1:8080`，聚合健康接口为：

```text
GET http://127.0.0.1:8080/api/v1/health
```

响应固定包含 `system`、`database`、`algorithm` 三项。依赖运行中断时接口仍返回 HTTP 200，总状态为 `DEGRADED`，对应组件为 `DOWN` 并附简短说明。数据库在启动迁移阶段不可用或迁移非法时，应用会直接启动失败，避免把迁移失败隐藏成可用状态。

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SERVER_PORT` | `8080` | HTTP 端口 |
| `DB_URL` | `jdbc:postgresql://127.0.0.1:5432/alert_management` | JDBC 地址 |
| `DB_USERNAME` | `alert_management` | 数据库用户 |
| `DB_PASSWORD` | `alert_management` | 数据库口令 |
| `DB_CONNECTION_TIMEOUT_MS` | `1000` | 数据库连接超时毫秒数 |
| `IMPORT_MAX_FILE_SIZE` | `50MB` | 单个导入文件上限 |
| `IMPORT_MAX_REQUEST_SIZE` | `50MB` | 单次 multipart 请求上限 |
| `ALGORITHM_HEALTH_URL` | `http://127.0.0.1:8001/health` | Python 健康地址 |
| `ALGORITHM_ANALYSIS_URL` | `http://127.0.0.1:8001/api/v1/analyze` | Python 分析地址 |
| `ALGORITHM_CONNECT_TIMEOUT` | `500ms` | 算法连接超时 |
| `ALGORITHM_REQUEST_TIMEOUT` | `1s` | 算法请求总超时 |
| `ALGORITHM_ANALYSIS_TIMEOUT` | `60s` | 单次同步分析独立超时 |
| `ALGORITHM_SERVICE` | `algorithm-service` | 预期算法服务身份 |
| `ALGORITHM_VERSION` | `0.1.0` | 预期算法服务版本 |
| `ALGORITHM_CONTRACT_VERSION` | `v1` | 预期算法健康契约版本 |
| `APP_SERVICE_NAME` | `alert-management-backend` | 健康响应服务名 |
| `APP_VERSION` | `0.1.0` | 健康响应版本 |
| `APP_IDENTITY` | `2026 年灾后重建 Demo` | 审核身份标识 |

算法服务默认监听 `127.0.0.1:8001`。其 `/health` 必须返回 2xx，且 `status`、`service`、`version`、`contract_version` 与配置完全匹配，否则算法组件状态为 `DOWN`。

## 两阶段文件导入

```text
POST /api/v1/imports/preview
POST /api/v1/imports/{batchId}/confirm
GET  /api/v1/imports?limit=20
GET  /api/v1/imports/{batchId}
GET  /api/v1/imports/{batchId}/records?page=0&size=20
```

`preview` 使用 `multipart/form-data`，必填字段 `file`，可选字段 `mapping`。`mapping` 是“目标字段到源表头”的 JSON 对象，例如 `{"event_time":"报警时间","tag":"位号"}`。系统支持 UTF-8（含 BOM）及 GB18030 CSV、制表符 TXT，并读取 XLSX 的首个可见工作表；公式仅作为原始文本读取，不执行。

预览会校验全文件并把批次置为 `READY` 或 `REJECTED`。错误包含 `source_row`、`field`、稳定错误码和中文说明。只有 `READY` 批次可确认；确认在单一 PostgreSQL 事务中把全部暂存记录写入 `alarm_record`。重复确认返回 HTTP 409，不会重复写入。

批次列表 `limit` 默认为 20、最大为 100。记录追溯接口按 `source_row` 排序，响应为 `{items,total,page,size}`；`page` 从 0 开始，`size` 默认为 20、最大为 200，`items` 同时包含规范化字段和 `raw_payload`。

业务 API 的 HTTP 失败响应包含稳定的 `code`、中文 `message` 和 `trace_id`；例如重复确认使用 `IMPORT_STATUS_CONFLICT`，不得把英文框架错误直接交给界面。

文件大小默认上限为 50 MB，可通过 `IMPORT_MAX_FILE_SIZE` 和 `IMPORT_MAX_REQUEST_SIZE` 调整。集成测试使用真实嵌入式 PostgreSQL，首次运行会下载对应平台测试二进制。

## 同步分析

```text
POST /api/v1/imports/{batchId}/analyses
GET  /api/v1/analyses/{runId}
```

只有 `IMPORTED` 批次可首次分析；算法调用失败后批次为 `FAILED`，可通过同一 POST 明确重试。调用期间批次为 `ANALYZING`，成功后为 `COMPLETED`；其余状态返回 HTTP 409，已成功批次不会生成重复运行或结果。

Java 按 `source_row` 向 Python 发送全部已导入记录、固定 v1 版本和显式规则参数。Python 的运行 ID、契约/算法/规则版本、规则参数、逐记录唯一全覆盖、摘要计数及事件链成员归属和时间顺序均须通过校验。HTTP 错误、超时、非法 JSON 或契约漂移会保存中文可重试原因，并保证逐记录结果和事件链为零；成功结果、事件链和完成状态在一个 PostgreSQL 事务中提交。查询响应中的逐记录结果带 `source_row`，事件链成员带 `record_id`、`source_row` 和从 0 开始的 `order`，便于审查追溯。

## 看板、报警详情与处置

```text
GET   /api/v1/imports/{batchId}/analyses/latest
GET   /api/v1/analyses/{runId}/dashboard
GET   /api/v1/analyses/{runId}/alarms?page=0&size=50
GET   /api/v1/analyses/{runId}/alarms/{recordId}
PATCH /api/v1/analyses/{runId}/alarms/{recordId}/disposition
```

看板、列表、详情和处置仅接受 `COMPLETED` 分析运行，统计与筛选直接查询 PostgreSQL。报警列表可按 `priority`、`area`、`unit`、`noise_type`、`cause_category` 和 `disposition_status` 筛选，`unit=未指定单元` 用于筛选空单元。详情包含原始行、算法证据、相关事件链及完整处置历史。

处置请求为 `{"status":"IN_PROGRESS","operator":"值班员","note":"开始核查"}`。合法流转为 `OPEN → IN_PROGRESS`、`IN_PROGRESS → OPEN/CLOSED`、`CLOSED → IN_PROGRESS`；每次变更在同一事务中更新当前态并追加历史。关闭保存 `closed_at`，重新打开时清空。
