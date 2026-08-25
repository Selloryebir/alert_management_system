# Java 后端

本模块是报警管理系统灾后重建 Demo 的 Java 主系统，使用 Java 21、Spring Boot 3.5.16、PostgreSQL JDBC 和 Flyway。当前提供聚合健康接口，以及 CSV、制表符 TXT、XLSX 的两阶段报警文件导入。

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
| `ALGORITHM_CONNECT_TIMEOUT` | `500ms` | 算法连接超时 |
| `ALGORITHM_REQUEST_TIMEOUT` | `1s` | 算法请求总超时 |
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
GET  /api/v1/imports/{batchId}
```

`preview` 使用 `multipart/form-data`，必填字段 `file`，可选字段 `mapping`。`mapping` 是“目标字段到源表头”的 JSON 对象，例如 `{"event_time":"报警时间","tag":"位号"}`。系统支持 UTF-8（含 BOM）及 GB18030 CSV、制表符 TXT，并读取 XLSX 的首个可见工作表；公式仅作为原始文本读取，不执行。

预览会校验全文件并把批次置为 `READY` 或 `REJECTED`。错误包含 `source_row`、`field`、稳定错误码和中文说明。只有 `READY` 批次可确认；确认在单一 PostgreSQL 事务中把全部暂存记录写入 `alarm_record`。重复确认返回 HTTP 409，不会重复写入。

文件大小默认上限为 50 MB，可通过 `IMPORT_MAX_FILE_SIZE` 和 `IMPORT_MAX_REQUEST_SIZE` 调整。集成测试使用真实嵌入式 PostgreSQL，首次运行会下载对应平台测试二进制。
